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
import fitstatus
import hojd
import passdata

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
DAGAR_LANGA = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]

# (dag, text): visas överst i rapporten för veckan som innehåller dagen.
DEFINITIONSBYTEN = [
    (date(2026, 11, 4),
     "Simtempot räknas från och med denna vecka på setens tid med vändningarna "
     "inräknade, och tid stillastående vid väggen före start räknas bort. Alla "
     "simpass är omräknade. Äldre rapporter visar ett snabbare tempo enligt den "
     "gamla definitionen - till exempel 2:03 per 100 m för 2026-10-21, som nu "
     "är 2:12. Det är en ändrad beräkning, inte en förändring i simningen."),
    (date(2026, 11, 5),
     "Andelen lugn träning räknas från och med denna vecka bara på löpning, "
     "cykling och simning. Vandring, styrka och andra sporter står kvar i "
     "volymtabellen och zontabellen men ingår inte i procenten. Pass utan "
     "zondata räknas nu som tid utan puls i stället för att falla bort. Äldre "
     "rapporter räknade på allt och är inte jämförbara."),
]

# Gränser för vad som räknas som avvikande dygn.
SOMN_LAG = 6.0          # timmar - under detta flaggas natten
SOMN_MYCKET_LAG = 5.0
VILOPULS_OVER = 5       # slag över 28-dagarsmedianen
HRV_ANDEL = 0.75        # andel av medianen - under detta flaggas dygnet
STEG_ANDEL = 0.40       # andel av medianen - under detta är dagen påfallande stillasittande
BASLINJE_MIN_DAGAR = 10 # färre än så: ingen jämförelse alls
LUGNA_ZONER = ("Z1-Z2",)  # zoner som räknas som lugn träning i 80/20-måttet
ZON_MAL = 80            # andel av träningstiden som bör ligga lugnt


def _nu():
    """Lokal tid just nu - rapportens sluttidpunkt om inget annat anges."""
    return datetime.now()


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

def _hm(m):
    return f"{n(m, 0)} m" if m is not None else "-"


def week_bounds(anchor: date):
    monday = anchor - timedelta(days=anchor.weekday())
    return monday, monday + timedelta(days=6)


def totals(conn, start: date, end: date):
    rows = conn.execute(
        """SELECT sport_code, sport_name, COUNT(*) pass,
                  SUM(duration_s) sek, SUM(distance_m) m,
                  AVG(hr_avg) puls, SUM(kcal_active) kcal,
                  SUM(ascent_m) upp, SUM(descent_m) ner
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
    Tid i pulszon per vecka, per sport. Andelen lugn träning står först -
    det är måttet som gör 80/20 verifierbart. Den räknas bara på sporter med
    sport.counts_in_intensity_ratio (A4); övriga visas i tabellen utanför.
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
    # Från passen, inte från zonraderna: ett pass utan zoner ska synas som tid
    # utan puls, inte falla ur urvalet.
    passen = conn.execute(
        """SELECT v.id, v.local_date, v.sport_code, v.sport_name, v.duration_s,
                  s.counts_in_intensity_ratio ingar,
                  EXISTS (SELECT 1 FROM workout_zone z WHERE z.workout_id = v.id) har_zoner
           FROM v_workout v JOIN sport s ON s.id = v.sport_id
           WHERE v.local_date BETWEEN ? AND ? ORDER BY v.started_at""",
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

    tid_per_sport = defaultdict(int)
    for p in passen:
        tid_per_sport[p["sport_code"], p["sport_name"], p["ingar"]] += p["duration_s"]
    def tider(ingar):
        return sorted(((sek, namn_.lower()) for (_, namn_, i), sek in tid_per_sport.items()
                       if i == ingar), reverse=True)

    i_andelen = {p["sport_code"] for p in passen if p["ingar"]}
    totalt = defaultdict(float)
    for code in i_andelen & per_sport.keys():
        for z, s in per_sport[code].items():
            totalt[z] += s
    summa = sum(totalt.values())
    lugnt = sum(totalt[z] for z in LUGNA_ZONER if z in totalt)

    # Zontiden byggs av pulsen sekund för sekund. Tappar klockan kontakten
    # finns inget värde att placera i en zon, och underlaget blir mindre än
    # passets tid. Det ska synas - annars ser andelen säkrare ut än den är.
    tranad = sum(sek for sek, _ in tider(1))

    L = ["## Tid i pulszon", ""]
    if summa:
        andel = 100.0 * lugnt / summa
        status = "över" if andel >= ZON_MAL else "under"
        L.append(f"**{n(andel, 0)} % av träningstiden låg lugnt** "
                 f"({'+'.join(LUGNA_ZONER)}), {status} målet på {ZON_MAL} %.")
    else:
        # Ingen siffra hellre än 0 %: veckan har inget att räkna andelen på.
        L.append("**Ingen andel lugn träning för veckan** - ingen zontid i de "
                 "sporter som andelen räknas på.")
    L.append("")

    # Vad andelen bygger på, så att underlaget inte läses som hela veckan.
    utanfor = ", ".join(f"{hms(sek)} {namn_}" for sek, namn_ in tider(0))
    bas = (f"Räknat på {hms(tranad)} " + " och ".join(namn_ for _, namn_ in tider(1))
           + f" av veckans {hms(sum(tid_per_sport.values()))}."
           if tranad else "Inget pass i veckan räknas in i andelen.")
    L.append(bas + (f" Utanför: {utanfor}." if utanfor else ""))
    L.append("")

    utan_zoner = [p for p in passen if p["ingar"] and not p["har_zoner"]]
    if utan_zoner:
        L.append("Utan zondata, räknas som tid utan puls: " + ", ".join(
            f"{p['local_date']} {p['sport_name']} (pass {p['id']})" for p in utan_zoner) + ".")
        L.append("")

    if summa and summa < tranad * 0.995:
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
    i_ordning = sorted(per_sport.items(), key=lambda kv: -sum(kv[1].values()))
    for code, sport in i_ordning:
        if code not in i_andelen:
            continue
        s_summa = sum(sport.values())
        s_lugnt = sum(sport.get(z, 0) for z in LUGNA_ZONER)
        celler = [hms(sport.get(z, 0)) if sport.get(z) else "-" for z in zoner]
        L.append(f"| {namn[code]} | " + " | ".join(celler) +
                 f" | {n(100.0 * s_lugnt / s_summa, 0)} % |")
    if summa:
        celler = [hms(totalt.get(z, 0)) if totalt.get(z) else "-" for z in zoner]
        L.append(f"| **I andelen** | " + " | ".join(celler) + f" | **{n(andel, 0)} %** |")
    for code, sport in i_ordning:
        if code in i_andelen:
            continue
        celler = [hms(sport.get(z, 0)) if sport.get(z) else "-" for z in zoner]
        L.append(f"| {namn[code]} | " + " | ".join(celler) + " | utanför |")
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

def health_section(conn, start: date, end: date, idag: date):
    """
    Hälsodata för veckan: sammanfattning, dygnstabell och en uttrycklig
    rad per mätvärde. Baslinjen är medianen för 28 dagar före veckan -
    medianen används i stället för medelvärdet så att ett enskilt
    extremdygn inte flyttar referensen.

    Avvikelser är poängen. Saknas de sägs det rakt ut, så att tystnad
    inte går att förväxla med utebliven mätning.
    """
    bas_start, bas_slut = start - timedelta(days=28), start - timedelta(days=1)

    serier = {m: metrics_between(conn, m, start, min(end, idag)) for m in
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
        if d > idag:
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

def delvis_vecka(start: date, end: date, slut: datetime) -> bool:
    """Har veckan börjat men inte nått sin sista dag? Söndagen räknas som hel."""
    return start <= slut.date() < end


def delvis_rad(start: date, slut: datetime) -> str:
    """Raden överst i en ofullständig vecka: 'Vecka 45, delvis - måndag till torsdag 13:55.'"""
    dag = DAGAR_LANGA[slut.weekday()]
    spann = (f"{dag} {slut:%H:%M}" if slut.date() == start
             else f"{DAGAR_LANGA[start.weekday()]} till {dag} {slut:%H:%M}")
    return f"Vecka {start.isocalendar()[1]}, delvis - {spann}."


def build_report(conn, start: date, end: date, now=None, slut=None) -> str:
    """
    Veckans rapport, i en enda beräkning för söndagskörningen och /veckorapport.

    slut är rapportens sluttidpunkt i lokal tid, None betyder just nu. Slutar
    den före veckans sista dag står det överst att veckan är ofullständig, och
    passen räknas till och med slutets dag. now är klockan för passens
    FIT-status, som följer verkligheten och inte rapportens slut.
    """
    slut = slut or _nu()
    idag = slut.date()
    delvis = delvis_vecka(start, end, slut)
    sista = idag if delvis else end
    iso_year, iso_week, _ = start.isocalendar()
    prev_start, prev_end = start - timedelta(days=7), end - timedelta(days=7)
    base_start = start - timedelta(days=28)

    nu = totals(conn, start, sista)
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
    # Rapporterna klistras in där träningsplanen följs. En halv vecka som ser
    # ut som en hel läses som att det tränats för lite.
    if delvis:
        L.append(f"**{delvis_rad(start, slut)}**")
        L.append("")
    L.append(f"# Träningsvecka {iso_year}-v{iso_week:02d}")
    L.append(f"{start.isoformat()} till {end.isoformat()}")
    L.append("")

    # När ett mått byter definition ska den första rapporten efteråt säga
    # det högst upp - rapporterna klistras in där träningsplanen följs, och
    # ett hopp i ett tal får inte se ut som en förändring i formen.
    for datum_, text in DEFINITIONSBYTEN:
        if start <= datum_ <= end:
            L.append(f"**Obs:** {text}")
            L.append("")

    if idag >= end:
        L.append("Veckan är komplett.")
        L.append("")
    elif idag < start:
        L.append("Veckan har inte börjat.")
        L.append("")
    L.append("Underlag för uppföljning mot träningsplan. Alla värden är "
             "uppmätta av Apple Watch och oredigerade.")
    L.append("")

    # --- översikt ---
    L.append("## Volym per sport")
    L.append("")
    if visa_4v:
        L.append("| Sport | Pass | Tid | Mot fg v | Mot 4v-snitt | Distans | Upp | Ner | Snittpuls |")
        L.append("|---|---|---|---|---|---|---|---|---|")
    else:
        L.append("| Sport | Pass | Tid | Mot fg v | Distans | Upp | Ner | Snittpuls |")
        L.append("|---|---|---|---|---|---|---|---|")

    for code, t in nu.items():
        p = fg.get(code, {})
        rad = [t["sport_name"], str(t["pass"]), hms(t["sek"]),
               delta(t["sek"] / 3600, (p.get("sek") or 0) / 3600, " h")]
        if visa_4v:
            s4 = snitt4.get(code, {})
            rad.append(delta(t["sek"] / 3600, (s4.get("sek") or 0) / 3600, " h"))
        rad += [f"{n((t['m'] or 0) / 1000, 2)} km", _hm(t["upp"]), _hm(t["ner"]),
                n(t["puls"], 0)]
        L.append("| " + " | ".join(rad) + " |")

    tot_s = sum(t["sek"] or 0 for t in nu.values())
    tot_p = sum(t["pass"] for t in nu.values())
    tot_m = sum(t["m"] or 0 for t in nu.values())
    tot_upp = sum(t["upp"] or 0 for t in nu.values())
    tot_ner = sum(t["ner"] or 0 for t in nu.values())
    har_hojd = any(t["upp"] is not None for t in nu.values())
    fg_s = sum(t["sek"] or 0 for t in fg.values())

    rad = ["**Totalt**", f"**{tot_p}**", f"**{hms(tot_s)}**",
           delta(tot_s / 3600, fg_s / 3600, " h")]
    if visa_4v:
        rad.append("")
    rad += [f"{n(tot_m / 1000, 2)} km", _hm(tot_upp if har_hojd else None),
            _hm(tot_ner if har_hojd else None), ""]
    L.append("| " + " | ".join(rad) + " |")
    L.append("")

    if delvis:
        L.append("Jämförelserna ställer delveckan mot hela veckor, och sporter som "
                 "saknas kan komma senare i veckan.")
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
    L += zone_section(conn, start, sista)

    # --- hälsodata ---
    L += health_section(conn, start, end, idag)

    # --- pass för pass ---
    L.append("## Pass")
    L.append("")
    for w in workouts(conn, start, sista):
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
        hojdtext = hojd.text(w["ascent_m"], w["descent_m"], w["ascent_apple_m"])
        if hojdtext:
            rad.append(hojdtext)
        L.append(", ".join(rad))
        # Samma status som vakthundens larm - när larmet tystnar ska passet
        # fortfarande synas som HAE-pass här.
        markering = fitstatus.markering(
            fitstatus.fit_status(w["source_code"], w["fit_enriched"], w["started_at"], now),
            w["started_at"])
        if markering:
            L.append(markering)
        simmarkering = fitstatus.simmarkering(conn, w, now)
        if simmarkering:
            L.append(simmarkering)

        zoner = zones_for(conn, w["id"])
        if zoner:
            i_ordning = sorted(zoner.items(), key=lambda kv: kv[0])
            zontext = " · ".join(f"{z} {hms(s)}" for z, s in i_ordning if s >= 1)
            andel = passdata.pulstackning(zoner.values(), w["duration_s"])
            if passdata.visa_tackning(andel):
                zontext += (f" (täcker {n(100.0 * andel, 0)} % "
                            f"av passet, resten saknar pulsvärde)")
            L.append("Zoner: " + zontext)

        det = detail(conn, w["id"], w["sport_code"])
        segs = segments(conn, w["id"])

        if det and w["sport_code"].startswith("run"):
            extra = []
            if det["pace_s_per_km"]:
                extra.append(f"snitttempo {mmss(det['pace_s_per_km'])}/km")
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
                    tempo = passdata.tempo_per_km(s["duration_s"], dist)
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
                    # Tempot räknas ur segmentets tid och distans i stället
                    # för att läsas ur ett fält som bara HAE-parsern fyller.
                    tempo = passdata.tempo_per_km(s["duration_s"], dist)
                    dist_txt = (f"{n(dist / 1000, 2)} km" if dist >= 990
                                else f"{n(dist, 0)} m")
                    L.append(f"| {i} | {dist_txt} | {hms(s['duration_s'])} "
                             f"| {mmss(tempo)} | {s['hr_avg'] or '-'} "
                             f"| {s['hr_max'] or '-'} |")

                # Varvens tid går mot aktiv eller förfluten tid beroende på
                # varvtyp (A2a). Referensen väljs ur summan och skrivs ut.
                paus_s = (w["elapsed_s"] or 0) - (w["duration_s"] or 0)
                summa = sum(s["duration_s"] or 0 for s in varv)
                referens, varna, svans = blockdata.varv_kontroll(
                    varv, w["duration_s"], w["elapsed_s"], w["ended_at"])
                if svans:
                    L.append("")
                    L.append(f"Varven täcker inte passets sista "
                             f"{f'{round(svans)} s' if svans < 60 else hms(svans)}.")
                if paus_s > 5:
                    L.append("")
                    L.append(f"Passet pausades {hms(paus_s)} totalt. " + (
                        "Varven räknas på aktiv tid - pauserna ligger utanför varven."
                        if referens == "aktiv" else
                        "Varven räknas på förfluten tid - pauserna ingår i varven."))
                if w["duration_s"] and varna:
                    L.append("")
                    L.append(f"Obs: varven summerar till {hms(summa)} mot passets "
                             f"aktiva tid {hms(w['duration_s'])} och förflutna tid "
                             f"{hms(w['elapsed_s'])}. Avvikelsen förklaras inte av "
                             f"pausen och bör kontrolleras.")

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
            # Utan längder är längdantal, armtag och SWOLF bara distans delat
            # med bana. simmarkering säger redan att de saknas.
            per_langd = fitstatus.har_langder(conn, w["id"])

            # Längsta sammanhängande sträcka först - passets viktigaste tal,
            # och det ska inte gå att förväxla med totaldistans. Två mått
            # (A6): utan stopp, och med korta stopp som inte bryter.
            if det["longest_continuous_m"]:
                rad = (f"**Längsta utan stopp: {n(det['longest_continuous_m'], 0)} m**")
                if det["longest_with_short_stops_m"]:
                    rad += (f" · **längsta med korta stopp: "
                            f"{n(det['longest_with_short_stops_m'], 0)} m**")
                L.append(f"{rad} av totalt {n(w['distance_m'], 0)} m")

            extra = []
            if det["pool_length_m"]:
                extra.append(f"bana {n(det['pool_length_m'], 0)} m")
            if det["length_count"] and per_langd:
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
            if det["strokes_per_length_avg"] and per_langd:
                nyckeltal.append(f"{n(det['strokes_per_length_avg'])} armtag/längd")
            if det["swolf_avg"] and per_langd:
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
                fotnoter = []
                for i, s in enumerate(sets, 1):
                    ex = json.loads(s["extra"]) if s["extra"] else {}
                    dist = s["distance_m"] or 0
                    if s["kind"] == "rest" or dist <= 0:
                        # Stopp eller vändning avgjordes vid tolkningen, med
                        # samma funktion som bryter längsta sammanhängande
                        # sträcka. HAE-set saknar längder och faller tillbaka
                        # på vilans egen tid mot samma gräns.
                        stopp = ex.get("stopp")
                        if stopp is None:
                            stopp = passdata.ar_stopp(s["duration_s"] or 0)
                        L.append(f"| {i} | {'vila' if stopp else 'vändning'} "
                                 f"| {hms(s['duration_s'])} | | {s['hr_avg'] or '-'} | |")
                        continue
                    # Tid är setets tid utan stopp - aktiva längder och
                    # vändningar. Ett stopp inne i setet märks i stället för
                    # att flyttas: ett 100-meterssett med stopp vid väggen är
                    # inte ett sammanhängande 100-meterssett (D1).
                    # Tid före första längden är en sen start och tid efter
                    # sista ett sent stopp - inte avbrott. De får egna ord så
                    # att de inte läses som ett brott i simningen.
                    avbrott = ex.get("avbrott_s") or 0
                    fore = ex.get("fore_start_s") or 0
                    efter = ex.get("efter_sista_s") or 0
                    tid = (s["duration_s"] or 0) - avbrott - fore - efter
                    tempo = passdata.tempo_per_100m(tid, dist)
                    markering = "*" if avbrott or fore or efter else ""
                    for sek, text in ((fore, "före start"), (avbrott, "avbrott inne i setet"),
                                      (efter, "efter sista längden")):
                        if sek:
                            fotnoter.append(f"* Set {i}: {hms(sek)} {text}.")
                    L.append(f"| {i} | {n(dist, 0)} m | {hms(tid)}{markering} "
                             f"| {mmss(tempo)} | {s['hr_avg'] or '-'} "
                             f"| {ex.get('swim_stroke') or '-'} |")
                if fotnoter:
                    L.append("")
                    L.extend(fotnoter)

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
        t = totals(conn, ws, min(we, sista))
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

def rapportfil(start: date, end: date, slut: datetime):
    """(filnamn, rubrik) för rapporten. En delvecka får egen fil och egen rubrik."""
    iso_year, iso_week, _ = start.isocalendar()
    delvis = delvis_vecka(start, end, slut)
    return (f"vecka-{iso_year}-{iso_week:02d}{'-delvis' if delvis else ''}.md",
            f"Veckounderlag {iso_year}-v{iso_week:02d}{', delvis' if delvis else ''}")


def telegram(method, fields, files=None, token=None):
    url = f"https://api.telegram.org/bot{token or TOKEN}/{method}"
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
    slut = _nu()
    anchor = (date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1
              else slut.date() - timedelta(days=1))
    start, end = week_bounds(anchor)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    report = build_report(conn, start, end, slut=slut)
    conn.close()

    filnamn, rubrik = rapportfil(start, end, slut)
    path = OUT_DIR / filnamn
    path.write_text(report, encoding="utf-8")
    logger.info("Skrev %s (%s tecken)", path, len(report))

    if TOKEN and CHAT_ID:
        try:
            telegram("sendDocument",
                     {"chat_id": CHAT_ID, "caption": rubrik},
                     {"document": (path.name, report.encode("utf-8"))})
            logger.info("Skickade veckorapport till Telegram")
        except Exception as exc:
            logger.error("Kunde inte skicka veckorapport: %s", exc)


if __name__ == "__main__":
    main()
