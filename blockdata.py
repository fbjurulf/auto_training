"""
Integritetskontroll och sammanslagning för arbetsblocken mellan
timer-pauserna.

Blocken härleds ur timer-händelser, och en enda saknad händelse räcker
för att ett helt block ska försvinna. Därför visas de bara när de går
ihop med passet: distansen ska täcka passets totaldistans, och block
plus pauserna mellan dem ska täcka den förflutna tiden.

A1, 2026-10-22: specen gissade på en paus utan återstart. Den verkliga
orsaken var att sista blocket aldrig stängdes - det avslutas av
session/stop_all, som ignorerades. Filen har pauser på 40, 2 och 9 s;
specens 15 s rödljus var en minnesbild, inte en mätning.
"""
from datetime import datetime, timedelta

TOLERANS = 0.02
OPALITLIG = "Blockdata är opålitliga för passet - blocken går inte ihop med passets distans och tid."

# A7:s regel: ett block utan innehåll slås ihop med nästföljande. Ingen
# tröskel på pausens längd - den skulle gissa varför klockan stoppades.
TOMT_M = 50
TOMT_S = 30
FOTNOT = ("* Innehåller ett block under 50 m eller 30 s som slagits ihop med "
          "nästföljande.")


def _tid(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def palitliga(block, distance_m, elapsed_s):
    """True om blocken (workout_segment-rader) går ihop med passet."""
    if not block or not distance_m or not elapsed_s:
        return False
    dist = sum(b["distance_m"] or 0 for b in block)
    if abs(dist - distance_m) > distance_m * TOLERANS:
        return False
    # Block plus pauserna mellan dem är spannet från första blockets start
    # till sista blockets slut.
    forst = _tid(block[0]["started_at"])
    sist = max(_tid(b["started_at"]) + timedelta(seconds=b["duration_s"] or 0)
               for b in block)
    return abs((sist - forst).total_seconds() - elapsed_s) <= elapsed_s * TOLERANS


def _tomt(b):
    return (b["distance_m"] or 0) < TOMT_M or (b["duration_s"] or 0) < TOMT_S


def sla_ihop(block):
    """
    Block under 50 m eller 30 s slås ihop med nästföljande - eller med
    föregående om det är sist. Ger dict:ar med distance_m, duration_s,
    hr_avg, hr_max och sammanslagen=True där något slagits ihop.
    """
    ut = []
    vantar = None
    for b in block:
        rad = {"distance_m": b["distance_m"] or 0, "duration_s": b["duration_s"] or 0,
               "hr_avg": b["hr_avg"], "hr_max": b["hr_max"], "sammanslagen": False}
        if vantar:
            rad = _sammanfoga(vantar, rad)
            vantar = None
        if _tomt(rad):
            vantar = rad
        else:
            ut.append(rad)
    if vantar:
        if ut:
            ut[-1] = _sammanfoga(ut[-1], vantar)
        else:
            ut.append(vantar)
    return ut


def _sammanfoga(a, b):
    tid = a["duration_s"] + b["duration_s"]
    pulsar = [(x["hr_avg"], x["duration_s"]) for x in (a, b) if x["hr_avg"]]
    vikt = sum(t for _, t in pulsar)
    maxar = [x["hr_max"] for x in (a, b) if x["hr_max"]]
    return {"distance_m": a["distance_m"] + b["distance_m"], "duration_s": tid,
            "hr_avg": round(sum(p * t for p, t in pulsar) / vikt) if vikt else None,
            "hr_max": max(maxar) if maxar else None, "sammanslagen": True}
