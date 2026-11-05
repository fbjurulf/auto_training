"""
D: sammanfattningen (notify) och veckorapporten (weekly) ska ge samma tal
för samma pass. Formateringen får skilja sig, talen inte.

Testet jämför de renderade texterna, inte funktionerna. Det är med avsikt:
börjar den ena modulen räkna själv igen syns det här, även om den delade
funktionen fortfarande finns kvar.
"""
import os
import re
import shutil
import tempfile
import unittest
from datetime import date, timedelta

os.environ.setdefault("TRANING_NOTIFY_LOG", os.path.join(tempfile.gettempdir(), "traning-test-notify.log"))

from tests.replay import replay

conn = tmp = None
SEKTIONER = {}


def setUpModule():
    global conn, tmp
    conn, tmp = replay()
    import weekly
    veckor = {date.fromisoformat(r["local_date"]) - timedelta(
        days=date.fromisoformat(r["local_date"]).weekday())
        for r in conn.execute("SELECT local_date FROM v_workout")}
    for mon in veckor:
        rader = weekly.build_report(conn, mon, mon + timedelta(days=6)).splitlines()
        for i, l in enumerate(rader):
            if l.startswith("### "):
                slut = next((j for j in range(i + 1, len(rader)) if rader[j].startswith("#")),
                            len(rader))
                SEKTIONER.setdefault(l.split(" ")[2], []).append("\n".join(rader[i:slut]))


def tearDownModule():
    conn.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _par():
    """(pass, notify-text, weekly-sektion) för varje pass i fixturerna."""
    import notify
    for w in conn.execute("SELECT id, local_date, sport_code, sport_name FROM v_workout ORDER BY id"):
        sektion = next(s for s in SEKTIONER[w["local_date"]] if w["sport_name"] in s.splitlines()[0])
        yield w, notify.build_message(conn, w["id"], w["sport_code"]), sektion


class SameNumbersTests(unittest.TestCase):
    def test_block_tempo(self):
        for w, kort, vecka in _par():
            with self.subTest(dag=w["local_date"], sport=w["sport_code"]):
                rad = next((l for l in kort.splitlines() if l.startswith("Block: ")), "")
                i_kort = re.findall(r"km (\d+:\d\d)", rad)
                tabell = vecka.split("**Block mellan pauser**")
                i_vecka = (re.findall(r"^\| \d+\*? \| [^|]+ \| [^|]+ \| (\d+:\d\d) \|",
                                      tabell[1].split("\n\n**")[0], re.M)
                           if len(tabell) > 1 else [])
                self.assertEqual(i_kort, i_vecka)

    def test_heart_rate_coverage(self):
        for w, kort, vecka in _par():
            with self.subTest(dag=w["local_date"], sport=w["sport_code"]):
                i_kort = re.findall(r"\((\d+) % puls\)", kort)
                i_vecka = re.findall(r"\(täcker (\d+) % av passet", vecka)
                self.assertEqual(i_kort, i_vecka)

    def test_elevation(self):
        for w, kort, vecka in _par():
            with self.subTest(dag=w["local_date"], sport=w["sport_code"]):
                monster = r"upp \d+ m, ner \d+ m"
                self.assertEqual(re.findall(monster, kort), re.findall(monster, vecka))
                self.assertNotIn("Apple", kort, "Apples tal står i rapporten och i /pass")

    def test_longest_swim_distances(self):
        for w, kort, vecka in _par():
            if not w["sport_code"].startswith("swim"):
                continue
            with self.subTest(dag=w["local_date"]):
                # Notify skriver 1 000 med tusentalsmellanrum, weekly 1000 -
                # formateringen får skilja sig, talen inte.
                tal = r"([\d\s]+)"
                i_kort = re.findall(rf"utan stopp: <b>{tal} m</b>(?: · med korta stopp: <b>{tal} m</b>)?", kort)
                i_vecka = re.findall(rf"utan stopp: {tal} m\*\*(?: · \*\*längsta med korta stopp: {tal} m)?", vecka)
                rensa = lambda par: [tuple(re.sub(r"\s", "", x) for x in p) for p in par]
                self.assertEqual(rensa(i_kort), rensa(i_vecka))

    def test_bike_block_speeds(self):
        visade = 0
        for w, kort, vecka in _par():
            if not w["sport_code"].startswith("bike"):
                continue
            with self.subTest(dag=w["local_date"]):
                rad = next(l for l in kort.splitlines() if l.startswith("Block: "))
                i_kort = re.findall(r"km ([\d,]+) km/h", rad)
                tabell = vecka.split("**Block mellan pauser**")[1].split("\n\n**")[0]
                i_vecka = re.findall(r"^\| \d+\*? \| [^|]+ \| [^|]+ \| ([\d,]+) \|", tabell, re.M)
                self.assertEqual(i_kort, i_vecka)
                visade += 1
        self.assertEqual(visade, 2)

    def test_bike_speeds(self):
        for w, kort, vecka in _par():
            if not w["sport_code"].startswith("bike"):
                continue
            with self.subTest(dag=w["local_date"]):
                i_kort = re.findall(r"([\d,]+) km/h · max ([\d,]+) km/h", kort)
                i_vecka = re.findall(r"snittfart ([\d,]+) km/h, maxfart ([\d,]+) km/h", vecka)
                self.assertTrue(i_kort)
                self.assertEqual(i_kort, i_vecka)

    def test_average_pace(self):
        for w, kort, vecka in _par():
            if not w["sport_code"].startswith("run"):
                continue
            with self.subTest(dag=w["local_date"]):
                i_kort = re.findall(r"· (\d+:\d\d)/km", kort)
                i_vecka = re.findall(r"snitttempo (\d+:\d\d)/km", vecka)
                self.assertEqual(i_kort, i_vecka)


if __name__ == "__main__":
    unittest.main()
