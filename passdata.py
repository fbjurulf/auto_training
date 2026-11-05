"""
Värden som både sammanfattningen (notify) och veckorapporten (weekly) visar
- räknade på ett ställe (D). Formateringen får skilja sig mellan dem,
beräkningarna inte. tests/test_samma_tal.py kontrollerar att de två ger
samma tal för samma pass.
"""
from datetime import timedelta

TEMPO_MIN_M = 50          # kortare segment ger inget meningsfullt tempo

# Simning: ett uppehåll mellan två aktiva längder som är kortare än detta är
# en vändning, inte ett stopp. Gränsen bor bara här. Den avgör både var
# längsta sammanhängande sträcka bryts (A6) och vilka set som får fotnoten
# om avbrott (D1) - så de två kan aldrig säga olika saker om samma paus.
VILA_MIN_S = 12
# A6: det andra måttet, längsta sträcka där stopp under detta inte bryter.
# Blir stort på intervallpass med 20 s vila - det är okej, gissa inte vilka
# stopp som är vila.
KORTA_STOPP_S = 30


def uppehall(langder):
    """
    Uppehållen mellan på varandra följande aktiva längder som
    [(slut på förra, start på nästa, sekunder), ...].
    langder: [(start datetime, varaktighet_s, aktiv bool), ...] i ordning.
    Vilolängder däremellan ingår i uppehållet - det är tiden från slutet av
    en aktiv längd till början av nästa.
    """
    aktiva = [(t, d or 0) for t, d, a in langder if a and t is not None]
    ut = []
    for a, b in zip(aktiva, aktiva[1:]):
        slut = a[0] + timedelta(seconds=a[1])
        ut.append((slut, b[0], (b[0] - slut).total_seconds()))
    return ut


def pauser(langder):
    """Uppehållens längd i sekunder."""
    return [s for _, _, s in uppehall(langder)]


def ar_stopp(sekunder, grans=None):
    return sekunder >= (VILA_MIN_S if grans is None else grans)


def stopp_vid(langder, t0, t1, grans=None):
    """
    True om uppehållet som omsluter tidsfönstret [t0, t1] - ett vilovarv
    mellan två set - är ett stopp. None om inget uppehåll täcker fönstret.
    """
    for slut, start, sek in uppehall(langder):
        if slut <= t1 and start >= t0:
            return ar_stopp(sek, grans)
    return None


def langsta_svit(langder, grans=None):
    """Flest aktiva längder i följd utan ett stopp på minst gränsen."""
    grans = VILA_MIN_S if grans is None else grans
    if not any(a for _, _, a in langder):
        return 0
    langst = rad = 1
    for p in pauser(langder):
        rad = 1 if ar_stopp(p, grans) else rad + 1
        langst = max(langst, rad)
    return langst


def avbrott(langder, grans=None):
    """Summan av stopp på minst gränsen mellan aktiva längder - inom ett set."""
    grans = VILA_MIN_S if grans is None else grans
    return sum(p for p in pauser(langder) if ar_stopp(p, grans))


def kanttid(varv_start, varv_s, langder, grans=None):
    """
    (före start, efter sista längden) i ett set, i sekunder - var för sig
    bara om den når gränsen, annars 0. Tid vid väggen före första
    frånskjutet är en sen start, inte ett brott i simningen: den kan inte
    bryta en sammanhängande sträcka, för det finns inget före den. Därför
    hålls den isär från avbrott.
    """
    aktiva = [(t, d or 0) for t, d, a in langder if a and t is not None]
    if not aktiva or varv_start is None:
        return 0, 0
    fore = (aktiva[0][0] - varv_start).total_seconds()
    slut = varv_start + timedelta(seconds=varv_s or 0)
    efter = (slut - (aktiva[-1][0] + timedelta(seconds=aktiva[-1][1]))).total_seconds()
    return (fore if ar_stopp(fore, grans) else 0,
            efter if ar_stopp(efter, grans) else 0)


TACKNING_VISA = 0.95      # pulstäckning under detta skrivs ut per pass


def tempo_per_km(duration_s, distance_m):
    """Sekunder per kilometer, eller None för segment utan distans."""
    if not duration_s or not distance_m or distance_m <= TEMPO_MIN_M:
        return None
    return duration_s / (distance_m / 1000.0)


def fart_kmh(duration_s, distance_m):
    """Kilometer i timmen, eller None för segment utan distans."""
    if not duration_s or not distance_m or distance_m <= TEMPO_MIN_M:
        return None
    return distance_m / duration_s * 3.6


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
