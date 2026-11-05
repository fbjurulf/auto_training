"""
/veckorapport och söndagsrapporten är en beräkning, inte två. Testen går genom
hela kommandot (telegram_logg.hantera) och hela söndagskörningen (weekly.main).
"""
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("TRANING_BOT_LOG", os.path.join(tempfile.gettempdir(), "traning-test-bot.log"))
os.environ.setdefault("TRANING_WEEKLY_LOG", os.path.join(tempfile.gettempdir(), "traning-test-weekly.log"))
os.environ.setdefault("TRANING_REPORT_DIR", os.path.join(tempfile.gettempdir(), "traning-test-rapporter"))

import telegram_logg
import weekly
from tests.fixtures import build_db
from tests.replay import replay

conn = tmp = None
SONDAG_20 = datetime(2026, 10, 18, 20, 0)     # v42, som cron kör den
SONDAG_2359 = datetime(2026, 10, 18, 23, 59)
TORSDAG = datetime(2026, 10, 15, 13, 55)


def setUpModule():
    global conn, tmp
    conn, tmp = replay()


def tearDownModule():
    conn.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _kommando(text, nu):
    """Kör kommandot och ger (svarstext, skickade filer som (namn, rubrik, innehåll))."""
    filer = []

    def fanga(metod, falt, files=None, token=None):
        namn, innehall = files["document"]
        filer.append((namn, falt["caption"], innehall.decode("utf-8")))

    with patch.object(telegram_logg, "DB_PATH", os.environ["TRANING_DB"]), \
            patch.object(weekly, "_nu", return_value=nu), \
            patch.object(weekly, "telegram", fanga):
        return telegram_logg.hantera(text), filer


def _sondagskorning(nu):
    """Som weekly.sh: hela weekly.main utan argument. Ger filens text."""
    ut = Path(tempfile.mkdtemp(prefix="traning-test-"))
    try:
        with patch.object(weekly, "DB_PATH", os.environ["TRANING_DB"]), \
                patch.object(weekly, "OUT_DIR", ut), \
                patch.object(weekly, "TOKEN", ""), \
                patch.object(weekly, "_nu", return_value=nu), \
                patch.object(sys, "argv", ["weekly.py"]):
            weekly.main()
        return next(ut.glob("*.md")).read_text(encoding="utf-8")
    finally:
        shutil.rmtree(ut, ignore_errors=True)


class SameReportTests(unittest.TestCase):
    def test_sunday_2359_is_exactly_the_sunday_report(self):
        svar, filer = _kommando("/veckorapport", SONDAG_2359)
        self.assertIsNone(svar)
        namn, rubrik, text = filer[0]
        self.assertEqual(text, _sondagskorning(SONDAG_20))
        self.assertEqual((namn, rubrik), ("vecka-2026-42.md", "Veckounderlag 2026-v42"))

    def test_the_comparison_has_real_content(self):
        text = _sondagskorning(SONDAG_20)
        self.assertIn("### tor 2026-10-15 - Vandring", text)
        self.assertIn("Räknat på", text)

    def test_named_finished_week_is_the_same_report(self):
        _, filer = _kommando("/veckorapport 42", datetime(2026, 11, 5, 13, 55))
        self.assertEqual(filer[0][0], "vecka-2026-42.md")
        self.assertNotIn("delvis", filer[0][2])
        self.assertEqual(filer[0][2].split("## Volym senaste")[0],
                         _sondagskorning(SONDAG_20).split("## Volym senaste")[0])


class PartialWeekTests(unittest.TestCase):
    def _text(self, kommando="/veckorapport", nu=TORSDAG):
        return _kommando(kommando, nu)[1][0]

    def test_first_line_says_partial(self):
        _, _, text = self._text()
        self.assertEqual(text.splitlines()[0], "**Vecka 42, delvis - måndag till torsdag 13:55.**")

    def test_file_and_caption_say_partial(self):
        namn, rubrik, _ = self._text()
        self.assertEqual(namn, "vecka-2026-42-delvis.md")
        self.assertIn("delvis", rubrik)

    def test_naming_the_current_week_is_also_partial(self):
        self.assertEqual(self._text("/veckorapport 42")[2], self._text()[2])

    def test_monday_has_no_range(self):
        _, _, text = self._text(nu=datetime(2026, 10, 12, 9, 5))
        self.assertEqual(text.splitlines()[0], "**Vecka 42, delvis - måndag 09:05.**")

    def test_sunday_report_has_no_partial_line(self):
        text = _sondagskorning(SONDAG_20)
        self.assertNotIn("delvis", text)
        self.assertIn("Veckan är komplett.", text)
        self.assertNotIn("pågående", text)

    def test_comparison_is_called_out(self):
        self.assertIn("Jämförelserna ställer delveckan mot hela veckor", self._text()[2])

    def test_days_after_the_end_are_not_counted(self):
        # Vandringarna 10-16 och 10-17 och löpningen 10-18 har inte skett på torsdagen.
        _, _, text = self._text()
        self.assertIn("### tor 2026-10-15 - Vandring", text)
        self.assertNotIn("2026-10-16", text.split("## Volym senaste")[0].split("## Pass")[1])
        self.assertRegex(text, r"\| Vandring \| 1 \|")


class PartialHealthTests(unittest.TestCase):
    def test_health_stops_at_the_end_not_at_the_clock(self):
        # Testet körs långt efter veckan: läser hälsoavsnittet klockan syns alla sju dagar.
        c = build_db()
        c.row_factory = sqlite3.Row
        for i in range(7):
            c.execute("""INSERT INTO daily_metric (local_date, metric, qty, source_id)
                         VALUES (?, 'sleep_analysis', ?, 1)""",
                      ((date(2026, 10, 12) + timedelta(days=i)).isoformat(), 7.0 if i < 4 else 3.0))
        text = weekly.build_report(c, date(2026, 10, 12), date(2026, 10, 18), slut=TORSDAG)
        halsa = text.split("## Hälsodata")[1].split("## Pass")[0]
        self.assertIn("tor 15/10", halsa)
        self.assertNotIn("fre 16/10", halsa)
        self.assertIn("| Sömn | 7,0 h | 7,0 h | 7,0 h |", halsa, "fredagens 3 h har inte skett")
        self.assertNotIn("3,0", halsa)


class ReadOnlyTests(unittest.TestCase):
    def test_nothing_is_written(self):
        db = Path(os.environ["TRANING_DB"])
        rapporter = set(weekly.OUT_DIR.glob("*"))
        fore = conn.execute("SELECT id, notified_at, updated_at FROM workout ORDER BY id").fetchall()
        fore_hash = hashlib.sha256(db.read_bytes()).hexdigest()
        _kommando("/veckorapport", TORSDAG)
        _kommando("/veckorapport 42", SONDAG_2359)
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), fore_hash)
        efter = conn.execute("SELECT id, notified_at, updated_at FROM workout ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in fore], [tuple(r) for r in efter])
        self.assertEqual(set(weekly.OUT_DIR.glob("*")), rapporter, "ingen rapportfil får skrivas")

    def test_connection_cannot_write(self):
        def forsok_skriva(c, start, end, slut=None):
            c.execute("UPDATE workout SET notified_at='x'")
        with patch.object(weekly, "build_report", forsok_skriva):
            svar, filer = _kommando("/veckorapport", TORSDAG)
        self.assertIn("Kunde inte ta fram veckorapporten", svar)
        self.assertEqual(filer, [])
        self.assertEqual(conn.execute(
            "SELECT count(*) FROM workout WHERE notified_at='x'").fetchone()[0], 0)


class ArgumentTests(unittest.TestCase):
    NU = datetime(2026, 11, 5, 13, 55)

    def test_week_number(self):
        self.assertEqual(telegram_logg.rapportvecka("44", self.NU),
                         (date(2026, 10, 26), date(2026, 11, 1)))

    def test_no_argument_is_current_week(self):
        self.assertEqual(telegram_logg.rapportvecka("", self.NU),
                         (date(2026, 11, 2), date(2026, 11, 8)))

    def test_week_ahead_means_last_year(self):
        self.assertEqual(telegram_logg.rapportvecka("50", self.NU)[0], date(2025, 12, 8))

    def test_bad_arguments_get_an_answer_and_no_file(self):
        for arg in ("abc", "0", "54", "2026-44"):
            with self.subTest(arg=arg):
                svar, filer = _kommando(f"/veckorapport {arg}", self.NU)
                self.assertTrue(svar)
                self.assertEqual(filer, [])

    def test_send_failure_is_reported(self):
        def trasig(*a, **k):
            raise RuntimeError("Telegram nere")
        with patch.object(telegram_logg, "DB_PATH", os.environ["TRANING_DB"]), \
                patch.object(weekly, "telegram", trasig):
            self.assertIn("Telegram nere", telegram_logg.hantera("/veckorapport"))


if __name__ == "__main__":
    unittest.main()
