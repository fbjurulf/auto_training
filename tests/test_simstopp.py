"""
D1: settiden och längsta sammanhängande sträcka (A6) avgör stopp med samma
funktion och samma gräns, passdata.VILA_MIN_S. Två kopior av gränsen vore
samma fel som de tre tempoberäkningarna.
"""
import gzip
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import passdata
from tests.replay import replay

T0 = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)


def _l(start, dur, aktiv=True):
    return (T0 + timedelta(seconds=start), dur, aktiv)


class StopRuleTests(unittest.TestCase):
    def test_turns_are_not_stops(self):
        # Fyra längder med vändningar på 3 s, sedan ett stopp på 16 s.
        langder = [_l(0, 30), _l(33, 30), _l(66, 30), _l(112, 30)]
        self.assertEqual(passdata.pauser(langder), [3, 3, 16])
        self.assertEqual(passdata.avbrott(langder), 16)
        self.assertEqual(passdata.langsta_svit(langder), 3)

    def test_idle_length_between_counts_into_the_pause(self):
        langder = [_l(0, 30), _l(30, 8, aktiv=False), _l(40, 30)]
        self.assertEqual(passdata.pauser(langder), [10])
        self.assertEqual(passdata.langsta_svit(langder), 2)

    def test_stop_around_rest_lap(self):
        langder = [_l(0, 30), _l(45, 30)]
        self.assertTrue(passdata.stopp_vid(langder, T0 + timedelta(seconds=31),
                                           T0 + timedelta(seconds=44)))


class EdgeTimeTests(unittest.TestCase):
    def test_time_before_first_length_is_late_start(self):
        langder = [_l(31, 25), _l(59, 25)]
        self.assertEqual(passdata.kanttid(T0, 84, langder), (31, 0))
        self.assertEqual(passdata.avbrott(langder), 0, "en sen start är inget avbrott")

    def test_short_edges_count_as_swimming(self):
        self.assertEqual(passdata.kanttid(T0, 62, [_l(5, 25), _l(30, 25)]), (0, 0))


class SameLimitTests(unittest.TestCase):
    """Flyttas gränsen ändras både A6 och fotnoterna - de läser samma funktion."""

    def _simpass(self, dag):
        conn, tmp = replay()
        try:
            w = conn.execute("SELECT id FROM v_workout WHERE local_date=? AND sport_code='swim_pool'",
                             (dag,)).fetchone()
            sw = conn.execute("SELECT longest_continuous_m FROM workout_swim WHERE workout_id=?",
                              (w["id"],)).fetchone()[0]
            set_med_avbrott = conn.execute(
                """SELECT count(*) FROM workout_segment WHERE workout_id=? AND parent_id IS NULL
                   AND extra LIKE '%avbrott_s%'""", (w["id"],)).fetchone()[0]
            vila_som_stopp = conn.execute(
                """SELECT count(*) FROM workout_segment WHERE workout_id=? AND parent_id IS NULL
                   AND extra LIKE '%"stopp": true%'""", (w["id"],)).fetchone()[0]
            return sw, set_med_avbrott, vila_som_stopp
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_both_follow_the_limit(self):
        # 2026-10-19 har uppehåll på 10,3-11,7 s - under gränsen i dag.
        normal = self._simpass("2026-10-19")
        with patch.object(passdata, "VILA_MIN_S", 10):
            lagre = self._simpass("2026-10-19")
        with patch.object(passdata, "VILA_MIN_S", 10_000):
            ingen = self._simpass("2026-10-19")
        self.assertEqual(normal[0], 350)
        self.assertLess(lagre[0], normal[0], "A6 följde inte en lägre gräns")
        self.assertGreater(lagre[1] + lagre[2], normal[1] + normal[2],
                           "fotnoterna följde inte en lägre gräns")
        self.assertEqual(ingen, (1400, 0, 0), "utan stopp ska hela passet vara en sträcka")


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn, cls.tmp = replay()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _sektion(self, dag):
        import weekly
        from datetime import date
        d = date.fromisoformat(dag)
        mon = d - timedelta(days=d.weekday())
        r = weekly.build_report(self.conn, mon, mon + timedelta(days=6)).splitlines()
        i = next(i for i, l in enumerate(r) if l.startswith("### ") and dag in l and "Sim" in l)
        j = next((j for j in range(i + 1, len(r)) if r[j].startswith("#")), len(r))
        return "\n".join(r[i:j])

    def test_late_start_gets_its_own_footnote(self):
        # 2026-10-07: 87 s vid väggen före första frånskjutet i set 1.
        sektion = self._sektion("2026-10-07")
        self.assertIn("* Set 1: 1:27 före start.", sektion)
        self.assertNotIn("Set 1: 1:27 avbrott", sektion)

    def test_both_a6_measures_in_report(self):
        sektion = self._sektion("2026-10-07")
        self.assertIn("**Längsta utan stopp: 375 m** · **längsta med korta stopp: 1000 m**", sektion)

    def test_set_times_add_up_to_header_swim_time(self):
        # Rubrikens simtid och setens tid räknas likadant - tempot går ihop.
        for dag in ("2026-09-30", "2026-10-07", "2026-10-19"):
            with self.subTest(dag=dag):
                w = self.conn.execute(
                    "SELECT id FROM v_workout WHERE local_date=? AND sport_code='swim_pool'",
                    (dag,)).fetchone()
                aktiv = self.conn.execute("SELECT active_s FROM workout_swim WHERE workout_id=?",
                                          (w["id"],)).fetchone()[0]
                import json
                summa = sum(
                    s["duration_s"] - sum(json.loads(s["extra"] or "{}").get(k) or 0
                                          for k in ("avbrott_s", "fore_start_s", "efter_sista_s"))
                    for s in self.conn.execute(
                        """SELECT duration_s, extra FROM workout_segment WHERE workout_id=?
                           AND parent_id IS NULL AND distance_m > 0""", (w["id"],)))
                self.assertAlmostEqual(summa, aktiv, delta=1)


if __name__ == "__main__":
    unittest.main()
