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
