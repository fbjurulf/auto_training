"""Del B: höjdmeter ur höjdserien, samma funktion för FIT och HAE."""
import shutil
import unittest
from datetime import datetime, timedelta, timezone

import hojd
from tests.replay import replay

T0 = datetime(2026, 10, 15, 8, tzinfo=timezone.utc)


class HysteresisTests(unittest.TestCase):
    def test_slow_climb_counts_fully(self):
        # En lång, långsam klättring får inte kastas av tröskeln.
        upp, ner = hojd.upp_ner([i * 0.2 for i in range(101)], troskel=3)
        self.assertAlmostEqual(upp, 20.0)
        self.assertEqual(ner, 0.0)

    def test_small_reversals_under_threshold_are_ignored(self):
        serie = [0, 2, 1, 3, 2, 4, 3, 5]  # stiger, med vändningar på 1 m
        self.assertEqual(hojd.upp_ner(serie, troskel=1.5), (5, 0))
        self.assertEqual(hojd.upp_ner(serie, troskel=0), (8, 3))

    def test_up_minus_down_is_end_minus_start(self):
        serie = [100, 110, 95, 120, 118, 130, 104]
        upp, ner = hojd.upp_ner(serie, troskel=0)
        self.assertAlmostEqual(upp - ner, serie[-1] - serie[0])

    def test_smoothing_is_centered_moving_average(self):
        self.assertEqual(hojd.utjamna([0, 0, 3, 0, 0], fonster=3), [0, 1, 1, 1, 0])


class KmTests(unittest.TestCase):
    def test_km_segments_use_moving_time(self):
        # 2,5 km i 4 m/s med en paus på 60 s efter 1,2 km.
        punkter, t, d = [], T0, 0.0
        while d <= 2500:
            punkter.append((t, d, 150))
            t += timedelta(seconds=1)
            d += 4.0
            if abs(d - 1200) < 2:
                t += timedelta(seconds=60)
        km = hojd.km_segment(punkter)
        self.assertEqual([round(k["distance_m"]) for k in km], [1000, 1000, 500])
        # Steget över pausen (1 s rörelse + 60 s) är längre än 10 s och räknas
        # helt som paus - därav 249.
        self.assertEqual([round(k["duration_s"]) for k in km], [250, 249, 125])


class TextTests(unittest.TestCase):
    def test_apple_value_shown_only_when_25_percent_apart(self):
        self.assertEqual(hojd.text(120, 118, 100), "upp 120 m, ner 118 m")
        self.assertEqual(hojd.text(102, 99, 31), "upp 102 m, ner 99 m (Apple: upp 31 m)")
        self.assertIsNone(hojd.text(None, None, 50))


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn, cls.tmp = replay()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _pass(self, local_date, sport):
        return self.conn.execute(
            "SELECT * FROM v_workout WHERE local_date=? AND sport_code LIKE ?",
            (local_date, sport + "%")).fetchone()

    def test_v42_hikes_within_15_percent_of_apple(self):
        # Kalibreringen: vandringarna i v42 låg på 0,94-0,99 av Apples värde.
        for dag in ("2026-10-15", "2026-10-16", "2026-10-17"):
            with self.subTest(dag=dag):
                w = self._pass(dag, "hike")
                self.assertAlmostEqual(w["ascent_m"] / w["ascent_apple_m"], 1, delta=0.15)
                self.assertGreater(w["descent_m"], 0)

    def test_km_segments_cover_distance_and_active_time(self):
        w = self._pass("2026-10-22", "run")
        km = self.conn.execute(
            "SELECT distance_m, duration_s FROM workout_segment WHERE workout_id=? AND kind='km'",
            (w["id"],)).fetchall()
        self.assertEqual(len(km), 14)
        # Sessionens total_distance ligger 2 m över sista recordets distans.
        self.assertAlmostEqual(sum(k["distance_m"] for k in km), w["distance_m"], delta=5)
        self.assertAlmostEqual(sum(k["duration_s"] for k in km), w["duration_s"], delta=30)

    def test_lap_elevation_sums_to_pass_elevation(self):
        # 2026-10-04: klockans km-varv, inga luckor mellan varven.
        w = self._pass("2026-10-04", "run")
        upp = self.conn.execute(
            """SELECT SUM(elevation_gain_m) FROM workout_segment
               WHERE workout_id=? AND kind='split'""", (w["id"],)).fetchone()[0]
        self.assertAlmostEqual(upp, w["ascent_m"], delta=w["ascent_m"] * 0.05)

    def test_swims_have_no_elevation(self):
        self.assertIsNone(self._pass("2026-10-21", "swim")["ascent_m"])

    def test_weekly_volume_table_has_up_and_down(self):
        import weekly
        from datetime import date
        rapport = weekly.build_report(self.conn, date(2026, 10, 12), date(2026, 10, 18))
        rad = next(l for l in rapport.splitlines() if l.startswith("| Vandring |"))
        upp = sum(self._pass(d, "hike")["ascent_m"] for d in ("2026-10-15", "2026-10-16", "2026-10-17"))
        self.assertIn(f"| {weekly.n(upp, 0)} m |", rad)


if __name__ == "__main__":
    unittest.main()
