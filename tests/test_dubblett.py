"""A8: en FIT-fil utan UUID följd av HAE får inte bli två pass."""
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.fixtures import build_db
from tests.replay import DATA_DIR, _ingest_hae

FIT_PREFIX = "2026-10-22-172553-"
HAE_EVENTS = [  # i produktionens ordning efter FIT-filen 17:00:11Z
    {"at": "2026-10-22T17:14:11Z", "hae": ["hae/2026-10-22_run_raw616.json.gz"]},
    {"at": "2026-10-23T18:27:08Z", "hae": ["hae/2026-10-22_run_raw624.json.gz"]},
]


class UuidLessFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="traning-a8-"))
        os.environ["TRANING_DB"] = str(cls.tmp / "traning.db")
        build_db(os.environ["TRANING_DB"]).close()
        import parse_fit
        import parse_hae
        parse_hae.DB_PATH = parse_fit.DB_PATH = os.environ["TRANING_DB"]
        parse_fit.FIT_DIR = cls.tmp / "fit"
        parse_fit.FIT_DIR.mkdir()
        fit = next((DATA_DIR / "fit").glob(FIT_PREFIX + "*.fit"))
        shutil.copy(fit, parse_fit.FIT_DIR / fit.name)

        # Som de åtta filerna i produktionen: inget UUID, filhashen blir external_id.
        with patch.object(parse_fit, "session_uuid", return_value=None):
            parse_fit.main()
        for event in HAE_EVENTS:
            _ingest_hae(event)
            parse_hae.main()

        cls.conn = sqlite3.connect(os.environ["TRANING_DB"])
        cls.conn.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_one_workout_not_two(self):
        rows = self.conn.execute(
            "SELECT source_code, external_id FROM v_workout WHERE local_date='2026-10-22'"
        ).fetchall()
        self.assertEqual(len(rows), 1, [tuple(r) for r in rows])
        self.assertEqual(rows[0]["source_code"], "healthfit_fit")
        self.assertEqual(len(rows[0]["external_id"]), 32, "external_id ska vara filhashen")

    def test_time_match_is_recorded_once(self):
        rows = self.conn.execute(
            """SELECT m.method, s.code FROM workout_match m
               JOIN source s ON s.id = m.source_id""").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("tid", "apple_health_hae")])

    def test_watchdog_reports_time_match_for_new_workout(self):
        import watchdog
        # Vakthunden kör morgonen efter passet (start 15:25:53Z).
        alerts = watchdog.database_alerts(self.conn, datetime(2026, 10, 23, 8, tzinfo=timezone.utc))
        self.assertTrue(any(a.startswith("Matchat på starttid") and "2026-10-22" in a
                            for a in alerts), alerts)
        self.assertFalse(any(a.startswith("Möjliga dubbletter") for a in alerts), alerts)

    def test_old_workout_does_not_alert_even_if_match_row_is_new(self):
        # Som pass 13 efter omtolkningen 2026-11-04: matchningsraden är ny,
        # passet är gammalt och redan godtaget.
        import watchdog
        alerts = watchdog.database_alerts(self.conn, datetime(2026, 11, 5, 8, tzinfo=timezone.utc))
        self.assertFalse(any(a.startswith("Matchat på starttid") for a in alerts), alerts)


if __name__ == "__main__":
    unittest.main()
