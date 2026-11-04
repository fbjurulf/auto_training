"""
Värden som både sammanfattningen (notify) och veckorapporten (weekly) visar
- räknade på ett ställe (D). Formateringen får skilja sig mellan dem,
beräkningarna inte. tests/test_samma_tal.py kontrollerar att de två ger
samma tal för samma pass.
"""

TEMPO_MIN_M = 50          # kortare segment ger inget meningsfullt tempo
TACKNING_VISA = 0.95      # pulstäckning under detta skrivs ut per pass


def tempo_per_km(duration_s, distance_m):
    """Sekunder per kilometer, eller None för segment utan distans."""
    if not duration_s or not distance_m or distance_m <= TEMPO_MIN_M:
        return None
    return duration_s / (distance_m / 1000.0)


def tempo_per_100m(duration_s, distance_m):
    """Sekunder per 100 m, eller None."""
    if not duration_s or not distance_m or distance_m <= 0:
        return None
    return duration_s / (distance_m / 100.0)


def pulstackning(zon_sekunder, duration_s):
    """
    Andel av passets aktiva tid som har ett pulsvärde, 0-1, eller None när
    passet saknar zondata. A7a kommer att räkna mot passets tid minus de
    första 90 sekunderna - det ändras här, på ett ställe.
    """
    if not zon_sekunder or not duration_s:
        return None
    return sum(zon_sekunder) / duration_s


def visa_tackning(andel):
    """True när täckningen är så låg att den ska skrivas ut."""
    return andel is not None and andel < TACKNING_VISA
