"""
Ett pass FIT-status, avgjord på ett ställe (D). Vakthundens larm och
rapportens märkning läser samma status - annars kan larmet tystna medan
rapporten fortsätter visa passet som om det vore komplett.

HealthFit laddar upp FIT-filen direkt efter passet. Saknas den efter ett
dygn har exporten fallit bort och passet har bara HAE:s minutdata. Larmet
upprepas i två veckor; sedan räknas passet som förlorat för FIT.
"""
from datetime import datetime, timedelta, timezone

FIT = "fit"
VANTAR = "vantar"      # under 24 h - FIT-filen kan fortfarande komma
SAKNAS = "saknas"      # 24 h-14 dygn - vakthunden larmar
FORLORAD = "forlorad"  # äldre - larmet har tystnat, passet förblir HAE-pass

SAKNAS_EFTER = timedelta(hours=24)
FORLORAD_EFTER = timedelta(days=14)


def fit_status(source_code, fit_enriched, started_at, now=None):
    """FIT, VANTAR, SAKNAS, FORLORAD - eller None för pass som aldrig får
    någon FIT-fil (manuellt loggade)."""
    if source_code == "healthfit_fit" or fit_enriched:
        return FIT
    if source_code != "apple_health_hae":
        return None
    now = now or datetime.now(timezone.utc)
    alder = now - datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if alder < SAKNAS_EFTER:
        return VANTAR
    if alder < FORLORAD_EFTER:
        return SAKNAS
    return FORLORAD


LANGDER_SAKNAS = ("Längder saknas i källfilen - mått per längd (längsta sträcka, "
                  "SWOLF, armtag) går inte att räkna.")
LANGDER_VANTAR = "Längder saknas än så länge - FIT-filen har inte kommit."
OPPET_VATTEN = ("Öppet vatten - det finns inga längder att mäta, så mått per längd "
                "finns inte.")


def har_langder(conn, workout_id):
    """True om passet har riktiga längder. HAE ger bara minutdata, så dess
    längdantal är distans delat med bana - det räknas inte."""
    return conn.execute(
        "SELECT 1 FROM workout_segment WHERE workout_id=? AND kind='length' LIMIT 1",
        (workout_id,)).fetchone() is not None


def simmarkering(conn, w, now=None):
    """
    Raden både sammanfattningen och rapporten visar för ett simpass utan
    längder, eller None. w är en rad ur v_workout. Gäller FIT-filer utan
    längder (pass 76) och HAE-pass lika: måtten ska inte bara saknas tyst.
    """
    if not w["sport_code"].startswith("swim"):
        return None
    if w["sport_code"] == "swim_open":
        return OPPET_VATTEN
    if har_langder(conn, w["id"]):
        return None
    status = fit_status(w["source_code"], w["fit_enriched"], w["started_at"], now)
    if status is None:
        return None
    return LANGDER_VANTAR if status == VANTAR else LANGDER_SAKNAS


def markering(status, started_at=None):
    """Raden rapporten visar för ett pass utan FIT-data, eller None."""
    if status == VANTAR:
        return "Bara HAE-data än så länge - FIT-filen har inte kommit."
    if status == SAKNAS:
        slut = ""
        if started_at:
            t = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ") + FORLORAD_EFTER
            slut = f" Vakthunden larmar till och med {t:%Y-%m-%d}."
        return ("**HAE-pass:** FIT-filen saknas, så varv och set är härledda ur "
                "minutdata." + slut)
    if status == FORLORAD:
        return ("**HAE-pass:** FIT-filen kom aldrig, så varv och set är härledda "
                "ur minutdata.")
    return None
