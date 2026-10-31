"""
Läser FIT-filer från HealthFit och skriver dem till databasen.

FIT är den rikaste källan vi har: varv, simlängder med simsätt och
armtag, puls per sekund. Där FIT och HAE säger olika saker vinner FIT.

Matchning mot redan importerade pass sker i två steg:
  1. SESSION UUID från FIT mot HAE:s external_id (exakt match)
  2. starttid inom några minuter och samma sport (reserv)
Hittas inget pass skapas ett nytt med FIT som källa - då går inget
förlorat om HAE missat passet.

Idempotent: filens sha256 ligger i raw_import. Samma fil tolkas en gång.
"""

import hashlib
import json
import logging
import os
import sqlite3
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import fitdecode

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("TRANING_DB", str(BASE_DIR / "traning.db"))
FIT_DIR = Path(os.environ.get("TRANING_FIT_DIR", str(BASE_DIR / "fit")))
LOG_PATH = os.environ.get("TRANING_FIT_LOG", str(BASE_DIR / "logs" / "fit.log"))

SOURCE_CODE = "healthfit_fit"
HAE_SOURCE = "apple_health_hae"
MATCH_FONSTER_S = 300      # starttidsmatchning mot befintligt pass
RECORD_LUCKA_MAX_S = 10    # längre glapp mellan pulsvärden räknas inte som zontid
VILA_MIN_S = 12            # kortare avbrott i simningen är en vändning, inte vila

Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("fit")
logger.setLevel(logging.INFO)
_h = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3)
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_h)
logger.addHandler(logging.StreamHandler(sys.stdout))


# ---------- hjälpare ----------

def utc_str(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def session_uuid(session):
    """HealthFit lägger HealthKit-UUID:t i ett utvecklarfält."""
    raw = session.get("SESSION UUID")
    if not raw:
        return None
    try:
        return str(uuid.UUID(bytes=bytes(raw))).upper()
    except (ValueError, TypeError):
        return None


def read_fit(path):
    """Plockar ut alla meddelandetyper vi bryr oss om ur en FIT-fil."""
    ut = defaultdict(list)
    with fitdecode.FitReader(str(path)) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.name in ("session", "lap", "length", "record", "sport", "event"):
                ut[frame.name].append({f.name: f.value for f in frame.fields})
    return ut


# ---------- beräkningar ----------

def swim_stats(session, lengths, pool_m):
    """
    Allt som bara går att få ur längd-för-längd-data.

    Längsta sammanhängande sträcka är den längsta följden av aktiva
    längder utan en vilolängd emellan - det mått som inte går att
    härleda ur minutupplösning.
    """
    aktiva = [l for l in lengths if l.get("length_type") == "active"]
    vila_s = sum(l.get("total_elapsed_time") or 0
                 for l in lengths if l.get("length_type") != "active")
    aktiv_s = sum(l.get("total_elapsed_time") or 0 for l in aktiva)

    # Längsta obrutna svit av aktiva längder.
    #
    # Apple märker alla avbrott som 'idle', även tiosekunderspauser mitt i
    # en sammanhängande sträcka. Typen duger därför inte som bevis på vila -
    # bara längden gör det. Under tröskeln är avbrottet en vändning.
    langst = rad = 0
    for l in lengths:
        if str(l.get("length_type") or "") == "active":
            rad += 1
            langst = max(langst, rad)
        elif (l.get("total_elapsed_time") or 0) >= VILA_MIN_S:
            rad = 0

    frisim = sum(1 for l in aktiva if l.get("swim_stroke") == "freestyle")
    strokes = sum(l.get("total_strokes") or 0 for l in aktiva)

    # SWOLF per längd: sekunder + armtag. Medianen är stabilare än snittet.
    swolfar = sorted((l.get("total_elapsed_time") or 0) + (l.get("total_strokes") or 0)
                     for l in aktiva if l.get("total_elapsed_time"))
    swolf_avg = (sum(swolfar) / len(swolfar)) if swolfar else None

    # Bästa SWOLF räknas bara på längder där tagräkningen är trovärdig.
    # Klockan missar ibland tag, och då blir just den längden "bäst" -
    # snittet påverkas inte, men bästa värdet gör det.
    tag = sorted(l.get("total_strokes") or 0 for l in aktiva)
    median_tag = tag[len(tag) // 2] if tag else 0
    palitliga = sorted(
        (l.get("total_elapsed_time") or 0) + (l.get("total_strokes") or 0)
        for l in aktiva
        if l.get("total_elapsed_time")
        and (l.get("total_strokes") or 0) >= median_tag * 0.75)
    swolf_best = palitliga[0] if palitliga else None

    stilar = defaultdict(int)
    for l in aktiva:
        stilar[l.get("swim_stroke") or "okänd"] += 1
    primar = max(stilar, key=stilar.get) if stilar else None

    dist = session.get("total_distance") or (len(aktiva) * (pool_m or 0))
    return {
        "pool_length_m": pool_m,
        "length_count": len(aktiva),
        "swolf_avg": round(swolf_avg, 1) if swolf_avg else None,
        "swolf_best": round(swolf_best, 1) if swolf_best else None,
        "stroke_count_total": strokes or None,
        "strokes_per_length_avg": round(strokes / len(aktiva), 1) if aktiva and strokes else None,
        "pace_s_per_100m": round(aktiv_s / (dist / 100.0), 1) if dist and aktiv_s else None,
        "primary_stroke": primar,
        "rest_s_total": round(vila_s),
        "active_s": round(aktiv_s),
        "longest_continuous_m": langst * pool_m if pool_m else None,
        "freestyle_share": round(frisim / len(aktiva), 3) if aktiva else None,
    }


def timer_blocks(events, records):
    """
    Arbetsblocken i ett pass som pausats manuellt.

    Apple Watch lägger varven per kilometer, inte per intervall - men
    varje gång klockan pausas skrivs ett timer/stop och vid återstart ett
    timer/start. Tiden mellan start och stop är ett block du faktiskt
    sprang, och det är den strukturen ett intervallpass har.

    Ger en lista av dict:ar med tid, distans och puls per block.
    """
    marker = []
    for e in events:
        if e.get("event") != "timer" or e.get("timestamp") is None:
            continue
        typ = str(e.get("event_type") or "")
        if typ == "start":
            marker.append(("start", e["timestamp"]))
        elif typ.startswith("stop"):
            marker.append(("stop", e["timestamp"]))

    intervall, oppen = [], None
    for typ, t in marker:
        if typ == "start" and oppen is None:
            oppen = t
        elif typ == "stop" and oppen is not None:
            if (t - oppen).total_seconds() > 0:
                intervall.append((oppen, t))
            oppen = None

    # Ett enda block betyder att passet aldrig pausades - då finns ingen
    # struktur att visa utöver kilometervarven.
    if len(intervall) < 2:
        return []

    pts = sorted((r for r in records if r.get("timestamp")),
                 key=lambda r: r["timestamp"])
    blocks = []
    for t0, t1 in intervall:
        inom = [r for r in pts if t0 <= r["timestamp"] <= t1]
        dists = [r["distance"] for r in inom if r.get("distance") is not None]
        pulsar = [r["heart_rate"] for r in inom if r.get("heart_rate")]
        blocks.append({
            "start": t0,
            "duration_s": (t1 - t0).total_seconds(),
            "distance_m": (dists[-1] - dists[0]) if len(dists) > 1 else None,
            "hr_avg": round(sum(pulsar) / len(pulsar)) if pulsar else None,
            "hr_max": max(pulsar) if pulsar else None,
        })
    return blocks


def zone_seconds(conn, records):
    """Tid i pulszon, räknad ur pulsen per sekund mot hr_zone-tabellen."""
    zoner = conn.execute("SELECT zone, hr_min, hr_max FROM hr_zone ORDER BY hr_min").fetchall()
    if not zoner:
        return {}

    punkter = sorted(((r["timestamp"], r["heart_rate"]) for r in records
                      if r.get("heart_rate") and r.get("timestamp")),
                     key=lambda t: t[0])
    ut = defaultdict(float)
    for i in range(len(punkter) - 1):
        t, hr = punkter[i]
        dt = (punkter[i + 1][0] - t).total_seconds()
        if dt <= 0 or dt > RECORD_LUCKA_MAX_S:
            continue
        for z in zoner:
            if z["hr_min"] <= hr <= z["hr_max"]:
                ut[z["zone"]] += dt
                break
    return ut


# ---------- persistering ----------

def find_workout(conn, hk_uuid, start_utc, sport_id):
    """Steg 1: exakt UUID-match. Steg 2: starttid inom fönstret."""
    if hk_uuid:
        # Söker i alla källor, inte bara HAE: passet kan redan ha skapats
        # av en tidigare FIT-körning.
        rad = conn.execute(
            "SELECT id FROM workout WHERE external_id = ? ORDER BY id LIMIT 1",
            (hk_uuid,)).fetchone()
        if rad:
            return rad["id"], "uuid"

    rad = conn.execute(
        """SELECT id, ABS(strftime('%s', started_at) - strftime('%s', ?)) diff
           FROM workout WHERE sport_id = ? AND diff <= ?
           ORDER BY diff LIMIT 1""",
        (start_utc, sport_id, MATCH_FONSTER_S)).fetchone()
    return (rad["id"], "tid") if rad else (None, None)


def save_segments(conn, workout_id, laps, lengths, is_swim, pool_m, blocks=()):
    """
    Varv blir segment, simlängder hängs under sitt varv via parent_id.

    Kopplingen görs med first_length_index ur FIT-filen, inte genom att
    stega framåt - annars glider längderna ur fas så fort ett varv har
    ett oväntat antal längder.

    Arbetsblock ur timer-events skrivs som egen nivå (kind='block') före
    varven. De två nivåerna beskriver samma pass på olika sätt och får
    aldrig summeras ihop.
    """
    conn.execute("DELETE FROM workout_segment WHERE workout_id=?", (workout_id,))
    seq = 0

    for b in blocks:
        seq += 1
        dist = b["distance_m"]
        conn.execute(
            """INSERT INTO workout_segment
               (workout_id, seq, kind, started_at, duration_s, distance_m,
                hr_avg, hr_max)
               VALUES (?,?,'interval',?,?,?,?,?)""",
            (workout_id, seq, utc_str(b["start"]), b["duration_s"],
             dist if dist and dist > 0 else None, b["hr_avg"], b["hr_max"]))

    for lap in laps:
        seq += 1
        dist = lap.get("total_distance") or 0
        # Ett varv utan distans är en vilopaus, inte ett arbetsintervall.
        if dist <= 0:
            kind = "rest"
        else:
            kind = "interval" if is_swim else "split"

        conn.execute(
            """INSERT INTO workout_segment
               (workout_id, seq, kind, started_at, duration_s, distance_m,
                hr_avg, hr_max, speed_kmh, cadence, power_w, elevation_gain_m, extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (workout_id, seq, kind,
             utc_str(lap.get("start_time")), lap.get("total_elapsed_time"),
             dist or None, lap.get("avg_heart_rate"), lap.get("max_heart_rate"),
             round((lap.get("avg_speed") or 0) * 3.6, 2) or None,
             lap.get("avg_running_cadence") or lap.get("avg_cadence"),
             lap.get("avg_power"), lap.get("total_ascent"),
             json.dumps({k: v for k, v in (
                 ("swim_stroke", lap.get("swim_stroke")),
                 # Avgör om varven är dina egna varvtryck eller klockans
                 # automatiska kilometermarkeringar.
                 ("lap_trigger", str(lap.get("lap_trigger"))
                  if lap.get("lap_trigger") else None),
                 ("hr_min", lap.get("min_heart_rate")),
                 ("strokes", lap.get("total_strokes")),
                 ("step_length_mm", lap.get("avg_step_length")),
                 ("stance_time_ms", lap.get("avg_stance_time")),
                 ("vertical_oscillation_mm", lap.get("avg_vertical_oscillation")),
             ) if v is not None})))
        lap_id = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]

        if not lengths:
            continue

        forsta = lap.get("first_length_index")
        antal = lap.get("num_lengths") or 0
        if forsta is None or antal <= 0:
            continue

        for l in lengths[forsta:forsta + antal]:
            seq += 1
            aktiv = l.get("length_type") == "active"
            tid = l.get("total_elapsed_time")
            strokes = l.get("total_strokes")
            conn.execute(
                """INSERT INTO workout_segment
                   (workout_id, parent_id, seq, kind, started_at, duration_s,
                    distance_m, cadence, extra)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (workout_id, lap_id, seq, "length" if aktiv else "rest",
                 utc_str(l.get("start_time")), tid,
                 pool_m if aktiv else None,
                 l.get("avg_swimming_cadence"),
                 json.dumps({k: v for k, v in (
                     ("swim_stroke", l.get("swim_stroke")),
                     ("strokes", strokes),
                     ("swolf", round(tid + strokes, 1) if aktiv and tid and strokes else None),
                 ) if v is not None})))


def main():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row

    src_id = conn.execute("SELECT id FROM source WHERE code=?", (SOURCE_CODE,)).fetchone()["id"]
    aliases = {r["external_code"]: r["sport_id"] for r in conn.execute(
        "SELECT external_code, sport_id FROM sport_alias WHERE source_id=?", (src_id,))}

    run_id = conn.execute(
        """INSERT INTO import_run (source_id, run_type, started_at)
           VALUES (?, 'poll', ?)""",
        (src_id, utc_str(datetime.now(timezone.utc)))).lastrowid

    filer = sorted(FIT_DIR.glob("*.fit"))
    nya = berikade = hoppade = 0
    okanda = set()

    for path in filer:
        digest = sha256(path)
        if conn.execute(
            """SELECT 1 FROM raw_import WHERE source_id=? AND payload_sha256=?
               AND status='parsed'""", (src_id, digest)).fetchone():
            continue

        try:
            msgs = read_fit(path)
        except Exception as exc:
            logger.error("Kunde inte läsa %s: %s", path.name, exc)
            hoppade += 1
            continue

        if not msgs.get("session"):
            logger.warning("%s saknar session-meddelande", path.name)
            hoppade += 1
            continue

        session = msgs["session"][0]
        nyckel = f"{session.get('sport')}/{session.get('sub_sport')}"
        if nyckel not in aliases:
            okanda.add(nyckel)
            hoppade += 1
            continue
        sport_id = aliases[nyckel]

        raw_id = conn.execute(
            """INSERT INTO raw_import
               (source_id, received_at, payload, payload_sha256, byte_size,
                workout_count, status)
               VALUES (?,?,?,?,?,1,'received')""",
            (src_id, utc_str(datetime.now(timezone.utc)),
             json.dumps({"fil": path.name, "sökväg": str(path),
                         "sport": nyckel}, ensure_ascii=False),
             digest, path.stat().st_size)).lastrowid

        start = session.get("start_time")
        slut = session.get("timestamp")
        hk = session_uuid(session)
        start_s = utc_str(start)

        workout_id, hur = find_workout(conn, hk, start_s, sport_id)
        is_swim = session.get("sport") == "swimming"

        # FIT är rikare än HAE - dess värden vinner på de gemensamma fälten.
        gemensamt = (
            utc_str(slut),
            int(round(session.get("total_timer_time") or 0)),
            int(round(session.get("total_elapsed_time") or 0)),
            session.get("total_distance"),
            session.get("avg_heart_rate"), session.get("max_heart_rate"),
            session.get("min_heart_rate"),
            session.get("total_calories"),
        )

        if workout_id:
            conn.execute(
                """UPDATE workout SET ended_at=?, duration_s=?, elapsed_s=?,
                     distance_m=?, hr_avg=?, hr_max=?, hr_min=?, kcal_active=?
                   WHERE id=?""", gemensamt + (workout_id,))
            berikade += 1
        else:
            lokal = start.astimezone() if start else None
            conn.execute(
                """INSERT INTO workout
                   (source_id, external_id, raw_import_id, sport_id, started_at,
                    ended_at, duration_s, elapsed_s, distance_m, hr_avg, hr_max,
                    hr_min, kcal_active, local_date, device)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (src_id, hk or digest[:32], raw_id, sport_id, start_s)
                + gemensamt + (lokal.strftime("%Y-%m-%d") if lokal else None,
                               "Apple Watch (FIT)"))
            workout_id = conn.execute(
                "SELECT id FROM workout WHERE source_id=? AND external_id=?",
                (src_id, hk or digest[:32])).fetchone()["id"]
            nya += 1

        laps = msgs.get("lap", [])
        lengths = msgs.get("length", [])
        pool_m = session.get("pool_length")
        blocks = ([] if is_swim else
                  timer_blocks(msgs.get("event", []), msgs.get("record", [])))
        save_segments(conn, workout_id, laps, lengths, is_swim, pool_m, blocks)

        if is_swim and lengths:
            s = swim_stats(session, lengths, pool_m)
            conn.execute(
                """INSERT INTO workout_swim
                   (workout_id, pool_length_m, length_count, swolf_avg, swolf_best,
                    stroke_count_total, strokes_per_length_avg, pace_s_per_100m,
                    primary_stroke, rest_s_total, active_s, longest_continuous_m,
                    freestyle_share)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(workout_id) DO UPDATE SET
                     pool_length_m=excluded.pool_length_m,
                     length_count=excluded.length_count,
                     swolf_avg=excluded.swolf_avg, swolf_best=excluded.swolf_best,
                     stroke_count_total=excluded.stroke_count_total,
                     strokes_per_length_avg=excluded.strokes_per_length_avg,
                     pace_s_per_100m=excluded.pace_s_per_100m,
                     primary_stroke=excluded.primary_stroke,
                     rest_s_total=excluded.rest_s_total, active_s=excluded.active_s,
                     longest_continuous_m=excluded.longest_continuous_m,
                     freestyle_share=excluded.freestyle_share""",
                (workout_id, s["pool_length_m"], s["length_count"], s["swolf_avg"],
                 s["swolf_best"], s["stroke_count_total"], s["strokes_per_length_avg"],
                 s["pace_s_per_100m"], s["primary_stroke"], s["rest_s_total"],
                 s["active_s"], s["longest_continuous_m"], s["freestyle_share"]))

        elif session.get("sport") == "running":
            dist = session.get("total_distance") or 0
            tid = session.get("total_timer_time") or 0
            conn.execute(
                """INSERT INTO workout_run
                   (workout_id, pace_s_per_km, elevation_gain_m, cadence_avg_spm,
                    ground_contact_ms, vertical_oscillation_cm, stride_length_m,
                    power_avg_w)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(workout_id) DO UPDATE SET
                     pace_s_per_km=excluded.pace_s_per_km,
                     elevation_gain_m=excluded.elevation_gain_m,
                     cadence_avg_spm=excluded.cadence_avg_spm,
                     ground_contact_ms=excluded.ground_contact_ms,
                     vertical_oscillation_cm=excluded.vertical_oscillation_cm,
                     stride_length_m=excluded.stride_length_m,
                     power_avg_w=excluded.power_avg_w""",
                (workout_id, round(tid / (dist / 1000.0), 1) if dist > 100 else None,
                 session.get("total_ascent"),
                 # FIT räknar kadens per ben - dubbla för steg per minut.
                 (session.get("avg_running_cadence") or 0) * 2 or None,
                 session.get("avg_stance_time"),
                 (session.get("avg_vertical_oscillation") or 0) / 10 or None,
                 (session.get("avg_step_length") or 0) / 1000 or None,
                 session.get("avg_power")))

        # Pulszoner
        zoner = zone_seconds(conn, msgs.get("record", []))
        conn.execute("DELETE FROM workout_zone WHERE workout_id=?", (workout_id,))
        for zon, sek in zoner.items():
            conn.execute(
                "INSERT INTO workout_zone (workout_id, zone, seconds) VALUES (?,?,?)",
                (workout_id, zon, round(sek, 1)))

        conn.execute("UPDATE raw_import SET status='parsed' WHERE id=?", (raw_id,))
        conn.commit()
        logger.info("%s -> pass %s (%s), %s varv, %s längder, %s block",
                    path.name, workout_id, hur or "nytt",
                    len(laps), len(lengths), len(blocks))

    conn.execute(
        """UPDATE import_run SET finished_at=?, status='ok',
           new_count=?, updated_count=?, skipped_count=? WHERE id=?""",
        (utc_str(datetime.now(timezone.utc)), nya, berikade, hoppade, run_id))
    conn.commit()

    logger.info("FIT: %s filer, %s nya pass, %s berikade, %s hoppade",
                len(filer), nya, berikade, hoppade)
    if okanda:
        logger.warning("Okända sporter (lägg till i sport_alias): %s",
                       ", ".join(sorted(okanda)))
    conn.close()


if __name__ == "__main__":
    main()
