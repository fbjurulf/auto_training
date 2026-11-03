import sqlite3
import subprocess
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import watchdog


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE raw_import (received_at TEXT);
            CREATE TABLE daily_metric (local_date TEXT);
            CREATE TABLE source (id INTEGER PRIMARY KEY, code TEXT);
            CREATE TABLE import_run (
                id INTEGER PRIMARY KEY,
                source_id INTEGER,
                started_at TEXT,
                status TEXT,
                skipped_count INTEGER,
                skipped_detail TEXT
            );
            INSERT INTO source VALUES (1, 'healthfit_fit');
            """
        )
        self.now = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)

    def tearDown(self):
        self.conn.close()

    def test_recent_data_and_healthy_services_do_not_alert(self):
        self.conn.execute(
            "INSERT INTO raw_import VALUES (?)", ("2026-10-31T12:00:00Z",)
        )
        self.conn.execute("INSERT INTO daily_metric VALUES (?)", ("2026-10-31",))
        with patch.object(watchdog, "send_alert") as send_alert:
            alerts = watchdog.run_checks(
                self.conn, self.now, run=lambda *args, **kwargs: _success()
            )
        self.assertEqual(alerts, [])
        send_alert.assert_not_called()

    def test_stale_import_and_health_data_alert(self):
        self.conn.execute(
            "INSERT INTO raw_import VALUES (?)", ("2026-10-28T07:59:59Z",)
        )
        self.conn.execute("INSERT INTO daily_metric VALUES (?)", ("2026-10-28",))
        run = lambda *args, **kwargs: _success()
        with patch.object(watchdog, "send_alert") as send_alert:
            alerts = watchdog.run_checks(self.conn, self.now, run=run)
        self.assertEqual(len(alerts), 2)
        send_alert.assert_called_once()

    def test_rclone_failure_and_inactive_service_alert(self):
        results = iter((_result(1), _success(), _result(3)))
        run = lambda *args, **kwargs: next(results)
        alerts = watchdog.external_alerts(run)
        self.assertEqual(len(alerts), 2)
        self.assertIn("Dropbox", alerts[0])
        self.assertIn("traning-bot", alerts[1])

    def test_missing_data_alerts(self):
        alerts = watchdog.database_alerts(self.conn, self.now)
        self.assertEqual(len(alerts), 2)

    def test_latest_fit_run_with_skipped_files_alerts(self):
        self.conn.execute(
            """INSERT INTO import_run
               (id, source_id, started_at, status, skipped_count, skipped_detail)
               VALUES (1, 1, '2026-11-01T07:00:00Z', 'ok', 7,
                       'cross_country_skiing/generic, racket/badminton')"""
        )
        alerts = watchdog.database_alerts(self.conn, self.now)
        self.assertEqual(len(alerts), 3)
        self.assertEqual(
            alerts[-1],
            "FIT-import 2026-11-01T07:00:00Z: 2 okända sporter: "
            "cross_country_skiing/generic, racket/badminton.",
        )

    def test_latest_fit_run_without_skips_does_not_alert_for_older_skips(self):
        self.conn.executemany(
            """INSERT INTO import_run
               (id, source_id, started_at, status, skipped_count, skipped_detail)
               VALUES (?, 1, ?, 'ok', ?, ?)""",
            (
                (1, "2026-10-31T07:00:00Z", 7,
                 "cross_country_skiing/generic, racket/badminton"),
                (2, "2026-11-01T07:00:00Z", 0, ""),
            ),
        )
        alerts = watchdog.database_alerts(self.conn, self.now)
        self.assertEqual(len(alerts), 2)

    def test_non_sport_skipped_files_still_alert(self):
        self.conn.execute(
            """INSERT INTO import_run
               (id, source_id, started_at, status, skipped_count, skipped_detail)
               VALUES (1, 1, '2026-11-01T07:00:00Z', 'ok', 2, '')"""
        )
        alerts = watchdog.database_alerts(self.conn, self.now)
        self.assertIn("hoppade över 2 filer", alerts[-1])


def _success():
    return subprocess.CompletedProcess(args=[], returncode=0)


def _result(returncode):
    return subprocess.CompletedProcess(args=[], returncode=returncode)


if __name__ == "__main__":
    unittest.main()
