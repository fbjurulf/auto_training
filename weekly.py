"""
Veckosammanfattning: skriver en Markdown-fil avsedd att klistras in i en
chatt för analys, och skickar den plus en kort notis via Telegram.

Filosofi: rådata framför tolkning. Scriptet räknar, jämför och
strukturerar - men drar inga slutsatser om form eller belastning.

Återhämtningsavsnittet lyfter avvikande dygn, inte snitt. En natt på
fyra timmar syns; ett veckosnitt på 6,5 h döljer den.

Körs söndag kväll via weekly.sh. Kan köras manuellt för valfri vecka:
    ./weekly.sh              # veckan som just avslutats
    ./weekly.sh 2026-09-07   # veckan som innehåller det datumet
"""

import json
import logging
import os
import sqlite3
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

import blockdata

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("TRANING_DB", str(BASE_DIR / "traning.db"))
OUT_DIR = Path(os.environ.get("TRANING_REPORT_DIR", str(BASE_DIR / "rapporter")))
LOG_PATH = os.environ.get("TRANING_WEEKLY_LOG", str(BASE_DIR / "logs" / "weekly.log"))
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

OUT_DIR.mkdir(parents=True, exist_ok=True)
Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("weekly")
logger.setLevel(logging.INFO)
_h = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3)
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_h)
logger.addHandler(logging.StreamHandler(sys.stdout))

DAGAR = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]

# Gränser för vad som räknas som avvikande dygn.
SOMN_LAG = 6.0          # timmar - under detta flaggas natten
SOMN_MYCKET_LAG = 5.0
VILOPULS_OVER = 5       # slag över 28-dagarsmedianen
HRV_ANDEL = 0.75        # andel av medianen - under detta flaggas dygnet
STEG_ANDEL = 0.40       # andel av medianen - under detta är dagen påfallande stillasittande
BASLINJE_MIN_DAGAR = 10 # färre än så: ingen jämförelse alls
VANDNING_MAX_S = 12     # kortare avbrott i simningen är en vändning, inte vila
LUGNA_ZONER = ("Z1-Z2",)  # zoner som räknas som lugn träning i 80/20-måttet
ZON_MAL = 80            # andel av träningstiden som bör ligga lugnt


# ---------- formatering ----------

def hms(s):
    if not s:
        return "-"
    s = int(round(s))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def mmss(s):
    if not s:
        return "-"
    s = int(round(s))
    return f"{s // 60}:{s % 60:02d}"


def n(v, d=1):
    return "-" if v is None else f"{v:.{d}f}".replace(".", ",")


def delta(now, prev, unit="", d=1):
    """Skillnad mot jämförelseperiod. Ren ASCII - inga specialtecken."""
    if prev is None or now is None:
        return "-"
    diff = now - prev
    return f"{'+' if diff >= 0 else '-'}{n(abs(diff), d)}{unit}"


def median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


# ---------- datahämtning ----------

def week_bounds(anchor: date):
    monday = anchor - timedelta(days=anchor.weekday())
    return monday, monday + timedelta(days=6)


def totals(conn, start: date, end: date):
    rows = conn.execute(
        """SELECT sport_code, sport_name, COUNT(*) pass,
                  SUM(duration_s) sek, SUM(distance_m) m,
                  AVG(hr_avg) puls, SUM(kcal_active) kcal
           FROM v_workout WHERE local_date BETWEEN ? AND ?
           GROUP BY sport_code ORDER BY sek DESC""",
        (start.isoformat(), end.isoformat())).fetchall()
    return {r["sport_code"]: dict(r) for r in rows}


def workouts(conn, start: date, end: date):
    return conn.execute(
        """SELECT * FROM v_workout WHERE local_date BETWEEN ? AND ?
           ORDER BY started_at""",
        (start.isoformat(), end.isoformat())).fetchall()


def detail(conn, workout_id, sport_code):
    view = {"run": "v_run", "run_treadmill": "v_run", "swim_pool": "v_swim",
            "swim_open": "v_swim", "bike": "v_bike", "bike_indoor": "v_bike"}.get(sport_code)
    if not view:
        return None
    return conn.execute(f"SELECT * FROM {view} WHERE id=?", (workout_id,)).fetchone()


def segments(conn, workout_id):
    return conn.execute(
        "SELECT * FROM workout_segment WHERE workout_id=? ORDER BY seq",
        (workout_id,)).fetchall()


def zones_for(conn, workout_id):
    """{zon: sekunder} för ett pass."""
    return {r["zone"]: r["seconds"] for r in conn.execute(
        "SELECT zone, seconds FROM workout_zone WHERE workout_id=?", (workout_id,))}


def zone_section(conn, start: date, end: date):
    """
    Tid i pulszon per vecka, per sport och totalt. Andelen lugn träning
    står först - det är måttet som gör 80/20 verifierbart.
    """
    rader = conn.execute(
        """SELECT s.code sport_code, s.name_sv sport_name, z.zone,
                  SUM(z.seconds) sek
           FROM workout_zone z
           JOIN workout w ON w.id = z.workout_id
           JOIN sport s ON s.id = w.sport_id
           WHERE w.local_date BETWEEN ? AND ? AND w.duplicate_of_id IS NULL AND w.deleted_at IS NULL
           GROUP BY s.code, z.zone""",
        (start.isoformat(), end.isoformat())).fetchall()

    if not rader:
        return ["## Tid i pulszon", "",
                "Ingen zondata för veckan. Zoner beräknas ur pulsen per sekund "
                "i FIT-filerna, så pass som bara importerats via Health Auto "
                "Export saknar dem.", ""]

    zoner = [r["zone"] for r in conn.execute(
        "SELECT zone FROM hr_zone ORDER BY hr_min")]
    granser = {r["zone"]: (r["hr_min"], r["hr_max"]) for r in conn.execute(
        "SELECT zone, hr_min, hr_max FROM hr_zone")}

    per_sport = defaultdict(lambda: defaultdict(float))
    namn = {}
    for r in rader:
        per_sport[r["sport_code"]][r["zone"]] += r["sek"]
        namn[r["sport_code"]] = r["sport_name"]

    totalt = defaultdict(float)
    for sport in per_sport.values():
        for z, s in sport.items():
            totalt[z] += s
    summa = sum(totalt.values())
    lugnt = sum(totalt[z] for z in LUGNA_ZONER if z in totalt)
    andel = 100.0 * lugnt / summa if summa else 0

    # Zontiden byggs av pulsen sekund för sekund. Tappar klockan kontakten
    # finns inget värde att placera i en zon, och underlaget blir mindre än
    # passets tid. Det ska synas - annars ser andelen säkrare ut än den är.
    tranad = conn.execute(
        """SELECT SUM(w.duration_s) sek FROM workout w
           WHERE w.local_date BETWEEN ? AND ? AND w.duplicate_of_id IS NULL AND w.deleted_at IS NULL
             AND EXISTS (SELECT 1 FROM workout_zone z WHERE z.workout_id = w.id)""",
        (start.isoformat(), end.isoformat())).fetchone()["sek"] or 0

    L = ["## Tid i pulszon", ""]
    status = "över" if andel >= ZON_MAL else "under"
    L.append(f"**{n(andel, 0)} % av träningstiden låg lugnt** "
             f"({'+'.join(LUGNA_ZONER)}), {status} målet på {ZON_MAL} %.")
    L.append("")

    if tranad and summa < tranad * 0.995:
        tackning = 100.0 * summa / tranad
        L.append(f"Underlaget är {hms(summa)} av {hms(tranad)} träningstid, "
                 f"{n(tackning, 0)} % täckning. Resten saknar pulsvärde: "
                 f"handledsmätningen tappar kontakt vid svett och spända "
                 f"underarmar, vilket typiskt drabbar de hårdaste passen mest. "
                 f"Andelen ovan är alltså räknad på den puls som faktiskt "
                 f"registrerades, inte på hela veckan.")
        L.append("")

    L.append("| Sport | " + " | ".join(zoner) + " | Lugnt |")
    L.append("|---" * (len(zoner) + 2) + "|")
    for code, sport in sorted(per_sport.items(),
                              key=lambda kv: -sum(kv[1].values())):
        s_summa = sum(sport.values())
        s_lugnt = sum(sport.get(z, 0) for z in LUGNA_ZONER)
        celler = [hms(sport.get(z, 0)) if sport.get(z) else "-" for z in zoner]
        L.append(f"| {namn[code]} | " + " | ".join(celler) +
                 f" | {n(100.0 * s_lugnt / s_summa, 0)} % |")
    celler = [hms(totalt.get(z, 0)) if totalt.get(z) else "-" for z in zoner]
    L.append(f"| **Totalt** | " + " | ".join(celler) + f" | **{n(andel, 0)} %** |")
    L.append("")

    L.append("Zongränser: " + ", ".join(
        f"{z} {granser[z][0]}-{granser[z][1]}" for z in zoner) +
        " slag/min. De ändras i tabellen hr_zone, inte i koden.")
    L.append("")
    return L


def metrics_between(conn, metric, start: date, end: date):
    """{datum: värde} för ett mätvärde i ett intervall."""
    return {r["local_date"]: r["qty"] for r in conn.execute(
        """SELECT local_date, qty FROM daily_metric
           WHERE metric=? AND local_date BETWEEN ? AND ?""",
        (metric, start.isoformat(), end.isoformat()))}


def veckor_med_data(conn, start: date, antal=4):
    """Hur många av de N föregående veckorna som faktiskt har pass."""
    träffar = 0
    for i in range(1, antal + 1):
        ws, we = start - timedelta(days=7 * i), start - timedelta(days=7 * i - 6)
        if conn.execute(
            "SELECT 1 FROM v_workout WHERE local_date BETWEEN ? AND ? LIMIT 1",
            (ws.isoformat(), we.isoformat())).fetchone():
            träffar += 1
    return träffar


# ---------- återhämtning ----------

def health_section(conn, start: date, end: date):
    """
    Hälsodata för veckan: sammanfattning, dygnstabell och en uttrycklig
    rad per mätvärde. Baslinjen är medianen för 28 dagar före veckan -
    medianen används i stället för medelvärdet så att ett enskilt
    extremdygn inte flyttar referensen.

    Avvikelser är poängen. Saknas de sägs det rakt ut, så att tystnad
    inte går att förväxla med utebliven mätning.
    """
    bas_start, bas_slut = start - timedelta(days=28), start - timedelta(days=1)

    serier = {m: metrics_between(conn, m, start, end) for m in
              ("resting_heart_rate", "heart_rate_variability",
               "sleep_analysis", "step_count", "vo2_max")}
    baslinje = {m: median(metrics_between(conn, m, bas_start, bas_slut).values())
                for m in serier}
    bas_dagar = len(metrics_between(conn, "resting_heart_rate", bas_start, bas_slut))
    har_baslinje = bas_dagar >= BASLINJE_MIN_DAGAR

    if not any(serier.values()):
        return ["## Hälsodata", "", "Ingen hälsodata registrerad för veckan.", ""]

    L = ["## Hälsodata", ""]

    # --- sammanfattning ---
    L.append("| Mätvärde | Veckosnitt | Lägst | Högst | Baslinje 28 d |")
    L.append("|---|---|---|---|---|")
    for key, namn, enhet, dec in (
            ("resting_heart_rate", "Vilopuls", "", 0),
            ("heart_rate_variability", "HRV", " ms", 0),
            ("sleep_analysis", "Sömn", " h", 1),
            ("step_count", "Steg", "", 0)):
        v = [x for x in serier[key].values() if x is not None]
        if not v:
            L.append(f"| {namn} | - | - | - | - |")
            continue
        bas = n(baslinje[key], dec) + enhet if har_baslinje and baslinje[key] else "-"
        L.append(f"| {namn} | {n(sum(v) / len(v), dec)}{enhet} | {n(min(v), dec)}{enhet} "
                 f"| {n(max(v), dec)}{enhet} | {bas} |")
    L.append("")

    # VO2max är ett estimat som skrivs om, inte en daglig mätning - lägst
    # och högst för veckan säger ingenting, så den får en egen rad.
    if serier["vo2_max"]:
        senaste_dag = max(serier["vo2_max"])
        senaste = serier["vo2_max"][senaste_dag]
        bas = baslinje["vo2_max"]
        riktning = ""
        if bas:
            diff = senaste - bas
            riktning = (f", {'+' if diff >= 0 else '-'}{n(abs(diff), 1)} "
                        f"mot {n(bas, 1)} fyra veckor tillbaka")
        L.append(f"VO2max senast uppdaterad {senaste_dag}: "
                 f"{n(senaste, 1)} ml/(kg·min){riktning}.")
        L.append("")

    if not har_baslinje:
        L.append(f"Baslinjen bygger på {bas_dagar} dagar och är för tunn för "
                 f"jämförelse. Dygnsvärdena redovisas utan avvikelsemarkering.")
        L.append("")

    # --- dygnstabell ---
    L.append("| Dag | Vilopuls | HRV | Sömn | Steg |")
    L.append("|---|---|---|---|---|")

    avvikelser = {"vilopuls": [], "hrv": [], "somn": [], "steg": []}
    hoga_vp_dagar = []
    dagar_med_data = 0

    for i in range(7):
        d = start + timedelta(days=i)
        if d > date.today():
            break
        key = d.isoformat()
        etikett = f"{DAGAR[d.weekday()]} {d.day}/{d.month}"

        v = serier["resting_heart_rate"].get(key)
        h = serier["heart_rate_variability"].get(key)
        s = serier["sleep_analysis"].get(key)
        st = serier["step_count"].get(key)
        if any(x is not None for x in (v, h, s, st)):
            dagar_med_data += 1

        v_txt, h_txt, s_txt, st_txt = n(v, 0), n(h, 0), n(s, 1), n(st, 0)

        if har_baslinje and v is not None and v >= baslinje["resting_heart_rate"] + VILOPULS_OVER:
            v_txt += " (!)"
            avvikelser["vilopuls"].append(
                f"{etikett}: {n(v, 0)}, {n(v - baslinje['resting_heart_rate'], 0)} över baslinjen")
            hoga_vp_dagar.append(d)
        if har_baslinje and h is not None and h < baslinje["heart_rate_variability"] * HRV_ANDEL:
            h_txt += " (!)"
            avvikelser["hrv"].append(
                f"{etikett}: {n(h, 0)} ms mot baslinjens {n(baslinje['heart_rate_variability'], 0)}")
        if s is not None and s < SOMN_LAG:
            s_txt += " (!)"
            grad = "mycket kort" if s < SOMN_MYCKET_LAG else "kort"
            avvikelser["somn"].append(f"{etikett}: {grad} natt, {n(s, 1)} h")
        if har_baslinje and st is not None and st < baslinje["step_count"] * STEG_ANDEL:
            st_txt += " (!)"
            avvikelser["steg"].append(
                f"{etikett}: {n(st, 0)} steg mot baslinjens {n(baslinje['step_count'], 0)}")

        L.append(f"| {etikett} | {v_txt} | {h_txt} | {s_txt} | {st_txt} |")

    L.append("")

    # Tre dygn i rad med förhöjd vilopuls är ett eget mönster.
    i_rad = any((hoga_vp_dagar[i + 2] - hoga_vp_dagar[i]).days == 2
                for i in range(len(hoga_vp_dagar) - 2))

    # --- en rad per mätvärde, avvikelse eller uttryckligt lugnt besked ---
    L.append("**Avvikelser**")
    L.append("")

    def besked(nyckel, namn, lugnt):
        if avvikelser[nyckel]:
            L.append(f"- **{namn}:** " + "; ".join(avvikelser[nyckel]))
        else:
            L.append(f"- **{namn}:** {lugnt}")

    sv = [x for x in serier["sleep_analysis"].values() if x is not None]
    vv = [x for x in serier["resting_heart_rate"].values() if x is not None]
    hv = [x for x in serier["heart_rate_variability"].values() if x is not None]
    stv = [x for x in serier["step_count"].values() if x is not None]

    besked("vilopuls", "Vilopuls",
           f"jämn, {n(min(vv), 0)}-{n(max(vv), 0)} slag, inget dygn över baslinjen"
           if vv else "ingen data")
    besked("hrv", "HRV",
           f"jämn, {n(min(hv), 0)}-{n(max(hv), 0)} ms, inget dygn under baslinjen"
           if hv else "ingen data")
    besked("somn", "Sömn",
           f"jämn, {n(min(sv), 1)}-{n(max(sv), 1)} h per natt, ingen natt under "
           f"{n(SOMN_LAG, 1)} h" if sv else "ingen data")
    besked("steg", "Steg",
           f"jämn, {n(min(stv), 0)}-{n(max(stv), 0)} per dag" if stv else "ingen data")

    if i_rad:
        L.append("- **Mönster:** vilopulsen låg förhöjd tre dygn i rad eller mer.")
    L.append("")

    # Skilj på nätter som kan komma i efterhand och nätter där klockan
    # helt enkelt inte var på - annars förklarar rapporten fel sak.
    idag = date.today()
    saknas, kan_droja = [], []
    for i in range(7):
        d = start + timedelta(days=i)
        if d > idag or d.isoformat() in serier["sleep_analysis"]:
            continue
        etikett = f"{DAGAR[d.weekday()]} {d.day}/{d.month}"
        (kan_droja if (idag - d).days <= 1 else saknas).append(etikett)

    if saknas:
        L.append(f"Ingen sömnregistrering för {', '.join(saknas)}. Nätterna "
                 f"ligger mer än ett dygn tillbaka, så de kommer inte i "
                 f"efterhand - klockan var sannolikt inte på.")
        L.append("")
    if kan_droja:
        L.append(f"Sömndata ännu inte inskriven för {', '.join(kan_droja)}. "
                 f"Health skriver in sömn med fördröjning.")
        L.append("")

    return L


# ---------- rapport ----------

def build_report(conn, start: date, end: date) -> str:
    iso_year, iso_week, _ = start.isocalendar()
    prev_start, prev_end = start - timedelta(days=7), end - timedelta(days=7)
    base_start = start - timedelta(days=28)
    idag = date.today()

    nu = totals(conn, start, end)
    fg = totals(conn, prev_start, prev_end)
    bas_veckor = veckor_med_data(conn, start)
    visa_4v = bas_veckor >= 4

    snitt4 = {}
    if visa_4v:
        snitt4 = {r["sport_code"]: dict(r) for r in conn.execute(
            """SELECT sport_code, SUM(duration_s)/4.0 sek
               FROM v_workout WHERE local_date BETWEEN ? AND ?
               GROUP BY sport_code""",
            (base_start.isoformat(), prev_end.isoformat()))}

    L = []
    L.append(f"# Träningsvecka {iso_year}-v{iso_week:02d}")
    L.append(f"{start.isoformat()} till {end.isoformat()}")
    L.append("")

    # Komplett eller pågående vecka
    if end < idag:
        L.append("Veckan är komplett.")
    else:
        sista = min(end, idag)
        dagar = [DAGAR[(start + timedelta(days=i)).weekday()]
                 for i in range((sista - start).days + 1)]
        L.append(f"**Veckan är pågående.** Rapporten omfattar {', '.join(dagar)} "
                 f"({start.isoformat()} till {sista.isoformat()}), inte hela veckan.")
    L.append("")
    L.append("Underlag för uppföljning mot träningsplan. Alla värden är "
             "uppmätta av Apple Watch och oredigerade.")
    L.append("")

    # --- översikt ---
    L.append("## Volym per sport")
    L.append("")
    if visa_4v:
        L.append("| Sport | Pass | Tid | Mot fg v | Mot 4v-snitt | Distans | Snittpuls |")
        L.append("|---|---|---|---|---|---|---|")
    else:
        L.append("| Sport | Pass | Tid | Mot fg v | Distans | Snittpuls |")
        L.append("|---|---|---|---|---|---|")

    for code, t in nu.items():
        p = fg.get(code, {})
        rad = [t["sport_name"], str(t["pass"]), hms(t["sek"]),
               delta(t["sek"] / 3600, (p.get("sek") or 0) / 3600, " h")]
        if visa_4v:
            s4 = snitt4.get(code, {})
            rad.append(delta(t["sek"] / 3600, (s4.get("sek") or 0) / 3600, " h"))
        rad += [f"{n((t['m'] or 0) / 1000, 2)} km", n(t["puls"], 0)]
        L.append("| " + " | ".join(rad) + " |")

    tot_s = sum(t["sek"] or 0 for t in nu.values())
    tot_p = sum(t["pass"] for t in nu.values())
    tot_m = sum(t["m"] or 0 for t in nu.values())
    fg_s = sum(t["sek"] or 0 for t in fg.values())

    rad = ["**Totalt**", f"**{tot_p}**", f"**{hms(tot_s)}**",
           delta(tot_s / 3600, fg_s / 3600, " h")]
    if visa_4v:
        rad.append("")
    rad += [f"{n(tot_m / 1000, 2)} km", ""]
    L.append("| " + " | ".join(rad) + " |")
    L.append("")

    if not visa_4v:
        L.append(f"Fyraveckorsjämförelsen utelämnad: bara {bas_veckor} av de "
                 f"fyra föregående veckorna har data i databasen.")
        L.append("")

    borta = [fg[c]["sport_name"] for c in fg if c not in nu]
    if borta:
        L.append(f"Sporter som förekom föregående vecka men inte denna: "
                 f"{', '.join(borta)}.")
        L.append("")

    # --- pulszoner ---
    L += zone_section(conn, start, end)

    # --- hälsodata ---
    L += health_section(conn, start, end)

    # --- pass för pass ---
    L.append("## Pass")
    L.append("")
    for w in workouts(conn, start, end):
        d = datetime.strptime(w["local_date"], "%Y-%m-%d").date()
        L.append(f"### {DAGAR[d.weekday()]} {w['local_date']} - {w['sport_name']}")
        rad = [f"Tid {hms(w['duration_s'])}"]
        # Aktiv tid och väggklockstid skiljer sig vid pauser. Splitsen mäts
        # i väggklocka, snitttempot i aktiv tid - visa båda så att de går
        # att jämföra.
        if (w["elapsed_s"] and w["duration_s"]
                and abs(w["elapsed_s"] - w["duration_s"]) > w["duration_s"] * 0.02):
            rad.append(f"total tid {hms(w['elapsed_s'])} inkl. {hms(w['elapsed_s'] - w['duration_s'])} paus")
        if w["distance_m"]:
            rad.append(f"distans {n(w['distance_m'] / 1000, 2)} km")
        if w["hr_avg"]:
            rad.append(f"puls {w['hr_avg']}/{w['hr_max']}")
        if w["kcal_active"]:
            rad.append(f"{n(w['kcal_active'], 0)} kcal")
        L.append(", ".join(rad))

        zoner = zones_for(conn, w["id"])
        if zoner:
            i_ordning = sorted(zoner.items(), key=lambda kv: kv[0])
            zontext = " · ".join(f"{z} {hms(s)}" for z, s in i_ordning if s >= 1)
            tackt = sum(zoner.values())
            if w["duration_s"] and tackt < w["duration_s"] * 0.95:
                zontext += (f" (täcker {n(100.0 * tackt / w['duration_s'], 0)} % "
                            f"av passet, resten saknar pulsvärde)")
            L.append("Zoner: " + zontext)

        det = detail(conn, w["id"], w["sport_code"])
        segs = segments(conn, w["id"])

        if det and w["sport_code"].startswith("run"):
            extra = []
            if det["pace_s_per_km"]:
                extra.append(f"snitttempo {mmss(det['pace_s_per_km'])}/km")
            if det["elevation_gain_m"]:
                extra.append(f"höjdmeter {n(det['elevation_gain_m'], 0)}")
            if det["cadence_avg_spm"]:
                extra.append(f"kadens {n(det['cadence_avg_spm'], 0)} spm")
            if det["stride_length_m"]:
                extra.append(f"steglängd {n(det['stride_length_m'], 2)} m")
            if det["ground_contact_ms"]:
                extra.append(f"marktid {n(det['ground_contact_ms'], 0)} ms")
            if det["power_avg_w"]:
                extra.append(f"effekt {n(det['power_avg_w'], 0)} W")
            if extra:
                L.append(", ".join(extra))

            # Blocken mellan timer-pauserna är passets verkliga struktur.
            # Kilometervarven är ett raster klockan lägger på, och skär
            # rakt igenom den.
            block = [s for s in segs if s["kind"] == "interval"]
            if block and not blockdata.palitliga(block, w["distance_m"], w["elapsed_s"]):
                L.append("")
                L.append(blockdata.OPALITLIG)
                block = []
            visade = blockdata.sla_ihop(block)
            if len(visade) < 2:
                block = []
            if block:
                L.append("")
                L.append("**Block mellan pauser** - så passet faktiskt kördes")
                L.append("")
                L.append("| Block | Distans | Tid | Tempo | Puls | Max |")
                L.append("|---|---|---|---|---|---|")
                for i, s in enumerate(visade, 1):
                    dist = s["distance_m"] or 0
                    tempo = (s["duration_s"] / (dist / 1000.0)
                             if dist > 50 and s["duration_s"] else None)
                    dist_txt = (f"{n(dist / 1000, 2)} km" if dist >= 990
                                else f"{n(dist, 0)} m" if dist else "-")
                    L.append(f"| {i}{'*' if s['sammanslagen'] else ''} | {dist_txt} "
                             f"| {hms(s['duration_s'])} "
                             f"| {mmss(tempo)} | {s['hr_avg'] or '-'} "
                             f"| {s['hr_max'] or '-'} |")
                if any(s["sammanslagen"] for s in visade):
                    L.append("")
                    L.append(blockdata.FOTNOT)
                paus = (w["elapsed_s"] or 0) - sum(s["duration_s"] or 0 for s in block)
                if paus > 30:
                    L.append("")
                    L.append(f"Mellan blocken: {hms(paus)} med klockan pausad.")

            varv = [s for s in segs if s["kind"] == "split"]
            if varv:
                # Varven är dina egna varvtryck bara om klockan säger det.
                # Är de distanstriggade är det ett raster, inte struktur,
                # och rubriken ska inte antyda något annat.
                triggers = set()
                for s in varv:
                    ex = json.loads(s["extra"]) if s["extra"] else {}
                    if ex.get("lap_trigger"):
                        triggers.add(ex["lap_trigger"])
                auto = triggers and triggers <= {"distance", "time"}
                rubrik = "Km" if auto else "Varv"

                L.append("")
                if block:
                    L.append(f"**{'Kilometermarkeringar' if auto else 'Varv'}** - "
                             + ("klockans automatiska raster, spänner över pauserna"
                                if auto else "dina egna varvtryck"))
                    L.append("")
                L.append(f"| {rubrik} | Distans | Tid | Tempo | Puls | Max |")
                L.append("|---|---|---|---|---|---|")
                for i, s in enumerate(varv, 1):
                    dist = s["distance_m"] or 0
                    # Tempot räknas här i stället för att läsas ur ett fält
                    # som bara den gamla HAE-parsern fyller.
                    tempo = (s["duration_s"] / (dist / 1000.0)
                             if dist > 50 and s["duration_s"] else None)
                    dist_txt = (f"{n(dist / 1000, 2)} km" if dist >= 990
                                else f"{n(dist, 0)} m")
                    L.append(f"| {i} | {dist_txt} | {hms(s['duration_s'])} "
                             f"| {mmss(tempo)} | {s['hr_avg'] or '-'} "
                             f"| {s['hr_max'] or '-'} |")

                # Varven mäter aktiv tid, inte väggklocka. Jämförelsen ska
                # därför gå mot duration_s - annars varnar kontrollen för
                # korrekt data så fort passet haft en paus.
                paus_s = (w["elapsed_s"] or 0) - (w["duration_s"] or 0)
                if auto and paus_s > 60:
                    L.append("")
                    L.append(f"Passet pausades {hms(paus_s)} totalt. Kilometer"
                             f"markeringarna sätts på tillryggalagd sträcka medan "
                             f"klockan mäter tid bara när den går, så sträcka som "
                             f"hinner tillgodoräknas under en paus hamnar i nästa "
                             f"aktiva kilometer. Tempot per kilometer är därför "
                             f"inte tillförlitligt för det här passet - läs "
                             f"blocktabellen i stället.")

                summa = sum(s["duration_s"] or 0 for s in varv)
                ref = w["duration_s"]
                if ref and abs(summa - ref) > ref * 0.02:
                    L.append("")
                    L.append(f"Obs: varven summerar till {hms(summa)} mot passets "
                             f"aktiva tid {hms(ref)}. Avvikelsen är inte förklarad "
                             f"av paus och bör kontrolleras.")

                # Handledsmätare spikar ofta de första minuterna innan de
                # låser på. Syns passets maxpuls bara i första segmentet,
                # och ligger långt över dess snitt, är det sannolikt brus.
                forsta = (block or varv)[0]
                if (w["hr_max"] and forsta["hr_max"] == w["hr_max"]
                        and forsta["hr_avg"]
                        and forsta["hr_max"] - forsta["hr_avg"] > 25):
                    L.append("")
                    L.append(f"Passets maxpuls {w['hr_max']} nåddes i första "
                             f"segmentet, där snittet var {forsta['hr_avg']}. "
                             f"Optisk pulsmätning spikar ofta innan sensorn "
                             f"låst på - värdet kan vara en artefakt, och i så "
                             f"fall är också zontiden i Z4-Z5 överskattad.")

        elif det and w["sport_code"].startswith("swim"):
            har_fit = det["active_s"] is not None

            # Längsta sammanhängande sträcka först - det är passets
            # viktigaste tal och ska inte gå att förväxla med totaldistans.
            if det["longest_continuous_m"]:
                L.append(f"**Längsta sammanhängande: "
                         f"{n(det['longest_continuous_m'], 0)} m** "
                         f"av totalt {n(w['distance_m'], 0)} m")

            extra = []
            if det["pool_length_m"]:
                extra.append(f"bana {n(det['pool_length_m'], 0)} m")
            if det["length_count"]:
                extra.append(f"{det['length_count']} längder")
            if det["freestyle_share"] is not None:
                extra.append(f"{n(det['freestyle_share'] * 100, 0)} % frisim")
            if extra:
                L.append(", ".join(extra))

            # Simtid, vila och totaltid skrivs ut tillsammans. De summerar
            # inte exakt: tid inne i varven före första och efter sista
            # längden mäts inte som någondera.
            tider = []
            if det["active_s"]:
                tider.append(f"simtid {hms(det['active_s'])}")
            if det["rest_s_total"]:
                tider.append(f"vila {hms(det['rest_s_total'])}")
            tider.append(f"totaltid {hms(w['duration_s'])}")
            L.append(", ".join(tider))

            nyckeltal = []
            if det["pace_s_per_100m"]:
                nyckeltal.append(f"{mmss(det['pace_s_per_100m'])}/100 m "
                                 f"räknat på simtiden")
            if det["strokes_per_length_avg"]:
                nyckeltal.append(f"{n(det['strokes_per_length_avg'])} armtag/längd")
            if det["swolf_avg"]:
                best = (f", bästa {n(det['swolf_best'], 0)}"
                        if det["swolf_best"] else "")
                nyckeltal.append(f"SWOLF {n(det['swolf_avg'], 0)} i snitt{best}")
            if nyckeltal:
                L.append(", ".join(nyckeltal))

            # Varv och simlängder ligger i samma tabell, längderna hängda
            # under sitt varv via parent_id. Bara varvnivån ska listas -
            # annars räknas varje meter två gånger.
            sets = [s for s in segs if s["parent_id"] is None]
            if sets:
                L.append("")
                L.append("| Set | Distans | Tid | Tempo/100 m | Puls | Stil |")
                L.append("|---|---|---|---|---|---|")
                for i, s in enumerate(sets, 1):
                    ex = json.loads(s["extra"]) if s["extra"] else {}
                    dist = s["distance_m"] or 0
                    if s["kind"] == "rest" or dist <= 0:
                        # Ett kort avbrott är en vändning, inte vila - och
                        # bryter inte den sammanhängande sträckan.
                        vad = ("vändning"
                               if (s["duration_s"] or 0) < VANDNING_MAX_S else "vila")
                        L.append(f"| {i} | {vad} | {hms(s['duration_s'])} | | "
                                 f"{s['hr_avg'] or '-'} | |")
                        continue
                    # Tempot räknas här, inte ur segmentets extra-fält:
                    # det fylls bara av den gamla HAE-parsern.
                    tempo = (s["duration_s"] / (dist / 100.0)
                             if s["duration_s"] else None)
                    L.append(f"| {i} | {n(dist, 0)} m | {hms(s['duration_s'])} "
                             f"| {mmss(tempo)} | {s['hr_avg'] or '-'} "
                             f"| {ex.get('swim_stroke') or '-'} |")

                if not har_fit:
                    L.append("")
                    L.append("Set härledda ur minutupplösta mätvärden. Vila "
                             "kortare än en minut syns inte, så enskilda set "
                             "kan vara sammanslagna och tempot innehålla vila.")
        L.append("")

    # --- trend ---
    L.append("## Volym senaste tolv veckorna")
    L.append("")
    L.append("| Vecka | Total tid | Löpning | Simning | Cykel | Övrigt |")
    L.append("|---|---|---|---|---|---|")
    for i in range(11, -1, -1):
        ws, we = start - timedelta(days=7 * i), end - timedelta(days=7 * i)
        t = totals(conn, ws, we)
        if not t:
            continue
        grupp = {"run": 0, "swim": 0, "bike": 0, "other": 0}
        for code, v in t.items():
            key = ("run" if code.startswith("run") else
                   "swim" if code.startswith("swim") else
                   "bike" if code.startswith("bike") else "other")
            grupp[key] += v["sek"] or 0
        _, wk, _ = ws.isocalendar()
        L.append(f"| v{wk:02d} | {hms(sum(grupp.values()))} | {hms(grupp['run'])} "
                 f"| {hms(grupp['swim'])} | {hms(grupp['bike'])} | {hms(grupp['other'])} |")
    L.append("")

    return "\n".join(L)


# ---------- utskick ----------

def telegram(method, fields, files=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    boundary = "----traning" + datetime.now().strftime("%H%M%S%f")
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                 f"{v}\r\n").encode()
    for k, (fname, content) in (files or {}).items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                 f"filename=\"{fname}\"\r\nContent-Type: text/markdown\r\n\r\n").encode()
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    if not result.get("ok"):
        raise RuntimeError(f"Telegram svarade: {result}")


def main():
    anchor = (date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1
              else date.today() - timedelta(days=1))
    start, end = week_bounds(anchor)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    report = build_report(conn, start, end)
    conn.close()

    iso_year, iso_week, _ = start.isocalendar()
    path = OUT_DIR / f"vecka-{iso_year}-{iso_week:02d}.md"
    path.write_text(report, encoding="utf-8")
    logger.info("Skrev %s (%s tecken)", path, len(report))

    if TOKEN and CHAT_ID:
        try:
            telegram("sendDocument",
                     {"chat_id": CHAT_ID,
                      "caption": f"Veckounderlag {iso_year}-v{iso_week:02d}"},
                     {"document": (path.name, report.encode("utf-8"))})
            logger.info("Skickade veckorapport till Telegram")
        except Exception as exc:
            logger.error("Kunde inte skicka veckorapport: %s", exc)


if __name__ == "__main__":
    main()
