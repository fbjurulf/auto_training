"""
A4: 80/20-andelen räknas bara på sporter med counts_in_intensity_ratio.
v42 kan inte testa procenten - all zontid ligger i Z1-Z2 med och utan vandring -
så här finns en vecka där den gör skillnad.
"""
import os
import sqlite3
import tempfile
import unittest
from datetime import date

os.environ.setdefault("TRANING_WEEKLY_LOG", os.path.join(tempfile.gettempdir(), "traning-test-weekly.log"))
os.environ.setdefault("TRANING_REPORT_DIR", os.path.join(tempfile.gettempdir(), "traning-test-rapporter"))

import weekly
from tests.fixtures import build_db

MON, SUN = date(2026, 10, 12), date(2026, 10, 18)
H = 3600


class ZoneShareTests(unittest.TestCase):
    def setUp(self):
        self.conn = build_db()
        self.conn.row_factory = sqlite3.Row
        self.nr = 0

    def _pass(self, sport, sek, zoner=None, dold=None):
        self.nr += 1
        self.conn.execute(
            """INSERT INTO workout (id, source_id, external_id, sport_id, started_at, ended_at,
                 local_date, duration_s, elapsed_s, deleted_at)
               VALUES (?, (SELECT id FROM source WHERE code='healthfit_fit'), ?,
                       (SELECT id FROM sport WHERE code=?), ?, ?, '2026-10-14', ?, ?, ?)""",
            (self.nr, f"x{self.nr}", sport, f"2026-10-14T0{self.nr}:00:00Z",
             f"2026-10-14T0{self.nr}:30:00Z", sek, sek, dold))
        for zon, s in (zoner or {}).items():
            self.conn.execute("INSERT INTO workout_zone (workout_id, zone, seconds) VALUES (?,?,?)",
                              (self.nr, zon, s))

    def _text(self):
        return "\n".join(weekly.zone_section(self.conn, MON, SUN))

    def test_hike_does_not_drown_the_share(self):
        self._pass("run", H, {"Z1-Z2": H / 2, "Z4": H / 2})
        self._pass("hike", 12 * H, {"Z1-Z2": 12 * H})
        text = self._text()
        self.assertIn("**50 % av träningstiden låg lugnt**", text, "med vandring vore det 96 %")
        self.assertIn("under målet", text)

    def test_basis_line_says_what_the_number_rests_on(self):
        self._pass("run", H, {"Z1-Z2": H})
        self._pass("swim_pool", H, {"Z1-Z2": H})
        self._pass("hike", 12 * H, {"Z1-Z2": 12 * H})
        self._pass("strength", H // 2)
        self.assertIn("Räknat på 2:00:00 ", self._text())
        self.assertRegex(self._text(), r"(löpning och simning \(bassäng\)|simning \(bassäng\) och löpning)"
                                       r" av veckans 14:30:00\. "
                                       r"Utanför: 12:00:00 vandring, 30:00 styrketräning\.")

    def test_excluded_sport_stays_in_zone_table_and_volume(self):
        self._pass("run", H, {"Z1-Z2": H})
        self._pass("hike", 12 * H, {"Z1-Z2": 12 * H})
        rader = self._text().splitlines()
        self.assertTrue(any(r.startswith("| Vandring |") and r.endswith("| utanför |") for r in rader), rader)
        rapport = weekly.build_report(self.conn, MON, SUN)
        self.assertRegex(rapport, r"\| Vandring \| 1 \| 12:00:00 \|")

    def test_share_row_equals_the_headline(self):
        self._pass("run", H, {"Z1-Z2": H / 2, "Z4": H / 2})
        self._pass("hike", 12 * H, {"Z1-Z2": 12 * H})
        rad = next(r for r in self._text().splitlines() if r.startswith("| **I andelen**"))
        self.assertTrue(rad.endswith("| **50 %** |"), rad)

    def test_workout_without_zones_is_seen_not_dropped(self):
        # Pass 76 och 94: ingen zonrad. De ska ligga i nämnaren och nämnas.
        self._pass("run", H, {"Z1-Z2": H})
        self._pass("run", H)
        text = self._text()
        self.assertIn("Underlaget är 1:00:00 av 2:00:00 träningstid", text)
        self.assertIn("Utan zondata, räknas som tid utan puls: 2026-10-14 Löpning (pass 2).", text)

    def test_no_share_rather_than_zero_when_only_excluded_sports(self):
        self._pass("hike", 3 * H, {"Z1-Z2": 3 * H})
        text = self._text()
        self.assertIn("**Ingen andel lugn träning för veckan**", text)
        self.assertNotIn("0 % av träningstiden", text)
        self.assertNotIn("I andelen", text)
        self.assertIn("Inget pass i veckan räknas in i andelen. Utanför: 3:00:00 vandring.", text)

    def test_sport_list_lives_in_the_table(self):
        self._pass("run", H, {"Z4": H})
        self._pass("hike", H, {"Z1-Z2": H})
        self.conn.execute("UPDATE sport SET counts_in_intensity_ratio=1 WHERE code='hike'")
        self.assertIn("**50 % av träningstiden låg lugnt**", self._text())

    def test_hidden_workout_is_not_counted(self):
        self._pass("run", H, {"Z1-Z2": H})
        self._pass("run", H, {"Z4": H}, dold="2026-10-20T00:00:00Z")
        text = self._text()
        self.assertIn("**100 % av träningstiden låg lugnt**", text)
        self.assertIn("Räknat på 1:00:00 löpning av veckans 1:00:00.", text)

    def test_counting_sports(self):
        rader = self.conn.execute(
            "SELECT code FROM sport WHERE counts_in_intensity_ratio=1 ORDER BY code").fetchall()
        self.assertEqual([r["code"] for r in rader],
                         ["bike", "bike_indoor", "run", "run_treadmill", "swim_open", "swim_pool"])


if __name__ == "__main__":
    unittest.main()
