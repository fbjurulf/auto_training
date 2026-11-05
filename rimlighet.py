"""
Rimlighetskontroll vid import. Varnar i Telegram, avvisar aldrig: passet ligger
redan i databasen när kontrollen körs, och det är en människa som avgör om det
ska döljas.

Pass 43 (16 h, 800 m) och 79 (63 min, 25 m) kom in utan att något reagerade.
43 bidrog med 9 774 s simtid till varje summa och trend tills någon såg det.

Körs sist i notify, alltså i varje pipelinekörning. Vad som redan varnats står i
workout.rimlighet_varnad: FIT-filen kommer ofta efter att HAE-passet aviserats,
så kontrollen måste se om passet ändrats, men en omtolkning av gamla filer får
inte larma på nytt för samma sak. Ändras texten varnar den igen.

Gränserna är satta med marginal runt produktionens 102 pass 2026-11-05, där inga
andra än 43 och 79 faller utanför tid och tempo. De fångar fel i källan, inte
ovanliga pass.

    python rimlighet.py --baslinje   # godta dagens värden utan att varna
"""
import html
import logging
import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("TRANING_DB", str(BASE_DIR / "traning.db"))

H = 3600
# Sportkodens början: (längsta tid, snabbast s/km, långsammast s/km). Tempot är
# passets hela tid delat med distansen, vila inräknad - för simning alltså inte
# simtempot.
GRANSER = {
    "run": (8 * H, 150, 900),
    "swim": (3 * H, 600, 9000),
    "bike": (12 * H, 50, 600),
    "hike": (16 * H, 300, 3600),
    "walk": (12 * H, 300, 3600),
    "ski": (10 * H, 120, 1200),
}
OVRIGT_MAX_S = 6 * H      # sporter utan distans: tennis, styrka, badminton
MIN_TID_S = 60
HR_MAX = 220
POOLER_M = (25, 33.33, 50)
POOL_MARGINAL_M = 0.5     # distansen är längder gånger bana - fel bana ger fel meter

FRAGA = """SELECT v.id, v.local_date, v.sport_code, v.sport_name, v.duration_s,
                  v.distance_m, v.hr_max, v.rimlighet_varnad, sw.pool_length_m
           FROM v_workout v LEFT JOIN workout_swim sw ON sw.workout_id = v.id
           ORDER BY v.started_at"""

log = logging.getLogger("rimlighet")


def _tid(s):
    return f"{int(s) // H}:{int(s) % H // 60:02d}:{int(s) % 60:02d}"


def _tempo(s_per_km, sport_code):
    if sport_code.startswith("swim"):
        s, enhet = round(s_per_km / 10), "100 m"
    else:
        s, enhet = round(s_per_km), "km"
    return f"{s // 60}:{s % 60:02d}/{enhet}"


def avvikelser(sport_code, duration_s, distance_m=None, hr_max=None, pool_length_m=None):
    """Ger en text per gräns passet ligger utanför - tom lista om allt är rimligt."""
    ut = []
    prefix = next((p for p in GRANSER if sport_code.startswith(p)), None)
    max_s, snabbast, langsammast = GRANSER[prefix] if prefix else (OVRIGT_MAX_S, None, None)

    if duration_s and duration_s > max_s:
        ut.append(f"tid {_tid(duration_s)} är över gränsen {_tid(max_s)}")
    if duration_s is not None and duration_s < MIN_TID_S:
        ut.append(f"tid {duration_s} s är under gränsen {MIN_TID_S} s")
    if prefix and duration_s and distance_m:
        tempo = duration_s / (distance_m / 1000.0)
        if tempo < snabbast:
            ut.append(f"tempo {_tempo(tempo, sport_code)} är snabbare än gränsen "
                      f"{_tempo(snabbast, sport_code)}")
        elif tempo > langsammast:
            ut.append(f"tempo {_tempo(tempo, sport_code)} är långsammare än gränsen "
                      f"{_tempo(langsammast, sport_code)}")
    if hr_max and hr_max > HR_MAX:
        ut.append(f"maxpuls {hr_max} är över gränsen {HR_MAX}")
    if pool_length_m and not any(abs(pool_length_m - p) <= POOL_MARGINAL_M for p in POOLER_M):
        ut.append(f"bana {pool_length_m:g} m är ingen vanlig banlängd")
    return ut


def _text(r):
    return "\n".join(avvikelser(r["sport_code"], r["duration_s"], r["distance_m"],
                                r["hr_max"], r["pool_length_m"]))


def _meddelande(r, text):
    rader = "\n".join(f"- {html.escape(t)}" for t in text.splitlines())
    return (f"⚠️ Orimligt pass {r['id']} · {r['local_date']} · {html.escape(r['sport_name'])}\n"
            f"{rader}\n"
            f"Passet är importerat som vanligt och räknas i summorna. "
            f"Är det fel: <code>/radera {r['id']}</code>")


def kontrollera(conn, send, logger=log):
    """Varnar för pass vars avvikelser är nya eller ändrade. Ger varnade pass-id.
    Misslyckas utskicket försöker nästa körning igen."""
    varnade = []
    for r in conn.execute(FRAGA).fetchall():
        text = _text(r)
        if text == r["rimlighet_varnad"]:
            continue
        if text:
            try:
                send(_meddelande(r, text))
            except Exception as exc:
                logger.error("Kunde inte varna för pass %s: %s", r["id"], exc)
                continue
            varnade.append(r["id"])
            logger.warning("Orimligt pass %s: %s", r["id"], text.replace("\n", "; "))
        conn.execute("UPDATE workout SET rimlighet_varnad=? WHERE id=?", (text, r["id"]))
        conn.commit()
    return varnade


def baslinje(conn):
    """Godtar dagens värden utan att varna - körs en gång efter migrationen.
    Ger [(pass-id, datum, sport, text)] för det som ligger utanför gränserna."""
    ut = []
    for r in conn.execute(FRAGA).fetchall():
        text = _text(r)
        conn.execute("UPDATE workout SET rimlighet_varnad=? WHERE id=?", (text, r["id"]))
        if text:
            ut.append((r["id"], r["local_date"], r["sport_code"], text.replace("\n", "; ")))
    conn.commit()
    return ut


def main():
    if sys.argv[1:] != ["--baslinje"]:
        sys.exit(__doc__)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    utanfor = baslinje(conn)
    conn.close()
    for rad in utanfor:
        print(*rad, sep=" | ")
    print(f"Baslinje satt, {len(utanfor)} pass utanför gränserna.")


if __name__ == "__main__":
    main()
