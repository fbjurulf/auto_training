"""FIT-status: en funktion styr både vakthundens larm och rapportens märkning."""
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("TRANING_WEEKLY_LOG", os.path.join(tempfile.gettempdir(), "traning-test-weekly.log"))
os.environ.setdefault("TRANING_REPORT_DIR", os.path.join(tempfile.gettempdir(), "traning-test-rapporter"))

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


if __name__ == "__main__":
    unittest.main()
