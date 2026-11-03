import subprocess
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import watchdog
from tests.fixtures import build_db


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.conn = build_db()
        self.now = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)

    def tearDown(self):
        self.conn.close()

    def test_recent_data_and_healthy_services_do_not_alert(self):
        _raw_import(self.conn, "2026-10-31T12:00:00Z")
        _daily_metric(self.conn, "2026-10-31")
        with patch.object(watchdog, "send_alert") as send_alert:
            alerts = watchdog.run_checks(
                self.conn, self.now, run=lambda *args, **kwargs: _success()
            )
        self.assertEqual(alerts, [])
        send_alert.assert_not_called()

    def test_stale_import_and_health_data_alert(self):
        _raw_import(self.conn, "2026-10-28T07:59:59Z")
        _daily_metric(self.conn, "2026-10-28")
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
        _fit_run(self.conn, 1, "2026-11-01T07:00:00Z", 7,
                 "cross_country_skiing/generic, racket/badminton")
        alerts = watchdog.database_alerts(self.conn, self.now)
        self.assertEqual(len(alerts), 3)
        self.assertEqual(
            alerts[-1],
            "FIT-import 2026-11-01T07:00:00Z: 2 okända sporter: "
            "cross_country_skiing/generic, racket/badminton.",
        )

    def test_latest_fit_run_without_skips_does_not_alert_for_older_skips(self):
        _fit_run(self.conn, 1, "2026-10-31T07:00:00Z", 7,
                 "cross_country_skiing/generic, racket/badminton")
        _fit_run(self.conn, 2, "2026-11-01T07:00:00Z", 0, "")
        alerts = watchdog.database_alerts(self.conn, self.now)
        self.assertEqual(len(alerts), 2)

    def test_non_sport_skipped_files_still_alert(self):
        _fit_run(self.conn, 1, "2026-11-01T07:00:00Z", 2, "")
        alerts = watchdog.database_alerts(self.conn, self.now)
        self.assertIn("hoppade över 2 filer", alerts[-1])


def _source_id(conn, code):
    return conn.execute("SELECT id FROM source WHERE code = ?", (code,)).fetchone()[0]


def _raw_import(conn, received_at):
    conn.execute(
        """INSERT INTO raw_import (source_id, received_at, payload, payload_sha256)
           VALUES (?, ?, '{}', 'sha')""",
        (_source_id(conn, "apple_health_hae"), received_at),
    )


def _daily_metric(conn, local_date):
    conn.execute(
        """INSERT INTO daily_metric (local_date, metric, source_id)
           VALUES (?, 'resting_heart_rate', ?)""",
        (local_date, _source_id(conn, "apple_health_metrics")),
    )


def _fit_run(conn, run_id, started_at, skipped_count, skipped_detail):
    conn.execute(
        """INSERT INTO import_run
           (id, source_id, run_type, started_at, status, skipped_count, skipped_detail)
           VALUES (?, ?, 'poll', ?, 'ok', ?, ?)""",
        (run_id, _source_id(conn, "healthfit_fit"), started_at,
         skipped_count, skipped_detail),
    )


def _success():
    return subprocess.CompletedProcess(args=[], returncode=0)


def _result(returncode):
    return subprocess.CompletedProcess(args=[], returncode=returncode)


if __name__ == "__main__":
    unittest.main()
