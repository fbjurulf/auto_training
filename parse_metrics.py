"""
Tolkar HAE:s Health Metrics-payloads till daily_metric.

HAE levererar flera mätpunkter per dag för de flesta metrics. Eftersom
daily_metric har PRIMARY KEY (local_date, metric) måste punkterna
aggregeras per dag INNAN de skrivs - annars skriver varje punkt över
den föregående och den sista vinner.

Aggregeringsregeln är per mätvärde, se AGG nedan.

Idempotent: en omsändning räknar om och uppdaterar raden.
"""

import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("TRANING_DB", str(BASE_DIR / "traning.db"))
LOG_PATH = os.environ.get("TRANING_METRICS_LOG", str(BASE_DIR / "logs" / "metrics.log"))

Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("metrics")
logger.setLevel(logging.INFO)
_h = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3)
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_h)
logger.addHandler(logging.StreamHandler(sys.stdout))

SOURCE_CODE = "apple_health_metrics"

# Hur dagens mätpunkter slås ihop till ett värde.
#   sum   - kumulativa värden (steg)
#   avg   - momentana mätningar där dagssnittet är det intressanta
#   last  - estimat som skrivs om, inte mäts (VO2max)
#   sleep - egen hantering, se aggregate_sleep
AGG = {
    "step_count": "sum",
    "heart_rate_variability": "avg",
    "resting_heart_rate": "avg",
    "vo2_max": "last",
    "sleep_analysis": "sleep",
}
DEFAULT_AGG = "avg"

SLEEP_PHASES = ["core", "deep", "rem", "awake", "inBed"]


def local_date(s):
    """'2026-09-01 00:00:00 +0200' -> '2026-09-01'"""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z").strftime("%Y-%m-%d")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def aggregate_sleep(points):
    """
    Summerar nattens sömn. Flera poster per datum förekommer när
    Health delat upp sömnen eller när en tupplur registrerats.
    Faserna summeras, tiderna spänner från tidigaste start till
    senaste slut.
    """
    total = sum(p.get("totalSleep") or 0 for p in points)
    extra = {ph: round(sum(p.get(ph) or 0 for p in points), 3)
             for ph in SLEEP_PHASES
             if any(p.get(ph) is not None for p in points)}

    starts = [p["sleepStart"] for p in points if p.get("sleepStart")]
    ends = [p["sleepEnd"] for p in points if p.get("sleepEnd")]
    if starts:
        extra["sleepStart"] = min(starts)
    if ends:
        extra["sleepEnd"] = max(ends)
    extra["poster"] = len(points)

    return total, json.dumps(extra)


def value_of(point):
    """Enkla metrics har qty; vissa levereras som Avg/Min/Max."""
    v = point.get("qty")
    return point.get("Avg") if v is None else v


def aggregate(name, points):
    """Ger (qty, extra_json) för en dags mätpunkter av ett mätvärde."""
    rule = AGG.get(name, DEFAULT_AGG)

    if rule == "sleep":
        return aggregate_sleep(points)

    values = [v for v in (value_of(p) for p in points) if v is not None]
    if not values:
        return None, None

    if rule == "sum":
        qty = sum(values)
    elif rule == "last":
        qty = values[-1]
    else:
        qty = sum(values) / len(values)

    extra = {"punkter": len(values)}
    if len(values) > 1:
        extra["min"] = round(min(values), 2)
        extra["max"] = round(max(values), 2)
    return qty, json.dumps(extra)


def main():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row

    src = conn.execute("SELECT id FROM source WHERE code=?", (SOURCE_CODE,)).fetchone()
    if src is None:
        logger.error("Källan %s saknas i source-tabellen", SOURCE_CODE)
        return
    src_id = src["id"]

    run_id = conn.execute(
        """INSERT INTO import_run (source_id, run_type, started_at)
           VALUES (?, 'push', ?)""", (src_id, utc_now())).lastrowid

    rows = conn.execute(
        """SELECT id, payload FROM raw_import
           WHERE source_id=? AND status IN ('received','partial')
           ORDER BY id""",
        (src_id,)).fetchall()

    # (datum, metric) -> {"units": ..., "points": [...]}
    buckets = defaultdict(lambda: {"units": None, "points": []})

    for row in rows:
        try:
            metrics = json.loads(row["payload"])["data"]["metrics"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            conn.execute("UPDATE raw_import SET status='failed', error=? WHERE id=?",
                         (str(exc), row["id"]))
            logger.error("raw_import %s gick inte att tolka: %s", row["id"], exc)
            continue

        for metric in metrics:
            name = metric.get("name")
            data = metric.get("data")
            if not name or not isinstance(data, list):
                continue
            for point in data:
                if not isinstance(point, dict) or "date" not in point:
                    continue
                try:
                    d = local_date(point["date"])
                except ValueError:
                    continue
                b = buckets[(d, name)]
                b["units"] = metric.get("units")
                b["points"].append(point)

        conn.execute("UPDATE raw_import SET status='parsed' WHERE id=?", (row["id"],))

    written = 0
    for (d, name), b in buckets.items():
        qty, extra = aggregate(name, b["points"])
        if qty is None:
            continue
        conn.execute(
            """INSERT INTO daily_metric
               (local_date, metric, qty, units, extra, source_id, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(local_date, metric) DO UPDATE SET
                 qty=excluded.qty, units=excluded.units,
                 extra=excluded.extra, updated_at=excluded.updated_at""",
            (d, name, qty, b["units"], extra, src_id, utc_now()))
        written += 1

    conn.execute(
        """UPDATE import_run SET finished_at=?, status='ok', new_count=? WHERE id=?""",
        (utc_now(), written, run_id))
    conn.commit()

    logger.info("Tolkade %s payloads -> %s dagsvärden", len(rows), written)
    conn.close()


if __name__ == "__main__":
    main()
