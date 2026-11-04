"""Daglig kontroll av importflöde, hälsodata, Dropbox och tjänster."""

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("TRANING_DB", str(BASE_DIR / "traning.db"))
LOG_PATH = os.environ.get(
    "TRANING_WATCHDOG_LOG", str(BASE_DIR / "logs" / "watchdog.log")
)
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

RAW_IMPORT_MAX_AGE = timedelta(days=3)
# HealthFit laddar upp FIT-filen direkt efter passet. Saknas den efter ett
# dygn har exporten fallit bort, och passet har bara HAE:s minutdata. Larmet
# upprepas dagligen i två veckor, sedan anses passet förlorat för FIT.
FIT_MISSING_AFTER = timedelta(hours=24)
FIT_MISSING_WINDOW = timedelta(days=14)
HEALTH_DATA_MAX_AGE_DAYS = 2
DROPBOX_PATH = "dropbox:Apps/HealthFitExporter"
SERVICES = ("traning-ingest", "traning-bot")

Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("watchdog")
logger.setLevel(logging.INFO)
_handler = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_handler)
logger.addHandler(logging.StreamHandler(sys.stdout))


def _utc_datetime(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def database_alerts(conn, now):
    alerts = []
    latest_import = conn.execute(
        "SELECT MAX(received_at) FROM raw_import"
    ).fetchone()[0]
    if latest_import is None:
        alerts.append("Ingen raw_import finns i databasen.")
    elif now - _utc_datetime(latest_import) > RAW_IMPORT_MAX_AGE:
        alerts.append(f"Senaste raw_import är äldre än 3 dygn ({latest_import}).")

    latest_health = conn.execute(
        "SELECT MAX(local_date) FROM daily_metric"
    ).fetchone()[0]
    if latest_health is None:
        alerts.append("Ingen hälsodata finns i daily_metric.")
    elif (now.date() - date.fromisoformat(latest_health)).days > HEALTH_DATA_MAX_AGE_DAYS:
        alerts.append(f"Senaste hälsodata är äldre än 2 dygn ({latest_health}).")

    latest_fit_run = conn.execute(
        """SELECT ir.started_at, ir.skipped_count, ir.skipped_detail
           FROM import_run ir
           JOIN source s ON s.id = ir.source_id
           WHERE s.code = 'healthfit_fit' AND ir.status = 'ok'
           ORDER BY ir.started_at DESC, ir.id DESC LIMIT 1"""
    ).fetchone()
    if latest_fit_run:
        skipped_count = latest_fit_run[1]
        skipped_detail = latest_fit_run[2]
        if skipped_detail:
            sports = [name.strip() for name in skipped_detail.split(",") if name.strip()]
            count = len(sports)
            label = "okänd sport" if count == 1 else "okända sporter"
            alerts.append(
                f"FIT-import {latest_fit_run[0]}: {count} {label}: "
                f"{', '.join(sports)}."
            )
        if skipped_count and not skipped_detail:
            alerts.append(
                f"FIT-import {latest_fit_run[0]} hoppade över "
                f"{skipped_count} filer; se parse_fit-loggen."
            )

    fmt = "%Y-%m-%dT%H:%M:%SZ"
    missing = conn.execute(
        """SELECT id, local_date, sport_name FROM v_workout
           WHERE source_code = 'apple_health_hae' AND fit_enriched = 0
             AND started_at <= ? AND started_at >= ?
           ORDER BY started_at""",
        ((now - FIT_MISSING_AFTER).strftime(fmt),
         (now - FIT_MISSING_WINDOW).strftime(fmt))).fetchall()
    if missing:
        alerts.append(
            "Pass utan FIT-fil efter 24 h, bara HAE-data: "
            + ", ".join(f"{m[1]} {m[2]} (pass {m[0]})" for m in missing) + ".")
    return alerts


def external_alerts(run=subprocess.run):
    alerts = []
    try:
        result = run(
            ["rclone", "lsf", DROPBOX_PATH, "--max-depth", "1"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            alerts.append(
                f"rclone kunde inte läsa Dropbox (exitkod {result.returncode})."
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        alerts.append(f"rclone-kontrollen misslyckades ({type(exc).__name__}).")

    for service in SERVICES:
        try:
            result = run(
                ["systemctl", "is-active", "--quiet", service],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            alerts.append(
                f"Kunde inte kontrollera tjänsten {service} ({type(exc).__name__})."
            )
            continue
        if result.returncode:
            alerts.append(f"Tjänsten {service} är inte active.")
    return alerts


def send_alert(message):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN eller TELEGRAM_CHAT_ID saknas")
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": message}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read())
    if not result.get("ok"):
        raise RuntimeError(f"Telegram svarade: {result}")
    logger.info("Telegram accepterade vakthundslarm (HTTP %s)", response.status)


def run_checks(conn, now=None, run=subprocess.run):
    now = now or datetime.now(timezone.utc)
    alerts = database_alerts(conn, now) + external_alerts(run)
    if alerts:
        send_alert("Traningsarkiv - vakthund:\n- " + "\n- ".join(alerts))
        logger.error("Vakthundslarm: %s", "; ".join(alerts))
    else:
        logger.info("Vakthund OK: import, hälsodata, Dropbox och tjänster")
    return alerts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--test-alert",
        action="store_true",
        help="Skicka ett tydligt testlarm via samma Telegram-väg som vakthundslarm.",
    )
    args = parser.parse_args()
    if args.test_alert:
        send_alert("TEST: Träningsarkivets vakthund kan skicka Telegram-larm.")
        return

    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        run_checks(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
