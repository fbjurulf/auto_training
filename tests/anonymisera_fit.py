"""
Nollar koordinaterna i FIT-fixturer, på byte-nivå så att allt annat i filen är
orört: höjd, distans, puls, tider. Filens CRC räknas om. Idempotent.

Ingen kod läser koordinaterna. parse_fit tar höjd ur enhanced_altitude och
distans ur record-fältet. Lägg en ny fixtur så här:

    python tests/anonymisera_fit.py tests/data/fit/*.fit
"""
import struct
import sys
from pathlib import Path

# (global_mesg_num, fältnummer): alla platsfält FIT-profilen har i de meddelanden
# fixturerna innehåller, även de som inte förekommer i dagens filer.
PLATSFALT = {
    (20, 0), (20, 1),                              # record: position_lat/long
    (19, 3), (19, 4), (19, 5), (19, 6),            # lap: start/end_position
    (18, 3), (18, 4), (18, 29), (18, 30), (18, 31), (18, 32),  # session: start, nec, swc
}
OGILTIG = b"\xff\xff\xff\x7f"   # sint32 "saknas" - lämnas som det är
NOLL = b"\x00\x00\x00\x00"

_CRC = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
        0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


def crc(data):
    c = 0
    for b in data:
        c = ((c >> 4) & 0x0FFF) ^ _CRC[c & 0xF] ^ _CRC[b & 0xF]
        c = ((c >> 4) & 0x0FFF) ^ _CRC[c & 0xF] ^ _CRC[(b >> 4) & 0xF]
    return c


def nolla(data):
    """Ger (ny fil, antal nollade fält). Kastar om filen inte går att gå igenom."""
    ut = bytearray(data)
    rubrik = data[0]
    storlek = struct.unpack_from("<I", data, 4)[0]
    if data[8:12] != b".FIT" or rubrik + storlek + 2 != len(data):
        raise ValueError("inte en FIT-fil med en enda kedjelänk")
    pos, slut, antal = rubrik, rubrik + storlek, 0
    lokala = {}
    while pos < slut:
        h = data[pos]
        pos += 1
        if not h & 0x80 and h & 0x40:       # definition
            lokal = h & 0x0F
            stor_endian = data[pos + 1] == 1
            glob = struct.unpack_from(">H" if stor_endian else "<H", data, pos + 2)[0]
            n = data[pos + 4]
            pos += 5
            falt = [(data[pos + i * 3], data[pos + i * 3 + 1]) for i in range(n)]
            pos += n * 3
            utv = 0
            if h & 0x20:                    # utvecklarfält
                m = data[pos]
                pos += 1
                utv = sum(data[pos + i * 3 + 1] for i in range(m))
                pos += m * 3
            lokala[lokal] = (glob, falt, utv, stor_endian)
            continue
        lokal = (h >> 5) & 3 if h & 0x80 else h & 0x0F
        glob, falt, utv, stor_endian = lokala[lokal]
        for nr, langd in falt:
            if (glob, nr) in PLATSFALT:
                if langd != 4:
                    raise ValueError(f"platsfält {glob}/{nr} har längd {langd}")
                varde = bytes(data[pos:pos + 4])
                if varde not in (OGILTIG, b"\x7f\xff\xff\xff"):
                    ut[pos:pos + 4] = NOLL
                    antal += 1
            pos += langd
        pos += utv
    struct.pack_into("<H", ut, slut, crc(ut[:slut]))
    return bytes(ut), antal


def main(filer):
    for f in map(Path, filer):
        ny, antal = nolla(f.read_bytes())
        f.write_bytes(ny)
        print(f"{antal:>7} fält  {f.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
