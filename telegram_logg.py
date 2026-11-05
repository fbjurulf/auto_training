"""
Telegram-bot för manuell loggning och kommentarer.

Två saker klockan inte kan: pass den inte registrerade (tennis utan
klocka, styrka, en cykeltur med telefonen hemma) och det du tänkte om
ett pass efteråt.

Allt sker på en rad - inga följdfrågor. Ordningen spelar ingen roll:
    /logga tennis igår 90min rpe7 Spelade mot Anders, bra känsla
    /logga cykel 2026-10-08 18:30 1h45 45km
    /logga styrka 45min
    /kommentar Kändes tungt i benen hela vägen
    /kommentar 23 Vände vid bron, kortare än planerat
    /rpe 8
    /senaste
    /veckorapport
    /veckorapport 44

Körs som systemd-tjänst med long polling. Fail-closed: bara chat-id:t i
.env släpps in.
"""

import json
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import weekly

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("TRANING_DB", str(BASE_DIR / "traning.db"))
LOG_PATH = os.environ.get("TRANING_BOT_LOG", str(BASE_DIR / "logs" / "bot.log"))
OFFSET_PATH = BASE_DIR / ".telegram_offset"
TOKEN = os.environ.get("TRANING_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", "")).strip()

POLL_TIMEOUT = 30
LOKAL_TZ = datetime.now().astimezone().tzinfo

Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("bot")
logger.setLevel(logging.INFO)
_h = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3)
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_h)
logger.addHandler(logging.StreamHandler(sys.stdout))

DAGAR = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
DAG_KORT = {"mån": 0, "man": 0, "tis": 1, "ons": 2, "tor": 3, "tors": 3,
            "fre": 4, "lör": 5, "lor": 5, "sön": 6, "son": 6}

# Ord du säger i stället för sportens kod i databasen.
SPORT_ORD = {
    "tennis": "tennis", "padel": "tennis",
    "styrka": "strength", "gym": "strength", "styrketräning": "strength",
    "cykel": "bike", "cykling": "bike", "spinning": "bike_indoor",
    "spin": "bike_indoor", "trainer": "bike_indoor",
    "löpning": "run", "löp": "run", "spring": "run", "löpband": "run_treadmill",
    "simning": "swim_pool", "sim": "swim_pool", "bassäng": "swim_pool",
    "öppetvatten": "swim_open", "ow": "swim_open",
    "vandring": "hike", "promenad": "walk", "gå": "walk",
    "övrigt": "other", "annat": "other",
}


# ---------- textverktyg ----------

def avkoda(s):
    """Gemener utan diakritiska tecken - för tålig ordmatchning."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def hms(sek):
    if not sek:
        return "-"
    sek = int(round(sek))
    h, r = divmod(sek, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def utc_nu():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- tolkning av kommandoraden ----------

def tolka_datum(token):
    """'idag', 'igår', 'ons', '2026-10-08', '8/10' -> date, annars None."""
    t = avkoda(token)
    idag = date.today()
    if t in ("idag", "nu"):
        return idag
    if t in ("igar", "gar"):
        return idag - timedelta(days=1)
    if t in ("forrgar", "iforrgar"):
        return idag - timedelta(days=2)
    if t in DAG_KORT:
        # Närmast föregående förekomst av veckodagen, inklusive idag.
        delta = (idag.weekday() - DAG_KORT[t]) % 7
        return idag - timedelta(days=delta)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        try:
            return date.fromisoformat(t)
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})", t)
    if m:
        dag, manad = int(m.group(1)), int(m.group(2))
        try:
            d = date(idag.year, manad, dag)
        except ValueError:
            return None
        # Ett datum längre fram än idag menar förra året.
        return d.replace(year=idag.year - 1) if d > idag else d
    return None


def tolka_klockslag(token):
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", token)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return (h, mi) if h < 24 and mi < 60 else None


def tolka_tid(token):
    """
    Duration i sekunder. Enheter krävs, annars blir '45' tvetydigt.
        90min  45m är INTE tid utan distans - se tolka_distans
        1h45   1h  2tim  1:45:00
    """
    t = avkoda(token).replace(",", ".")

    m = re.fullmatch(r"(\d+):(\d{2}):(\d{2})", t)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))

    m = re.fullmatch(r"(\d+)(?:h|t|tim|timmar?)(\d{1,2})?(?:min|m)?", t)
    if m:
        sek = int(m.group(1)) * 3600
        return sek + int(m.group(2)) * 60 if m.group(2) else sek

    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:min|minuter?)", t)
    if m:
        return int(float(m.group(1)) * 60)

    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:h|t|tim|timmar?)", t)
    if m:
        return int(float(m.group(1)) * 3600)
    return None


def tolka_distans(token):
    """Meter. 'km' och 'm' - 'm' är alltid meter, aldrig minuter."""
    t = avkoda(token).replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:km|kilometer)", t)
    if m:
        return float(m.group(1)) * 1000
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:m|meter)", t)
    if m:
        return float(m.group(1))
    return None


def tolka_rad(conn, text):
    """
    Plockar isär en /logga-rad. Allt som inte känns igen blir notering,
    i den ordning orden stod.
    """
    sporter = {}
    for r in conn.execute("SELECT code, name_sv FROM sport WHERE active=1"):
        sporter[avkoda(r["code"])] = r["code"]
        sporter[avkoda(r["name_sv"])] = r["code"]
    for ord_, kod in SPORT_ORD.items():
        sporter.setdefault(avkoda(ord_), kod)

    ut = {"sport": None, "datum": None, "klockslag": None, "duration_s": None,
          "distance_m": None, "rpe": None, "hr_avg": None, "notes": None}
    rest = []

    for token in text.split():
        t = avkoda(token)

        if ut["sport"] is None and t in sporter:
            ut["sport"] = sporter[t]
            continue
        if ut["datum"] is None:
            d = tolka_datum(token)
            if d:
                ut["datum"] = d
                continue
        if ut["klockslag"] is None:
            k = tolka_klockslag(token)
            if k:
                ut["klockslag"] = k
                continue
        if ut["duration_s"] is None:
            s = tolka_tid(token)
            if s:
                ut["duration_s"] = s
                continue
        if ut["distance_m"] is None:
            d = tolka_distans(token)
            if d:
                ut["distance_m"] = d
                continue
        m = re.fullmatch(r"rpe:?(\d{1,2})", t)
        if m and ut["rpe"] is None:
            v = int(m.group(1))
            if 1 <= v <= 10:
                ut["rpe"] = v
                continue
        m = re.fullmatch(r"(?:puls|hr):?(\d{2,3})", t)
        if m and ut["hr_avg"] is None:
            v = int(m.group(1))
            if 25 <= v <= 250:
                ut["hr_avg"] = v
                continue

        rest.append(token)

    ut["notes"] = " ".join(rest).strip() or None
    return ut


# ---------- databas ----------

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


def senaste_pass(conn, antal=8):
    return conn.execute(
        """SELECT w.id, w.local_date, w.started_at, w.duration_s, w.distance_m,
                  w.notes, w.rpe, s.name_sv sport, src.code kalla
           FROM workout w
           JOIN sport s ON s.id = w.sport_id
           JOIN source src ON src.id = w.source_id
           WHERE w.duplicate_of_id IS NULL AND w.deleted_at IS NULL
           ORDER BY w.started_at DESC LIMIT ?""", (antal,)).fetchall()


def skapa_pass(conn, f):
    sport = conn.execute(
        "SELECT id, name_sv FROM sport WHERE code=?", (f["sport"],)).fetchone()
    if sport is None:
        raise ValueError(f"Okänd sport: {f['sport']}")

    src_id = conn.execute(
        "SELECT id FROM source WHERE code='manual'").fetchone()["id"]

    d = f["datum"] or date.today()
    h, mi = f["klockslag"] or (18, 0)
    start = datetime(d.year, d.month, d.day, h, mi, tzinfo=LOKAL_TZ)
    dur = f["duration_s"] or 0
    slut = start + timedelta(seconds=dur)

    ext = f"manual:{uuid.uuid4()}"
    conn.execute(
        """INSERT INTO workout
           (source_id, external_id, sport_id, started_at, ended_at,
            tz_offset_min, local_date, duration_s, elapsed_s, distance_m,
            hr_avg, rpe, notes, device, notified_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'manuell',?)""",
        (src_id, ext, sport["id"],
         start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         slut.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
         int(start.utcoffset().total_seconds() // 60), d.isoformat(),
         dur, dur, f["distance_m"], f["hr_avg"], f["rpe"], f["notes"],
         utc_nu()))
    conn.commit()

    wid = conn.execute(
        "SELECT id FROM workout WHERE external_id=?", (ext,)).fetchone()["id"]
    return wid, sport["name_sv"], start


# ---------- kommandon ----------

HJALP = """<b>Träningsloggen</b>

<b>/logga</b> - pass klockan missade
Allt på en rad, ordningen spelar ingen roll:
<code>/logga tennis 90min</code>
<code>/logga styrka igår 45min rpe7 Tungt i benen</code>
<code>/logga cykel 2026-10-08 18:30 1h45 45km puls132</code>

Delar som känns igen:
• sport - tennis, styrka, cykel, spinning, löpning, simning, vandring, promenad
• datum - idag, igår, ons, 2026-10-08, 8/10 (utelämnas = idag)
• klockslag - 18:30 (utelämnas = 18:00)
• tid - 90min, 1h45, 2tim, 1:45:00
• distans - 45km, 1500m (m är alltid meter)
• ansträngning - rpe7
• puls - puls132
• resten blir notering

<b>/kommentar</b> - lägg till text på ett pass
<code>/kommentar Kändes tungt hela vägen</code> (senaste passet)
<code>/kommentar 23 Vände vid bron</code> (pass 23)

<b>/rpe</b> - upplevd ansträngning 1-10 på senaste passet
<code>/rpe 8</code>

<b>/senaste</b> - de åtta senaste passen med id

<b>/radera</b> - ta bort ett pass som inte var träning
<code>/radera 23</code>
Manuellt loggade pass försvinner helt. Importerade döljs i stället, så
att de inte kommer tillbaka vid nästa import - de räknas då inte i
rapporter eller statistik.

<b>/angra</b> - ta fram ett dolt pass igen: <code>/angra 23</code>
<b>/dolda</b> - lista dolda pass

<b>/veckorapport</b> - veckan hittills, som fil
<code>/veckorapport 44</code> ger vecka 44 i sin helhet. Rapporten säger överst
om veckan är ofullständig. Söndagens rapport påverkas inte."""


def cmd_logga(conn, arg):
    if not arg.strip():
        return ("Skriv vad du gjorde, till exempel:\n"
                "<code>/logga tennis 90min</code>\n\n/hjalp visar alla delar.")

    f = tolka_rad(conn, arg)
    if not f["sport"]:
        koder = ", ".join(sorted({v for v in SPORT_ORD}))
        return (f"Jag hittade ingen sport i raden.\n\nSkriv en av: {koder}\n"
                f"Saknas din sport? Lägg till den i sport-tabellen.")
    if not f["duration_s"]:
        return ("Jag hittade ingen tid. Skriv den med enhet - "
                "<code>90min</code>, <code>1h45</code> eller <code>2tim</code>.")

    wid, sportnamn, start = skapa_pass(conn, f)

    rader = [f"Loggat: <b>{sportnamn}</b>, pass {wid}",
             f"{DAGAR[start.weekday()]} {start.strftime('%Y-%m-%d %H:%M')}, "
             f"{hms(f['duration_s'])}"]
    detalj = []
    if f["distance_m"]:
        km = f["distance_m"] / 1000
        detalj.append(f"{km:.2f} km".replace(".", ",") if km >= 1
                      else f"{f['distance_m']:.0f} m")
    if f["hr_avg"]:
        detalj.append(f"puls {f['hr_avg']}")
    if f["rpe"]:
        detalj.append(f"RPE {f['rpe']}")
    if detalj:
        rader.append(" · ".join(detalj))
    if f["notes"]:
        rader.append(f"<i>{f['notes']}</i>")
    return "\n".join(rader)


def cmd_kommentar(conn, arg):
    arg = arg.strip()
    if not arg:
        return "Skriv kommentaren efter kommandot."

    m = re.match(r"^(\d+)\s+(.+)$", arg, re.S)
    if m:
        wid, text = int(m.group(1)), m.group(2).strip()
        rad = conn.execute(
            "SELECT id FROM workout WHERE id=?", (wid,)).fetchone()
        if rad is None:
            return f"Hittar inget pass med id {wid}. /senaste visar de senaste."
    else:
        text = arg
        rad = conn.execute(
            """SELECT id FROM workout WHERE duplicate_of_id IS NULL AND deleted_at IS NULL
               ORDER BY started_at DESC LIMIT 1""").fetchone()
        if rad is None:
            return "Det finns inga pass att kommentera än."
        wid = rad["id"]

    # Lägg till i stället för att skriva över - en andra tanke om samma
    # pass ska inte radera den första.
    gammal = conn.execute(
        "SELECT notes FROM workout WHERE id=?", (wid,)).fetchone()["notes"]
    ny = f"{gammal}\n{text}" if gammal else text
    conn.execute("UPDATE workout SET notes=? WHERE id=?", (ny, wid))
    conn.commit()

    info = conn.execute(
        """SELECT w.local_date, s.name_sv sport FROM workout w
           JOIN sport s ON s.id = w.sport_id WHERE w.id=?""", (wid,)).fetchone()
    lagt_till = " Tillagt under den tidigare." if gammal else ""
    return (f"Kommentar sparad på pass {wid} "
            f"({info['sport']} {info['local_date']}).{lagt_till}")


def cmd_rpe(conn, arg):
    m = re.fullmatch(r"\s*(\d{1,2})\s*", arg or "")
    if not m or not 1 <= int(m.group(1)) <= 10:
        return "Skriv ett tal mellan 1 och 10, till exempel <code>/rpe 7</code>."
    rad = conn.execute(
        """SELECT w.id, w.local_date, s.name_sv sport FROM workout w
           JOIN sport s ON s.id = w.sport_id
           WHERE w.duplicate_of_id IS NULL AND w.deleted_at IS NULL
           ORDER BY w.started_at DESC LIMIT 1""").fetchone()
    if rad is None:
        return "Det finns inga pass att sätta RPE på än."
    conn.execute("UPDATE workout SET rpe=? WHERE id=?", (int(m.group(1)), rad["id"]))
    conn.commit()
    return (f"RPE {m.group(1)} sparad på pass {rad['id']} "
            f"({rad['sport']} {rad['local_date']}).")


def cmd_senaste(conn, arg):
    rader = senaste_pass(conn)
    if not rader:
        return "Inga pass i databasen än."
    ut = ["<b>Senaste passen</b>", ""]
    for r in rader:
        bit = [f"<b>{r['id']}</b>", r["local_date"], r["sport"], hms(r["duration_s"])]
        if r["distance_m"]:
            bit.append(f"{r['distance_m'] / 1000:.2f} km".replace(".", ","))
        if r["rpe"]:
            bit.append(f"RPE {r['rpe']}")
        if r["kalla"] == "manual":
            bit.append("manuell")
        ut.append(" · ".join(bit))
        if r["notes"]:
            forsta = r["notes"].splitlines()[0]
            ut.append(f"   <i>{forsta[:80]}</i>")
    return "\n".join(ut)


def cmd_radera(conn, arg):
    m = re.fullmatch(r"\s*(\d+)\s*", arg or "")
    if not m:
        return ("Skriv passets id, till exempel <code>/radera 23</code>. "
                "<code>/senaste</code> visar id:na.")
    wid = int(m.group(1))
    rad = conn.execute(
        """SELECT w.id, w.local_date, w.deleted_at, s.name_sv sport,
                  src.code kalla
           FROM workout w JOIN sport s ON s.id = w.sport_id
           JOIN source src ON src.id = w.source_id WHERE w.id=?""",
        (wid,)).fetchone()
    if rad is None:
        return f"Hittar inget pass med id {wid}."
    if rad["deleted_at"]:
        return f"Pass {wid} är redan dolt. <code>/angra {wid}</code> tar fram det."

    if rad["kalla"] == "manual":
        # Inget importsteg kan återskapa ett manuellt pass, så raden kan gå.
        conn.execute("DELETE FROM workout WHERE id=?", (wid,))
        conn.commit()
        return f"Pass {wid} ({rad['sport']} {rad['local_date']}) raderat."

    # Ett importerat pass måste behålla sin rad - annars skapar nästa
    # import det på nytt ur samma fil. Det döljs i stället.
    conn.execute("UPDATE workout SET deleted_at=? WHERE id=?", (utc_nu(), wid))
    conn.commit()
    return (f"Pass {wid} ({rad['sport']} {rad['local_date']}) är nu dolt. "
            f"Det räknas inte i veckorapport eller statistik, och kommer "
            f"inte tillbaka vid nästa import.\n\n"
            f"<code>/angra {wid}</code> tar fram det igen.")


def cmd_angra(conn, arg):
    m = re.fullmatch(r"\s*(\d+)\s*", arg or "")
    if not m:
        return ("Skriv passets id, till exempel <code>/angra 23</code>. "
                "<code>/dolda</code> listar dolda pass.")
    wid = int(m.group(1))
    rad = conn.execute(
        """SELECT w.id, w.local_date, w.deleted_at, s.name_sv sport
           FROM workout w JOIN sport s ON s.id = w.sport_id WHERE w.id=?""",
        (wid,)).fetchone()
    if rad is None:
        return f"Hittar inget pass med id {wid}."
    if not rad["deleted_at"]:
        return f"Pass {wid} är inte dolt."
    conn.execute("UPDATE workout SET deleted_at=NULL WHERE id=?", (wid,))
    conn.commit()
    return f"Pass {wid} ({rad['sport']} {rad['local_date']}) syns igen."


def cmd_dolda(conn, arg):
    rader = conn.execute(
        """SELECT w.id, w.local_date, w.duration_s, w.distance_m,
                  s.name_sv sport
           FROM workout w JOIN sport s ON s.id = w.sport_id
           WHERE w.deleted_at IS NOT NULL
           ORDER BY w.started_at DESC LIMIT 15""").fetchall()
    if not rader:
        return "Inga dolda pass."
    ut = ["<b>Dolda pass</b>", ""]
    for r in rader:
        bit = [f"<b>{r['id']}</b>", r["local_date"], r["sport"], hms(r["duration_s"])]
        if r["distance_m"]:
            bit.append(f"{r['distance_m'] / 1000:.2f} km".replace(".", ","))
        ut.append(" · ".join(bit))
    ut.append("")
    ut.append("<code>/angra &lt;id&gt;</code> tar fram ett av dem igen.")
    return "\n".join(ut)


def rapportvecka(arg, nu):
    """(måndag, söndag) för /veckorapport, eller en text som säger vad som är fel."""
    arg = arg.strip()
    if not arg:
        return weekly.week_bounds(nu.date())
    if not re.fullmatch(r"\d{1,2}", arg):
        return "Skriv ett veckonummer, till exempel <code>/veckorapport 44</code>."
    vecka = int(arg)
    ar, denna, _ = nu.date().isocalendar()
    # En vecka längre fram än denna menar förra året, som i tolka_datum.
    if vecka > denna:
        ar -= 1
    try:
        return weekly.week_bounds(date.fromisocalendar(ar, vecka, 1))
    except ValueError:
        return f"Vecka {vecka} finns inte i {ar}."


def cmd_veckorapport(arg):
    """
    Samma beräkning som söndagsrapporten, weekly.build_report. Databasen öppnas
    skrivskyddad och ingen rapportfil skrivs, så söndagens körning blir oförändrad.
    """
    nu = weekly._nu()
    vecka = rapportvecka(arg, nu)
    if isinstance(vecka, str):
        return vecka
    start, end = vecka
    conn = sqlite3.connect(f"{Path(DB_PATH).resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rapport = weekly.build_report(conn, start, end, slut=nu)
    finally:
        conn.close()
    filnamn, rubrik = weekly.rapportfil(start, end, nu)
    weekly.telegram("sendDocument", {"chat_id": CHAT_ID, "caption": rubrik},
                    {"document": (filnamn, rapport.encode("utf-8"))}, token=TOKEN)
    return None


KOMMANDON = {
    "logga": cmd_logga, "log": cmd_logga,
    "kommentar": cmd_kommentar, "kommentera": cmd_kommentar, "not": cmd_kommentar,
    "rpe": cmd_rpe,
    "senaste": cmd_senaste, "lista": cmd_senaste,
    "radera": cmd_radera, "dolj": cmd_radera, "ta_bort": cmd_radera,
    "angra": cmd_angra, "aterstall": cmd_angra,
    "dolda": cmd_dolda,
}


# ---------- Telegram ----------

def api(metod, **falt):
    data = urllib.parse.urlencode(falt).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{metod}", data=data)
    with urllib.request.urlopen(req, timeout=POLL_TIMEOUT + 15) as resp:
        return json.loads(resp.read())


def skicka(text):
    try:
        svar = api("sendMessage", chat_id=CHAT_ID, text=text,
                   parse_mode="HTML", disable_web_page_preview="true")
        if not svar.get("ok"):
            logger.error("Telegram avvisade meddelandet: %s", svar)
    except Exception as exc:
        logger.error("Kunde inte skicka: %s", exc)


def las_offset():
    try:
        return int(OFFSET_PATH.read_text().strip())
    except (OSError, ValueError):
        return 0


def spara_offset(v):
    try:
        OFFSET_PATH.write_text(str(v))
    except OSError as exc:
        logger.error("Kunde inte spara offset: %s", exc)


def hantera(text):
    if not text.startswith("/"):
        return ("Börja med ett kommando. <code>/hjalp</code> visar vilka "
                "som finns.")
    ord_, _, arg = text[1:].partition(" ")
    namn = avkoda(ord_.split("@")[0])

    if namn in ("hjalp", "help", "start", "hjälp"):
        return HJALP
    if namn in ("veckorapport", "vecka"):
        try:
            return cmd_veckorapport(arg)
        except Exception as exc:
            logger.exception("Fel i /%s", namn)
            return f"Kunde inte ta fram veckorapporten: {exc}"
    fn = KOMMANDON.get(namn)
    if fn is None:
        return f"Okänt kommando: /{ord_}\n\n<code>/hjalp</code> visar alla."

    conn = db()
    try:
        return fn(conn, arg)
    except Exception as exc:
        logger.exception("Fel i /%s", namn)
        return f"Något gick fel: {exc}"
    finally:
        conn.close()


def main():
    if not TOKEN or not CHAT_ID:
        logger.error("TRANING_BOT_TOKEN/TELEGRAM_TOKEN eller "
                     "TELEGRAM_CHAT_ID saknas i miljön")
        sys.exit(1)

    offset = las_offset()
    logger.info("Boten startad, offset %s", offset)

    while True:
        try:
            svar = api("getUpdates", offset=offset, timeout=POLL_TIMEOUT)
        except urllib.error.URLError as exc:
            logger.warning("Nätverksfel mot Telegram: %s", exc)
            time.sleep(10)
            continue
        except Exception as exc:
            logger.error("Oväntat fel vid getUpdates: %s", exc)
            time.sleep(10)
            continue

        if not svar.get("ok"):
            logger.error("getUpdates misslyckades: %s", svar)
            time.sleep(10)
            continue

        for upd in svar.get("result", []):
            offset = upd["update_id"] + 1
            spara_offset(offset)

            meddelande = upd.get("message") or upd.get("edited_message")
            if not meddelande:
                continue
            avsandare = str(meddelande.get("chat", {}).get("id"))
            # Fail-closed: okända chattar får inget svar alls.
            if avsandare != CHAT_ID:
                logger.warning("Avvisade meddelande från chat %s", avsandare)
                continue

            text = (meddelande.get("text") or "").strip()
            if not text:
                continue
            logger.info("Tog emot: %s", text[:120])
            svar_text = hantera(text)
            # /veckorapport svarar med en fil och har inget att lägga till.
            if svar_text:
                skicka(svar_text)


if __name__ == "__main__":
    main()
