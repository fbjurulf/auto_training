"""FIT-status: en funktion styr både vakthundens larm och rapportens märkning."""
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("TRANING_WEEKLY_LOG", os.path.join(tempfile.gettempdir(), "traning-test-weekly.log"))
os.environ.setdefault("TRANING_REPORT_DIR", os.path.join(tempfile.gettempdir(), "traning-test-rapporter"))
os.environ.setdefault("TRANING_NOTIFY_LOG", os.path.join(tempfile.gettempdir(), "traning-test-notify.log"))

import fitstatus
from tests.fixtures import build_db

START = "2026-10-27T17:13:16Z"  # pass 94
T0 = datetime(2026, 10, 27, 17, 13, 16, tzinfo=timezone.utc)


class StatusTests(unittest.TestCase):
    def _status(self, efter, source="apple_health_hae", fit_enriched=0):
        return fitstatus.fit_status(source, fit_enriched, START, T0 + efter)

    def test_fit_source_or_enriched_is_fit(self):
        self.assertEqual(self._status(timedelta(days=30), source="healthfit_fit"), fitstatus.FIT)
        self.assertEqual(self._status(timedelta(days=30), fit_enriched=1), fitstatus.FIT)

    def test_hae_only_moves_through_three_states(self):
        self.assertEqual(self._status(timedelta(hours=23, minutes=59)), fitstatus.VANTAR)
        self.assertEqual(self._status(timedelta(hours=24)), fitstatus.SAKNAS)
        self.assertEqual(self._status(timedelta(days=14) - timedelta(seconds=1)), fitstatus.SAKNAS)
        self.assertEqual(self._status(timedelta(days=14)), fitstatus.FORLORAD)

    def test_manual_workouts_have_no_status(self):
        self.assertIsNone(self._status(timedelta(days=2), source="manual"))


class DefinitionNoticeTests(unittest.TestCase):
    def test_notice_only_in_week_of_change(self):
        import weekly
        conn = build_db()
        conn.row_factory = sqlite3.Row
        v45 = weekly.build_report(conn, date(2026, 11, 2), date(2026, 11, 8))
        v46 = weekly.build_report(conn, date(2026, 11, 9), date(2026, 11, 15))
        self.assertIn("**Obs:** Simtempot räknas", v45.split("## ")[0])
        self.assertNotIn("Simtempot räknas", v46)


class MarkingTests(unittest.TestCase):
    def test_report_marks_hae_pass_after_alarm_has_stopped(self):
        # Larmet tystnar efter 14 dygn - rapporten ska ändå visa HAE-passet.
        import weekly
        conn = build_db()
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT INTO workout (source_id, external_id, sport_id, started_at, ended_at,
                 local_date, duration_s, elapsed_s, distance_m)
               VALUES ((SELECT id FROM source WHERE code='apple_health_hae'), 'x',
                       (SELECT id FROM sport WHERE code='run'), ?, '2026-10-27T17:56:36Z',
                       '2026-10-27', 2600, 2600, 7173)""", (START,))
        rapport = weekly.build_report(conn, date(2026, 10, 26), date(2026, 11, 1),
                                       now=T0 + timedelta(days=15))
        self.assertIn("**HAE-pass:** FIT-filen kom aldrig", rapport)

    def test_marking_text_per_state(self):
        self.assertIsNone(fitstatus.markering(fitstatus.FIT))
        self.assertIn("till och med 2026-11-10", fitstatus.markering(fitstatus.SAKNAS, START))


class SwimMarkingTests(unittest.TestCase):
    """Simpass utan längder: samma rad i sammanfattning och rapport, för FIT och HAE."""

    def setUp(self):
        self.conn = build_db()
        self.conn.row_factory = sqlite3.Row

    def _pass(self, sport="swim_pool", source="healthfit_fit", fit_enriched=1,
              start="2026-10-05T18:12:47Z", dag="2026-10-05"):
        return self.conn.execute(
            """INSERT INTO workout (source_id, external_id, sport_id, started_at, ended_at,
                 local_date, duration_s, elapsed_s, distance_m, fit_enriched)
               VALUES ((SELECT id FROM source WHERE code=?), 'x',
                       (SELECT id FROM sport WHERE code=?), ?, ?, ?, 2100, 2100, 1200, ?)""",
            (source, sport, start, start, dag, fit_enriched)).lastrowid

    def _bada(self, wid, sport, dag="2026-10-05", now=None):
        """(notify-text, weekly-sektion) för passet."""
        import notify
        import weekly
        d = date.fromisoformat(dag)
        mon = d - timedelta(days=d.weekday())
        rapport = weekly.build_report(self.conn, mon, mon + timedelta(days=6), now=now)
        sektion = rapport.split("### ", 1)[1].split("## Volym senaste")[0]
        return notify.build_message(self.conn, wid, sport), sektion

    def _hae_rad(self, wid):
        # Som parse_hae.save_swim: längdantal, armtag och SWOLF härledda ur distans/bana.
        self.conn.execute(
            """INSERT INTO workout_swim (workout_id, pool_length_m, length_count, swolf_avg,
                 strokes_per_length_avg, pace_s_per_100m, rest_s_total)
               VALUES (?, 25, 48, 45, 12, 140, 300)""", (wid,))

    def test_fit_file_without_lengths_like_workout_76(self):
        wid = self._pass()
        kort, vecka = self._bada(wid, "swim_pool")
        self.assertIn(fitstatus.LANGDER_SAKNAS, kort)
        self.assertIn(fitstatus.LANGDER_SAKNAS, vecka)
        self.assertIn("längder saknas i källfilen", fitstatus.LANGDER_SAKNAS.lower())

    def test_hae_workout_gets_the_same_marking_and_hides_derived_values(self):
        wid = self._pass(source="apple_health_hae", fit_enriched=0)
        self._hae_rad(wid)
        kort, vecka = self._bada(wid, "swim_pool")
        for text in (kort, vecka):
            self.assertIn(fitstatus.LANGDER_SAKNAS, text)
            self.assertNotIn("48 längder", text)
            self.assertNotIn("SWOLF 45", text)
            self.assertNotIn("armtag/längd", text)

    def test_hae_workout_waiting_for_fit_is_not_called_missing(self):
        wid = self._pass(source="apple_health_hae", fit_enriched=0)
        w = self.conn.execute("SELECT * FROM v_workout WHERE id=?", (wid,)).fetchone()
        nu = datetime(2026, 10, 5, 19, tzinfo=timezone.utc)
        self.assertEqual(fitstatus.simmarkering(self.conn, w, nu), fitstatus.LANGDER_VANTAR)

    def test_open_water_says_why_and_is_no_warning(self):
        wid = self._pass(sport="swim_open")
        kort, vecka = self._bada(wid, "swim_open")
        self.assertIn(fitstatus.OPPET_VATTEN, kort)
        self.assertIn(fitstatus.OPPET_VATTEN, vecka)
        self.assertNotIn("saknas", kort)

    def test_workout_with_lengths_has_no_marking(self):
        wid = self._pass()
        self.conn.execute(
            """INSERT INTO workout_segment (workout_id, seq, kind, started_at, duration_s, distance_m)
               VALUES (?, 1, 'length', '2026-10-05T18:12:47Z', 30, 25)""", (wid,))
        w = self.conn.execute("SELECT * FROM v_workout WHERE id=?", (wid,)).fetchone()
        self.assertIsNone(fitstatus.simmarkering(self.conn, w))

    def test_other_sports_and_manual_swims_have_no_marking(self):
        for sport, source in (("run", "apple_health_hae"), ("swim_pool", "manual")):
            with self.subTest(sport=sport):
                self.conn.execute("DELETE FROM workout")
                wid = self._pass(sport=sport, source=source, fit_enriched=0)
                w = self.conn.execute("SELECT * FROM v_workout WHERE id=?", (wid,)).fetchone()
                self.assertIsNone(fitstatus.simmarkering(self.conn, w))


if __name__ == "__main__":
    unittest.main()
