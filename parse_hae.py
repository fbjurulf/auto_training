"""
Tolkar mottagna HAE-payloads från raw_import till workout-tabellerna.

Idempotent: kan köras om hur många gånger som helst mot samma rådata.
Körs efter varje mottagning och som schemalagd säkerhetsnät-körning.
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import hojd
from matchning import find_workout, record_match

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("TRANING_DB", str(BASE_DIR / "traning.db"))
LOG_PATH = os.environ.get("TRANING_PARSE_LOG", str(BASE_DIR / "logs" / "parse.log"))
KJ_PER_KCAL = 4.184
SWIM_REST_THRESHOLD_M = 5.0   # minuthink med mindre distans än så = vila

Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("parse")
logger.setLevel(logging.INFO)
_h = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=5)
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_h)
logger.addHandler(logging.StreamHandler(sys.stdout))


# ---------- hjälpare för HAE:s inkonsekventa struktur ----------

def qty(node, default=None):
    """HAE packar de flesta värden som {'qty': x, 'units': y}."""
    if node is None:
        return default
    if isinstance(node, dict):
        return node.get("qty", default)
    if isinstance(node, (int, float)):
        return node
    return default


def distance_m(node):
    """Distans är det enda fält där units-strängen går att lita på."""
    if not isinstance(node, dict):
        return None
    val = node.get("qty")
    if val is None:
        return None
    return val * 1000.0 if node.get("units") == "km" else val


def kcal(node):
    v = qty(node)
    return None if v is None else v / KJ_PER_KCAL


def parse_dt(s):
    """'2026-09-10 17:56:06 +0200' -> aware datetime."""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")


def to_utc_str(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hr_of(w, key):
    hr = w.get("heartRate") or {}
    v = qty(hr.get(key))
    if v is None:
        v = qty(w.get("avgHeartRate" if key == "avg" else "maxHeartRate"))
    return None if v is None else round(v)


def series(w, key):
    """Tidsserie som lista av (datetime, qty), sorterad."""
    raw = w.get(key)
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        try:
            out.append((parse_dt(item["date"]), float(item.get("qty", 0))))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda t: t[0])


def hr_series(w):
    raw = w.get("heartRateData")
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        try:
            out.append((parse_dt(item["date"]), float(item.get("Avg", 0)),
                        float(item.get("Max", 0))))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda t: t[0])


def hr_between(hrs, t0, t1):
    vals = [(a, m) for (d, a, m) in hrs if t0 <= d <= t1]
    if not vals:
        return None, None
    return round(sum(a for a, _ in vals) / len(vals)), round(max(m for _, m in vals))


# ---------- splits och set ----------

def run_splits(w, start_dt):
    """
    Interpolerar km-splits ur minutserien walkingAndRunningDistance.
    Varje element är distans (km) under den hinken, inte kumulativt.
    """
    pts = series(w, "walkingAndRunningDistance")
    if not pts:
        return []
    hrs = hr_series(w)

    splits = []
    cum_km = 0.0
    km_mark = 1.0
    prev_t = start_dt
    split_start = start_dt

    for t, km in pts:
        bucket_start = max(prev_t, split_start)
        bucket_s = (t - bucket_start).total_seconds()
        new_cum = cum_km + km

        # En hink kan passera flera kilometergränser vid hög fart.
        while new_cum >= km_mark and km > 0:
            andel = (km_mark - cum_km) / km
            cross = bucket_start.timestamp() + bucket_s * andel
            cross_dt = datetime.fromtimestamp(cross, tz=t.tzinfo)
            dur = (cross_dt - split_start).total_seconds()
            a, m = hr_between(hrs, split_start, cross_dt)
            if dur > 0:
                splits.append({
                    "seq": len(splits) + 1, "kind": "split",
                    "started_at": to_utc_str(split_start),
                    "duration_s": round(dur, 1), "distance_m": 1000.0,
                    "hr_avg": a, "hr_max": m,
                    "pace_s_per_km": round(dur, 1),
                    "speed_kmh": round(3600.0 / dur, 2),
                })
            split_start = cross_dt
            cum_km = km_mark
            km_mark += 1.0

        cum_km = new_cum
        prev_t = t

    # Sista, ofullständiga kilometern
    rest_km = cum_km - (km_mark - 1.0)
    if rest_km > 0.05:
        dur = (prev_t - split_start).total_seconds()
        if dur > 0:
            a, m = hr_between(hrs, split_start, prev_t)
            splits.append({
                "seq": len(splits) + 1, "kind": "split",
                "started_at": to_utc_str(split_start),
                "duration_s": round(dur, 1),
                "distance_m": round(rest_km * 1000, 1),
                "hr_avg": a, "hr_max": m,
                "pace_s_per_km": round(dur / rest_km, 1),
                "speed_kmh": round(rest_km / (dur / 3600.0), 2),
            })
    return splits


def swim_sets(w):
    """
    Grupperar minuthinkar i simset. Minuter med försumbar distans
    behandlas som vila. Upplösningen är en minut - enskilda längder
    går inte att särskilja i HAE:s data.
    """
    dist = series(w, "swimDistance")
    if not dist:
        return [], 0.0
    strokes = dict(series(w, "swimStroke"))
    hrs = hr_series(w)

    segs, rest_s = [], 0.0
    cur = None

    def close(cur, end_t):
        if not cur or cur["distance_m"] <= 0:
            return
        dur = (end_t - cur["t0"]).total_seconds()
        if dur <= 0:
            return
        a, m = hr_between(hrs, cur["t0"], end_t)
        segs.append({
            "seq": len(segs) + 1, "kind": "interval",
            "started_at": to_utc_str(cur["t0"]),
            "duration_s": round(dur, 1),
            "distance_m": round(cur["distance_m"], 1),
            "hr_avg": a, "hr_max": m,
            "extra": json.dumps({
                "stroke_count": round(cur["strokes"]),
                "pace_s_per_100m": round(dur / (cur["distance_m"] / 100.0), 1),
            }),
        })

    for i, (t, d) in enumerate(dist):
        next_t = dist[i + 1][0] if i + 1 < len(dist) else t
        if d < SWIM_REST_THRESHOLD_M:
            close(cur, t)
            cur = None
            rest_s += (next_t - t).total_seconds()
            continue
        if cur is None:
            cur = {"t0": t, "distance_m": 0.0, "strokes": 0.0}
        cur["distance_m"] += d
        cur["strokes"] += strokes.get(t, 0.0)
    if cur:
        close(cur, dist[-1][0])

    return segs, rest_s


# ---------- persistering ----------

# Har FIT berikat passet är FIT sanningskälla: HAE fyller bara tomma fält.
_UPPDATERA = ", ".join(
    f"{k}=CASE WHEN workout.fit_enriched THEN COALESCE(workout.{k}, excluded.{k}) "
    f"ELSE excluded.{k} END"
    for k in ("raw_import_id", "sport_id", "started_at", "ended_at", "duration_s",
              "distance_m", "hr_avg", "hr_max", "kcal_active", "kcal_total"))


def upsert_workout(conn, source_id, raw_id, sport_id, w):
    start, end = parse_dt(w["start"]), parse_dt(w["end"])
    offset = int(start.utcoffset().total_seconds() // 60)
    src = w.get("source") or {}

    conn.execute(
        """INSERT INTO workout
           (source_id, external_id, raw_import_id, sport_id, started_at, ended_at,
            tz_offset_min, local_date, duration_s, elapsed_s, distance_m,
            hr_avg, hr_max, kcal_active, kcal_total, device, indoor)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(source_id, external_id) DO UPDATE SET """ + _UPPDATERA,
        (source_id, w["id"], raw_id, sport_id, to_utc_str(start), to_utc_str(end),
         offset, start.strftime("%Y-%m-%d"),
         int(round(w.get("duration") or (end - start).total_seconds())),
         int((end - start).total_seconds()), distance_m(w.get("distance")),
         hr_of(w, "avg"), hr_of(w, "max"),
         kcal(w.get("activeEnergyBurned")), kcal(w.get("totalEnergy")),
         src.get("name"), 1 if w.get("isIndoor") else 0),
    )
    return conn.execute(
        "SELECT id FROM workout WHERE source_id=? AND external_id=?",
        (source_id, w["id"]),
    ).fetchone()[0]


def save_segments(conn, workout_id, segs):
    conn.execute("DELETE FROM workout_segment WHERE workout_id=?", (workout_id,))
    for s in segs:
        conn.execute(
            """INSERT INTO workout_segment
               (workout_id, seq, kind, started_at, duration_s, distance_m,
                hr_avg, hr_max, pace_s_per_km, speed_kmh, elevation_gain_m,
                elevation_loss_m, extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (workout_id, s["seq"], s["kind"], s.get("started_at"),
             s.get("duration_s"), s.get("distance_m"), s.get("hr_avg"),
             s.get("hr_max"), s.get("pace_s_per_km"), s.get("speed_kmh"),
             s.get("upp"), s.get("ner"), s.get("extra")),
        )


def route_serie(w):
    """Höjdserien ur GPS-rutten - samma 1 Hz-serie som FIT:s records."""
    punkter = []
    for p in w.get("route") or []:
        try:
            punkter.append((parse_dt(p["timestamp"]), float(p["altitude"])))
        except (KeyError, ValueError, TypeError):
            continue
    return hojd.Hojdserie(punkter)


def save_hojd(conn, workout_id, w, serie):
    """Del B: höjdmeter upp och ner för passet, Apples värde som kontroll."""
    upp, ner = serie.totalt()
    conn.execute("UPDATE workout SET ascent_m=?, descent_m=?, ascent_apple_m=? WHERE id=?",
                 (upp, ner, qty(w.get("elevationUp")), workout_id))


def save_run(conn, workout_id, w, start_dt, serie=None):
    dist = distance_m(w.get("distance")) or 0
    dur = w.get("duration") or 0
    pace = (dur / (dist / 1000.0)) if dist > 100 else None
    # Höjdmetrarna ligger i workout.ascent_m sedan del B.
    conn.execute(
        """INSERT INTO workout_run
           (workout_id, pace_s_per_km, cadence_avg_spm)
           VALUES (?,?,?)
           ON CONFLICT(workout_id) DO UPDATE SET
             pace_s_per_km=excluded.pace_s_per_km,
             cadence_avg_spm=excluded.cadence_avg_spm""",
        (workout_id, pace, qty(w.get("stepCadence"))),
    )
    splits = run_splits(w, start_dt)
    if serie:
        for sp in splits:
            t0 = datetime.strptime(sp["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            sp["upp"], sp["ner"] = serie.inom(t0, t0 + timedelta(seconds=sp["duration_s"]))
    save_segments(conn, workout_id, splits)


def save_swim(conn, workout_id, w):
    pool = qty(w.get("lapLength"))
    dist = distance_m(w.get("distance")) or 0
    strokes = qty(w.get("totalSwimmingStrokeCount"))
    lengths = round(dist / pool) if pool else None
    segs, rest_s = swim_sets(w)

    active_s = (w.get("duration") or 0) - rest_s
    pace100 = (active_s / (dist / 100.0)) if dist > 0 and active_s > 0 else None
    spl = (strokes / lengths) if strokes and lengths else None
    # Härlett SWOLF: sekunder per längd (aktiv tid) + armtag per längd.
    swolf = (active_s / lengths + spl) if lengths and spl and active_s > 0 else None

    conn.execute(
        """INSERT INTO workout_swim
           (workout_id, pool_length_m, length_count, swolf_avg,
            stroke_count_total, strokes_per_length_avg, pace_s_per_100m, rest_s_total)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(workout_id) DO UPDATE SET
             pool_length_m=excluded.pool_length_m, length_count=excluded.length_count,
             swolf_avg=excluded.swolf_avg, stroke_count_total=excluded.stroke_count_total,
             strokes_per_length_avg=excluded.strokes_per_length_avg,
             pace_s_per_100m=excluded.pace_s_per_100m, rest_s_total=excluded.rest_s_total""",
        (workout_id, pool, lengths,
         round(swolf, 1) if swolf else None,
         round(strokes) if strokes else None,
         round(spl, 1) if spl else None,
         round(pace100, 1) if pace100 else None,
         round(rest_s)),
    )
    save_segments(conn, workout_id, segs)


# ---------- huvudflöde ----------

def main():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row

    src_id = conn.execute(
        "SELECT id FROM source WHERE code='apple_health_hae'").fetchone()["id"]
    fit_src_id = conn.execute(
        "SELECT id FROM source WHERE code='healthfit_fit'").fetchone()["id"]
    run_id = conn.execute(
        """INSERT INTO import_run (source_id, run_type, started_at)
           VALUES (?, 'push', ?)""", (src_id, to_utc_str(datetime.now(timezone.utc)))
    ).lastrowid

    aliases = {r["external_code"]: (r["sport_id"], r["code"]) for r in conn.execute(
        """SELECT a.external_code, a.sport_id, s.code
           FROM sport_alias a JOIN sport s ON s.id = a.sport_id
           WHERE a.source_id = ?""", (src_id,))}

    new = updated = skipped = 0
    unknown = set()

    rows = conn.execute(
        "SELECT id, payload FROM raw_import WHERE source_id=? AND status='received' ORDER BY id",
        (src_id,)).fetchall()

    for row in rows:
        try:
            workouts = json.loads(row["payload"])["data"]["workouts"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            conn.execute("UPDATE raw_import SET status='failed', error=? WHERE id=?",
                         (str(exc), row["id"]))
            logger.error("raw_import %s gick inte att tolka: %s", row["id"], exc)
            continue

        for w in workouts:
            name = w.get("name")
            if name not in aliases:
                unknown.add(name)
                skipped += 1
                continue
            sport_id, sport_code = aliases[name]

            # HealthFit laddar upp direkt efter passet, HAE kör på schema -
            # FIT hinner ofta först. Finns passet redan från FIT ska HAE inte
            # skapa ett andra: FIT:s data är rikare på varenda punkt. Samma
            # matchning som parse_fit (A8): UUID, annars sport och starttid -
            # en FIT-fil utan UUID har en filhash som external_id.
            match_id, hur = find_workout(conn, w["id"], to_utc_str(parse_dt(w["start"])),
                                         sport_id)
            if match_id is not None and conn.execute(
                    "SELECT source_id FROM workout WHERE id=?",
                    (match_id,)).fetchone()["source_id"] == fit_src_id:
                record_match(conn, match_id, src_id, w["id"], hur)
                if hur == "tid":
                    logger.warning("HAE-pass %s (%s) matchat på starttid mot FIT-pass %s",
                                   w["id"], name, match_id)
                skipped += 1
                continue
            befintligt = conn.execute(
                "SELECT fit_enriched FROM workout WHERE source_id=? AND external_id=?",
                (src_id, w["id"])).fetchone()
            existed = befintligt is not None
            try:
                wid = upsert_workout(conn, src_id, row["id"], sport_id, w)
                # HAE skickar överlappande fönster, så samma pass kommer om och
                # om igen. Segment och detaljer från FIT får inte raderas av
                # HAE:s minutupplösta version (A3).
                if existed and befintligt["fit_enriched"]:
                    pass
                else:
                    serie = route_serie(w)
                    save_hojd(conn, wid, w, serie)
                    if sport_code.startswith("run"):
                        save_run(conn, wid, w, parse_dt(w["start"]), serie)
                    elif sport_code.startswith("swim"):
                        save_swim(conn, wid, w)
                updated += 1 if existed else 0
                new += 0 if existed else 1
            except Exception as exc:
                logger.error("Pass %s (%s) failade: %s", w.get("id"), name, exc)
                skipped += 1

        conn.execute("UPDATE raw_import SET status='parsed' WHERE id=?", (row["id"],))

    conn.execute(
        """UPDATE import_run SET finished_at=?, status='ok',
           new_count=?, updated_count=?, skipped_count=? WHERE id=?""",
        (to_utc_str(datetime.now(timezone.utc)), new, updated, skipped, run_id))
    conn.commit()

    logger.info("Tolkade %s payloads: %s nya, %s uppdaterade, %s hoppade",
                len(rows), new, updated, skipped)
    if unknown:
        logger.warning("Okända träningsformer (lägg till i sport_alias): %s",
                       ", ".join(sorted(str(u) for u in unknown)))
    conn.close()


if __name__ == "__main__":
    main()
