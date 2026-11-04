"""
Skickar en Telegram-sammanfattning för varje pass som ännu inte aviserats.

Formateringen ligger i en funktion per sport med generisk fallback.
Ny sport = en funktion till. Läser från databasen, inte från payloaden,
så en framtida Garmin-källa påverkar inte det här lagret alls.
"""

import html
import json
import logging
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import blockdata
import hojd

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("TRANING_DB", str(BASE_DIR / "traning.db"))
LOG_PATH = os.environ.get("TRANING_NOTIFY_LOG", str(BASE_DIR / "logs" / "notify.log"))
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("notify")
logger.setLevel(logging.INFO)
_h = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3)
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_h)
logger.addHandler(logging.StreamHandler(sys.stdout))

DAGAR = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
MANADER = ["jan", "feb", "mar", "apr", "maj", "jun",
           "jul", "aug", "sep", "okt", "nov", "dec"]
IKON = {"run": "🏃", "run_treadmill": "🏃", "swim_pool": "🏊", "swim_open": "🏊",
        "bike": "🚴", "bike_indoor": "🚴", "strength": "🏋️", "tennis": "🎾",
        "hike": "🥾", "walk": "🚶"}


# ---------- formateringshjälp ----------

def num(v, decimals=0):
    """Svenskt talformat: mellanslag som tusenavskiljare, komma som decimal."""
    if v is None:
        return "-"
    s = f"{v:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return s


def hms(seconds):
    if seconds is None:
        return "-"
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def mmss(seconds):
    if seconds is None:
        return "-"
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def datum(local_date):
    d = datetime.strptime(local_date, "%Y-%m-%d")
    return f"{DAGAR[d.weekday()]} {d.day} {MANADER[d.month - 1]}"


def puls(w):
    """Maxpuls saknas på manuellt loggade pass - skriv inte ut den då."""
    if not w["hr_avg"]:
        return None
    if not w["hr_max"]:
        return f"Puls {w['hr_avg']} snitt"
    return f"Puls {w['hr_avg']} snitt / {w['hr_max']} max"


def rad(*delar):
    return " · ".join(d for d in delar if d)


# ---------- sportspecifika format ----------

def tempo(seg):
    """Sekunder per km ur segmentets egna tal - fältet pace_s_per_km
    fylls bara av den gamla HAE-parsern och är tomt för FIT-pass."""
    d = seg["distance_m"] or 0
    return (seg["duration_s"] / (d / 1000.0)
            if d > 50 and seg["duration_s"] else None)


def fmt_run(conn, w):
    rows = conn.execute(
        """SELECT seq, kind, started_at, distance_m, duration_s, hr_avg, hr_max
           FROM workout_segment WHERE workout_id=? AND parent_id IS NULL
           ORDER BY seq""", (w["id"],)).fetchall()

    rader = [
        f"{IKON.get(w['sport_code'], '')} <b>{html.escape(w['sport_name'])}</b> · {datum(w['local_date'])}",
        rad(f"{num(w['distance_m'] / 1000, 2)} km på {hms(w['duration_s'])}",
            f"{mmss(w['pace_s_per_km'])}/km" if w["pace_s_per_km"] else None),
        rad(puls(w),
            hojd.text(w["ascent_m"], w["descent_m"], w["ascent_apple_m"]),
            f"{num(w['kcal_active'])} kcal" if w["kcal_active"] else None),
    ]

    # Blocken mellan timer-pauserna beskriver passet bättre än
    # kilometerrastret, när de finns.
    block = [r for r in rows if r["kind"] == "interval"]
    if block and not blockdata.palitliga(block, w["distance_m"], w["elapsed_s"]):
        rader.append("")
        rader.append(blockdata.OPALITLIG)
        block = []
    block = blockdata.sla_ihop(block)
    if len(block) >= 2:
        rader.append("")
        rader.append("Block: " + " · ".join(
            f"{num(r['distance_m'] / 1000, 2)} km {mmss(tempo(r))}"
            f"{'*' if r['sammanslagen'] else ''}"
            for r in block))
        if any(r["sammanslagen"] for r in block):
            rader.append(blockdata.FOTNOT)
        return "\n".join(r for r in rader if r)

    hela = [r for r in rows if r["kind"] == "split"
            and (r["distance_m"] or 0) >= 990 and tempo(r)]
    if hela:
        rader.append("")
        rader.append("Km: " + " · ".join(mmss(tempo(r)) for r in hela))
        snabb = min(hela, key=tempo)
        langsam = max(hela, key=tempo)
        if snabb["seq"] != langsam["seq"]:
            i = {r["seq"]: n for n, r in enumerate(hela, 1)}
            rader.append(f"Snabbast km {i[snabb['seq']]} · "
                         f"långsammast km {i[langsam['seq']]}")
    return "\n".join(r for r in rader if r)


def fmt_swim(conn, w):
    # Bara varvnivån - simlängderna hänger under sina varv och skulle
    # annars räknas dubbelt.
    segs = conn.execute(
        """SELECT distance_m, kind FROM workout_segment
           WHERE workout_id=? AND parent_id IS NULL ORDER BY seq""",
        (w["id"],)).fetchall()

    rader = [
        f"{IKON.get(w['sport_code'], '')} <b>{html.escape(w['sport_name'])}</b> · "
        f"{datum(w['local_date'])}"
        + (f" · {num(w['pool_length_m'])} m bana" if w["pool_length_m"] else ""),
        rad(f"{num(w['distance_m'])} m på {hms(w['duration_s'])}",
            f"{mmss(w['pace_s_per_100m'])}/100 m simtid" if w["pace_s_per_100m"] else None),
    ]
    if w["longest_continuous_m"]:
        rader.append(f"Längsta sammanhängande: "
                     f"<b>{num(w['longest_continuous_m'])} m</b>")
    rader += [
        rad(f"{w['length_count']} längder" if w["length_count"] else None,
            f"{num(w['freestyle_share'] * 100, 0)} % frisim"
            if w["freestyle_share"] is not None else None,
            f"{num(w['strokes_per_length_avg'], 1)} armtag/längd"
            if w["strokes_per_length_avg"] else None),
        rad(puls(w),
            f"SWOLF {num(w['swolf_avg'])}" if w["swolf_avg"] else None,
            f"vila {hms(w['rest_s_total'])}" if w["rest_s_total"] else None),
    ]
    arbete = [s for s in segs if (s["distance_m"] or 0) > 0]
    if arbete:
        rader.append("")
        rader.append("Set: " + " · ".join(f"{num(s['distance_m'])} m"
                                          for s in arbete))
    return "\n".join(r for r in rader if r)


def fmt_bike(conn, w):
    rader = [
        f"{IKON.get(w['sport_code'], '')} <b>{html.escape(w['sport_name'])}</b> · {datum(w['local_date'])}",
        rad(f"{num(w['distance_m'] / 1000, 2)} km på {hms(w['duration_s'])}"
            if w["distance_m"] else hms(w["duration_s"]),
            f"{num(w['speed_avg_kmh'], 1)} km/h" if w["speed_avg_kmh"] else None),
        rad(puls(w),
            f"{num(w['power_avg_w'])} W" if w["power_avg_w"] else None,
            hojd.text(w["ascent_m"], w["descent_m"], w["ascent_apple_m"]),
            f"{num(w['kcal_active'])} kcal" if w["kcal_active"] else None),
    ]
    return "\n".join(r for r in rader if r)


def fmt_generic(conn, w):
    rader = [
        f"{IKON.get(w['sport_code'], '🏅')} <b>{html.escape(w['sport_name'])}</b> · {datum(w['local_date'])}",
        rad(hms(w["duration_s"]),
            f"{num(w['distance_m'] / 1000, 2)} km" if w["distance_m"] else None),
        rad(puls(w), hojd.text(w["ascent_m"], w["descent_m"], w["ascent_apple_m"]),
            f"{num(w['kcal_active'])} kcal" if w["kcal_active"] else None),
    ]
    if w["rpe"]:
        rader.append(f"RPE {w['rpe']}")
    if w["notes"]:
        rader.append(f"<i>{html.escape(w['notes'].splitlines()[0])}</i>")
    return "\n".join(r for r in rader if r)


VIEWS = {"run": "v_run", "run_treadmill": "v_run",
         "swim_pool": "v_swim", "swim_open": "v_swim",
         "bike": "v_bike", "bike_indoor": "v_bike"}
FORMATTERS = {"v_run": fmt_run, "v_swim": fmt_swim, "v_bike": fmt_bike}


def zontext(conn, workout_id, duration_s):
    """Zonfördelningen på en rad, med täckning när pulsen tappats bort."""
    rader = conn.execute(
        "SELECT zone, seconds FROM workout_zone WHERE workout_id=? ORDER BY zone",
        (workout_id,)).fetchall()
    if not rader:
        return None
    delar = " · ".join(f"{r['zone']} {hms(r['seconds'])}"
                       for r in rader if r["seconds"] >= 1)
    tackt = sum(r["seconds"] for r in rader)
    if duration_s and tackt < duration_s * 0.95:
        delar += f" ({num(100.0 * tackt / duration_s, 0)} % puls)"
    return delar


def build_message(conn, workout_id, sport_code):
    view = VIEWS.get(sport_code)
    row = None
    if view:
        row = conn.execute(f"SELECT * FROM {view} WHERE id=?", (workout_id,)).fetchone()
    if row:
        text = FORMATTERS[view](conn, row)
    else:
        row = conn.execute(
            "SELECT * FROM v_workout WHERE id=?", (workout_id,)).fetchone()
        text = fmt_generic(conn, row)

    z = zontext(conn, workout_id, row["duration_s"])
    if z:
        text += f"\nZoner: {z}"

    # Passnumret sist: det är nyckeln till /kommentar och /radera i boten.
    text += (f"\n\nPass <b>{workout_id}</b> · "
             f"<code>/kommentar {workout_id} …</code>")
    return text


# ---------- utskick ----------

def send(text):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("TELEGRAM_TOKEN eller TELEGRAM_CHAT_ID saknas")
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read())
    if not body.get("ok"):
        raise RuntimeError(f"Telegram svarade: {body}")


def main():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    pending = conn.execute(
        """SELECT id, sport_code FROM v_workout
           WHERE notified_at IS NULL ORDER BY started_at""").fetchall()

    if not pending:
        logger.info("Inget nytt att avisera")
        return

    sent = 0
    for row in pending:
        try:
            send(build_message(conn, row["id"], row["sport_code"]))
            conn.execute(
                "UPDATE workout SET notified_at=? WHERE id=?",
                (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), row["id"]))
            conn.commit()
            sent += 1
        except Exception as exc:
            logger.error("Kunde inte avisera pass %s: %s", row["id"], exc)

    logger.info("Skickade %s av %s notiser", sent, len(pending))
    conn.close()


if __name__ == "__main__":
    main()
