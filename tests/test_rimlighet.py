"""Rimlighetskontrollen varnar en gång per avvikelse och avvisar aldrig ett pass."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("TRANING_NOTIFY_LOG", os.path.join(tempfile.gettempdir(), "traning-test-notify.log"))

import rimlighet
from rimlighet import avvikelser
from tests.fixtures import build_db
from tests.replay import replay


def _pass(conn, nr, sport="swim_pool", tid=2000, dist=1000.0, kalla="healthfit_fit", dold=None):
    conn.execute(
        """INSERT INTO workout (id, source_id, external_id, sport_id, started_at, ended_at,
             local_date, duration_s, elapsed_s, distance_m, fit_enriched, deleted_at, notified_at)
           VALUES (?, (SELECT id FROM source WHERE code=?), ?, (SELECT id FROM sport WHERE code=?),
                   '2026-10-05T18:00:00Z', '2026-10-05T19:00:00Z', '2026-10-05', ?, ?, ?, 1, ?,
                   '2026-10-05T20:00:00Z')""",
        (nr, kalla, f"x{nr}", sport, tid, tid, dist, dold))


class LimitTests(unittest.TestCase):
    def test_the_two_workouts_that_got_in(self):
        pass_43 = avvikelser("swim_pool", 57631, 800.0, pool_length_m=25.0)
        self.assertEqual(len(pass_43), 2, pass_43)
        self.assertIn("tid 16:00:31 är över gränsen 3:00:00", pass_43[0])
        self.assertIn("tempo 120:04/100 m", pass_43[1])
        pass_79 = avvikelser("swim_pool", 3754, 25.0, pool_length_m=25.0)
        self.assertEqual(len(pass_79), 1, pass_79)
        self.assertIn("tempo 250:16/100 m", pass_79[0])

    def test_ordinary_and_long_workouts_pass(self):
        for args in (("run", 14998, 42925.84), ("hike", 17547, 11462.43),
                     ("swim_pool", 3420, 520.0), ("swim_open", 419, 176.45),
                     ("ski_xc", 2082, 8795.47), ("bike", 5757, 32820.57),
                     ("tennis", 2925, None), ("badminton", 917, None)):
            with self.subTest(args=args):
                self.assertEqual(avvikelser(*args), [])

    def test_too_fast(self):
        self.assertIn("snabbare", avvikelser("run", 1000, 10000.0)[0])

    def test_sport_without_distance_has_only_time_limit(self):
        self.assertEqual(avvikelser("tennis", 3 * 3600, None), [])
        self.assertIn("över gränsen", avvikelser("tennis", 7 * 3600, None)[0])

    def test_pool_length(self):
        self.assertIn("bana 52 m", avvikelser("swim_pool", 3420, 520.0, pool_length_m=52.0)[0])
        for pool in (25.0, 33.33, 50.0):
            self.assertEqual(avvikelser("swim_pool", 2000, 1000.0, pool_length_m=pool), [])


class FixtureTests(unittest.TestCase):
    def test_no_real_workout_in_fixtures_warns(self):
        conn, tmp = replay()
        try:
            skickat = []
            self.assertEqual(rimlighet.kontrollera(conn, skickat.append), [])
            self.assertEqual(skickat, [])
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.conn = build_db()
        self.conn.row_factory = sqlite3.Row
        self.skickat = []
        _pass(self.conn, 1, tid=57631, dist=800.0)

    def _kor(self):
        return rimlighet.kontrollera(self.conn, self.skickat.append)

    def test_warns_and_keeps_the_workout(self):
        self.assertEqual(self._kor(), [1])
        self.assertIn("Orimligt pass 1", self.skickat[0])
        self.assertIn("/radera 1", self.skickat[0])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM v_workout").fetchone()[0], 1)

    def test_warns_only_once(self):
        self._kor()
        self.assertEqual(self._kor(), [])
        self.assertEqual(len(self.skickat), 1)

    def test_warns_again_when_fit_changes_the_values(self):
        _pass(self.conn, 2)
        self.assertEqual(self._kor(), [1], "pass 2 är rimligt")
        self.conn.execute("UPDATE workout SET duration_s=20000 WHERE id=2")
        self.assertEqual(self._kor(), [2])

    def test_failed_send_is_retried(self):
        def trasig(_):
            raise RuntimeError("Telegram nere")
        with self.assertLogs("rimlighet", level="ERROR"):
            self.assertEqual(rimlighet.kontrollera(self.conn, trasig), [])
        self.assertEqual(self._kor(), [1])

    def test_baseline_silences_old_workouts_only(self):
        self.assertEqual(len(rimlighet.baslinje(self.conn)), 1)
        self.assertEqual(self._kor(), [])
        _pass(self.conn, 2, tid=57631, dist=800.0)
        self.assertEqual(self._kor(), [2])

    def test_hidden_workout_does_not_warn(self):
        self.conn.execute("DELETE FROM workout")
        _pass(self.conn, 3, tid=57631, dist=800.0, dold="2026-10-09T15:09:37Z")
        self.assertEqual(self._kor(), [])

    def test_workout_without_swim_row_is_checked(self):
        # Kontrollen får inte gå via workout_swim: pass 74 och 76 saknar rad där.
        self.conn.execute("DELETE FROM workout")
        _pass(self.conn, 76, tid=57631, dist=800.0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM workout_swim").fetchone()[0], 0)
        self.assertEqual(self._kor(), [76])

    def test_sport_name_is_escaped(self):
        self.conn.execute("UPDATE sport SET name_sv='Sim & <bad>' WHERE code='swim_pool'")
        self._kor()
        self.assertIn("Sim &amp; &lt;bad&gt;", self.skickat[0])


class PipelineTests(unittest.TestCase):
    def test_runs_without_new_workouts_and_survives_failure(self):
        import notify
        tmp = tempfile.mkdtemp(prefix="traning-test-")
        try:
            db = os.path.join(tmp, "t.db")
            conn = build_db(db)
            conn.row_factory = sqlite3.Row
            _pass(conn, 1, tid=57631, dist=800.0)
            conn.commit()
            conn.close()
            skickat = []
            with patch.object(notify, "DB_PATH", db), patch.object(notify, "send", skickat.append):
                notify.main()
                self.assertEqual(len(skickat), 1)
                self.assertIn("Orimligt pass 1", skickat[0])
                with patch.object(rimlighet, "kontrollera", side_effect=sqlite3.OperationalError("x")):
                    notify.main()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
