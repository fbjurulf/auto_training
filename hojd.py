"""
Höjdmeter upp och ner, härledda ur höjdserien (del B). Samma funktioner för
FIT (record.enhanced_altitude) och HAE (route[].altitude) - båda är Apples
1 Hz-serie och ger samma resultat inom ett par meter.

Inställningen är kalibrerad 2026-11-04 mot 62 FIT-pass, se kravspec del B:
glidande medel över 11 s, därefter riktningsföljande hysteres utan tröskel.
Utjämningen tar barometerbruset. En tröskel på några meter - också som
hysteres - kastade verkliga små backar: löppassen hamnade på 0,80 av Apples
värde och spridningen mellan upprepningar av samma runda ökade från 17 till
29 %.

Upp och ner kommer ur samma räkning, så upp - ner är höjdskillnaden mellan
start och mål. En runda går därmed att stämma av mot sig själv.
"""
from datetime import timedelta

UTJAMNING_S = 11       # glidande medel, antal sampel vid 1 Hz
HYSTERES_M = 0.0       # vändning som krävs innan en stigning räknas
APPLE_AVVIKELSE = 0.25  # visa Apples värde bredvid när de skiljer mer än så
RECORD_LUCKA_MAX_S = 10  # längre glapp är paus, inte rörelse


def utjamna(hojder, fonster=UTJAMNING_S):
    """Glidande medel, centrerat. Ändarna använder det som finns."""
    if fonster <= 1 or len(hojder) < 2:
        return list(hojder)
    halv = fonster // 2
    summa = [0.0]
    for h in hojder:
        summa.append(summa[-1] + h)
    ut = []
    for i in range(len(hojder)):
        lo, hi = max(0, i - halv), min(len(hojder), i + halv + 1)
        ut.append((summa[hi] - summa[lo]) / (hi - lo))
    return ut


def upp_ner(hojder, troskel=HYSTERES_M):
    """
    Riktningsföljande hysteres: följ toppen (eller dalen) och räkna hela
    stigningen först när en vändning överstiger tröskeln. Sista segmentet
    räknas med, så upp - ner = sista extremvärdet - första värdet.
    """
    if len(hojder) < 2:
        return 0.0, 0.0
    upp = ner = 0.0
    senast = hi = lo = hojder[0]
    nedat = False
    for h in hojder[1:]:
        if not nedat:
            hi = max(hi, h)
            if hi - h > troskel:
                upp += hi - senast
                senast, lo, nedat = hi, h, True
        else:
            lo = min(lo, h)
            if h - lo > troskel:
                ner += senast - lo
                senast, hi, nedat = lo, h, False
    if nedat:
        ner += senast - lo
    else:
        upp += hi - senast
    return upp, ner


class Hojdserie:
    """Utjämnad höjdserie med tidsstämplar. punkter: [(datetime, höjd), ...]."""

    def __init__(self, punkter):
        punkter = sorted((t, h) for t, h in punkter if t is not None and h is not None)
        self.tider = [t for t, _ in punkter]
        self.hojder = utjamna([h for _, h in punkter])

    def __bool__(self):
        return len(self.hojder) >= 2

    def totalt(self):
        return upp_ner(self.hojder) if self else (None, None)

    def inom(self, t0, t1):
        """Upp och ner inom ett tidsfönster, t.ex. ett varv eller en kilometer."""
        if not self:
            return None, None
        del_ = [h for t, h in zip(self.tider, self.hojder) if t0 <= t <= t1]
        return upp_ner(del_) if len(del_) >= 2 else (None, None)


def km_segment(punkter, serie=None):
    """
    Kilometrar ur en distansserie. punkter: [(datetime, distans_m, puls), ...].
    Tiden är rörelsetid - glapp över RECORD_LUCKA_MAX_S är paus och räknas
    inte, så kilometern jämförs mot aktiv tid som varven (A2a). Sista,
    ofullständiga kilometern tas med.
    """
    punkter = sorted(p for p in punkter if p[0] is not None and p[1] is not None)
    ut = []
    if len(punkter) < 2:
        return ut
    start = punkter[0]
    rorelse = 0.0
    pulsar = []
    grans = (int(start[1] // 1000) + 1) * 1000
    for forra, p in zip(punkter, punkter[1:]):
        dt = (p[0] - forra[0]).total_seconds()
        if 0 < dt <= RECORD_LUCKA_MAX_S:
            rorelse += dt
        if p[2]:
            pulsar.append(p[2])
        if p[1] >= grans:
            ut.append(_km(start, p, rorelse, pulsar, serie))
            start, rorelse, pulsar = p, 0.0, []
            grans = (int(p[1] // 1000) + 1) * 1000
    if punkter[-1][1] - start[1] > 0 and punkter[-1][0] > start[0]:
        ut.append(_km(start, punkter[-1], rorelse, pulsar, serie))
    return ut


def _km(start, slut, rorelse, pulsar, serie):
    upp, ner = serie.inom(start[0], slut[0]) if serie else (None, None)
    return {"start": start[0], "duration_s": round(rorelse, 1),
            "distance_m": round(slut[1] - start[1], 1),
            "hr_avg": round(sum(pulsar) / len(pulsar)) if pulsar else None,
            "hr_max": max(pulsar) if pulsar else None,
            "upp": upp, "ner": ner}


def text(upp, ner, apple=None):
    """'upp 120 m, ner 118 m' - och Apples värde bredvid när de skiljer
    mer än 25 %. Grundregeln: en rimlig men fel siffra är värre än två."""
    if upp is None:
        return None
    s = f"upp {upp:.0f} m, ner {ner:.0f} m"
    if apple and abs(upp - apple) > apple * APPLE_AVVIKELSE:
        s += f" (Apple: upp {apple:.0f} m)"
    return s
