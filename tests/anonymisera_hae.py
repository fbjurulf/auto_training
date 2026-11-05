"""
Nollar latitud och longitud i rutten i HAE-fixturer. Allt annat i workout-objektet
är orört. parse_hae läser bara höjden ur rutten. Idempotent, och mtime 0 gör att
samma innehåll ger samma fil. Lägg en ny fixtur så här:

    python tests/anonymisera_hae.py tests/data/hae/*.json.gz
"""
import gzip
import json
import sys
from pathlib import Path


def nolla(workout):
    """Nollar koordinaterna på plats. Ger antal ruttpunkter."""
    rutt = workout.get("route") or []
    for punkt in rutt:
        punkt["latitude"] = punkt["longitude"] = 0
    return len(rutt)


def main(filer):
    for f in map(Path, filer):
        workout = json.loads(gzip.decompress(f.read_bytes()))
        antal = nolla(workout)
        text = json.dumps(workout, ensure_ascii=False).encode("utf-8")
        f.write_bytes(gzip.compress(text, mtime=0))
        print(f"{antal:>7} punkter  {f.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
