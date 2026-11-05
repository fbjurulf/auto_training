"""
/pass visar allt som finns lagrat om ett pass. Testen läser schemat på egen hand och
kräver varje kolumn och värde i utdata, så att de inte delar en lista med koden.
"""
import html
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

os.environ.setdefault("TRANING_BOT_LOG", os.path.join(tempfile.gettempdir(), "traning-test-bot.log"))
os.environ.setdefault("TRANING_WEEKLY_LOG", os.path.join(tempfile.gettempdir(), "traning-test-weekly.log"))
os.environ.setdefault("TRANING_REPORT_DIR", os.path.join(tempfile.gettempdir(), "traning-test-rapporter"))

import fitstatus
import passvisning
import telegram_logg
import weekly
from tests.fixtures import build_db
from tests.replay import replay


def _pass(c, sport="run", kalla="healthfit_fit", **falt):
    varden = {"source_id": c.execute("SELECT id FROM source WHERE code=?", (kalla,)).fetchone()[0],
              "external_id": f"x{c.execute('SELECT count(*) FROM workout').fetchone()[0]}",
              "sport_id": c.execute("SELECT id FROM sport WHERE code=?", (sport,)).fetchone()[0],
              "started_at": "2026-10-05T18:00:00Z", "ended_at": "2026-10-05T19:00:00Z",
              "local_date": "2026-10-05", "duration_s": 3600, "elapsed_s": 3600}
    varden.update(falt)
    kol = ", ".join(varden)
    return c.execute(f"INSERT INTO workout ({kol}) VALUES ({', '.join('?' * len(varden))})",
                     tuple(varden.values())).lastrowid


def _allt_lagrat(c, wid):
    """(tabell, kolumn, värde) för varje fält om passet, ur schemat."""
    ut = []
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        kolumner = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')]
        if t == "workout":
            villkor = "id"
        elif "workout_id" in kolumner:
            villkor = "workout_id"
        else:
            continue
        for r in c.execute(f'SELECT * FROM "{t}" WHERE {villkor}=?', (wid,)).fetchall():
            ut += [(t, k, r[k]) for k in r.keys()]
    return ut


class Faltkontroll(unittest.TestCase):
    def assertAllaFalt(self, c, wid):
        text = passvisning.rendera(c, wid)
        falt = _allt_lagrat(c, wid)
        self.assertTrue(falt)
        for t, k, v in falt:
            vantat = "NULL" if v is None else str(v).replace("\n", "\\n")
            self.assertIn(f"{k}={vantat}", text, f"{t}.{k} saknas i pass {wid}")
        return text


class RealWorkoutTests(Faltkontroll):
    @classmethod
    def setUpClass(cls):
        cls.conn, cls.tmp = replay()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_field_of_every_workout_in_the_fixtures(self):
        for (wid,) in self.conn.execute("SELECT id FROM workout").fetchall():
            with self.subTest(pass_=wid):
                self.assertAllaFalt(self.conn, wid)

    def test_each_segment_appears_once_children_under_parent(self):
        wid = self.conn.execute(
            "SELECT id FROM v_workout WHERE sport_code='swim_pool' AND local_date='2026-10-21'"
        ).fetchone()[0]
        rader = passvisning.rendera(self.conn, wid).split("== workout_segment")[1].splitlines()[1:]
        ids = [r.strip().split(" ")[0] for r in rader if r.strip()]
        n = self.conn.execute("SELECT count(*) FROM workout_segment WHERE workout_id=?", (wid,)).fetchone()[0]
        self.assertEqual(len(ids), n)
        self.assertEqual(len(set(ids)), n)
        self.assertFalse(rader[0].startswith(" "), "aggregatsegment först")
        barn = next(i for i, r in enumerate(rader) if r.startswith("  "))
        self.assertIn("parent_id=NULL", rader[barn - 1])
        self.assertNotIn("parent_id=NULL", rader[barn])


class EachKindTests(Faltkontroll):
    def setUp(self):
        self.c = build_db()
        self.c.row_factory = sqlite3.Row

    def test_strength_with_sets(self):
        wid = _pass(self.c, "strength", fit_enriched=1)
        self.c.execute("INSERT INTO workout_strength (workout_id) VALUES (?)", (wid,))
        ex = self.c.execute("INSERT INTO exercise (name) VALUES ('Knäböj')").lastrowid
        self.c.execute("""INSERT INTO strength_set (workout_id, exercise_id, seq, set_no, reps, weight_kg)
                          VALUES (?,?,1,1,8,60)""", (wid, ex))
        text = self.assertAllaFalt(self.c, wid)
        self.assertIn("== workout_strength: 1 rad ==", text)
        self.assertIn("== strength_set: 1 rad ==", text)
        self.assertIn("Sporttabell: workout_strength", text)

    def test_manual_workout_through_the_real_logging(self):
        f = telegram_logg.tolka_rad(self.c, "styrka 45min rpe7 Tungt i benen")
        wid, _, _ = telegram_logg.skapa_pass(self.c, f)
        text = self.assertAllaFalt(self.c, wid)
        self.assertIn("Källa: manual", text)
        self.assertIn("FIT-status: ingen, manuellt loggat", text)
        self.assertIn("notes=Tungt i benen", text)

    def test_manual_bike_gets_a_row_with_speed_from_distance_and_time(self):
        f = telegram_logg.tolka_rad(self.c, "cykel 1h45 45km")
        wid, _, _ = telegram_logg.skapa_pass(self.c, f)
        rad = self.c.execute("SELECT * FROM workout_bike WHERE workout_id=?", (wid,)).fetchone()
        self.assertEqual(rad["speed_avg_kmh"], round(45 / 1.75, 2))
        self.assertIsNone(rad["speed_max_kmh"], "ett manuellt pass har ingen maxfart")
        self.assertIsNone(rad["power_avg_w"])
        mon, sun = weekly.week_bounds(date.today())
        self.assertIn("snittfart 25,7 km/h", weekly.build_report(self.c, mon, sun))

    def test_manual_bike_without_distance_has_a_row_and_no_speed(self):
        f = telegram_logg.tolka_rad(self.c, "spinning 45min")
        wid, _, _ = telegram_logg.skapa_pass(self.c, f)
        rad = self.c.execute("SELECT * FROM workout_bike WHERE workout_id=?", (wid,)).fetchone()
        self.assertIsNotNone(rad)
        self.assertIsNone(rad["speed_avg_kmh"])

    def test_manual_other_sport_gets_no_bike_row(self):
        f = telegram_logg.tolka_rad(self.c, "tennis 90min")
        telegram_logg.skapa_pass(self.c, f)
        self.assertEqual(self.c.execute("SELECT count(*) FROM workout_bike").fetchone()[0], 0)

    def test_open_water_says_why_and_shows_missing_swim_row(self):
        text = self.assertAllaFalt(self.c, _pass(self.c, "swim_open", fit_enriched=1))
        self.assertIn(fitstatus.OPPET_VATTEN, text)
        self.assertIn("== workout_swim: inga rader ==", text)

    def test_pool_swim_without_lengths_like_76(self):
        text = self.assertAllaFalt(self.c, _pass(self.c, "swim_pool", fit_enriched=1))
        self.assertIn(fitstatus.LANGDER_SAKNAS, text)

    def test_bike_and_hike_show_their_empty_detail_tables(self):
        for sport, tabell in (("bike", "workout_bike"), ("hike", "workout_other")):
            with self.subTest(sport=sport):
                text = self.assertAllaFalt(self.c, _pass(self.c, sport, fit_enriched=1))
                self.assertIn(f"== {tabell}: inga rader ==", text)

    def test_hae_only_has_its_marking(self):
        text = self.assertAllaFalt(self.c, _pass(self.c, "run", "apple_health_hae"))
        self.assertIn("Källa: apple_health_hae", text)
        self.assertIn("FIT-berikat: nej", text)
        self.assertIn("**HAE-pass:**", text)

    def test_hidden_workout_is_shown_and_says_so(self):
        text = self.assertAllaFalt(self.c, _pass(self.c, deleted_at="2026-10-09T15:09:37Z"))
        self.assertIn("Dolt: ja, 2026-10-09T15:09:37Z", text)

    def test_duplicate_is_visible_from_both_sides(self):
        a = _pass(self.c)
        b = _pass(self.c, duplicate_of_id=a)
        self.assertIn(f"Dubblett av: {a}", passvisning.rendera(self.c, b))
        self.assertIn("== workout.duplicate_of_id: 1 rad ==", passvisning.rendera(self.c, a))

    def test_new_column_and_new_table_appear_without_code_change(self):
        wid = _pass(self.c)
        self.c.execute("ALTER TABLE workout ADD COLUMN nytt_falt TEXT")
        self.c.execute("ALTER TABLE workout_zone ADD COLUMN zonextra TEXT")
        self.c.execute("""CREATE TABLE workout_framtid (workout_id INTEGER REFERENCES workout(id), matt REAL)""")
        self.c.execute("INSERT INTO workout_framtid VALUES (?, 1.5)", (wid,))
        text = passvisning.rendera(self.c, wid)
        self.assertIn("nytt_falt=NULL", text)
        self.assertIn("== workout_framtid: 1 rad ==", text)
        self.assertIn("matt=1.5", text)

    def test_missing_workout(self):
        self.assertIsNone(passvisning.rendera(self.c, 999))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="traning-test-")
        self.db = os.path.join(self.tmp, "t.db")
        self.c = build_db(self.db)
        self.c.row_factory = sqlite3.Row

    def tearDown(self):
        self.c.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _kor(self, text):
        self.c.commit()
        filer = []

        def fanga(metod, falt, files=None, token=None):
            namn, innehall = files["document"]
            filer.append((namn, falt["caption"], innehall.decode("utf-8")))
        with patch.object(telegram_logg, "DB_PATH", self.db), patch.object(weekly, "telegram", fanga):
            return telegram_logg.hantera(text), filer

    def test_short_output_is_a_message_in_pre(self):
        wid = _pass(self.c, notes="puls <150 & bra")
        svar, filer = self._kor(f"/pass {wid}")
        self.assertEqual(filer, [])
        self.assertTrue(svar.startswith("<pre>") and svar.endswith("</pre>"))
        self.assertIn("puls &lt;150 &amp; bra", svar)

    def test_long_output_is_a_file_with_nothing_cut(self):
        wid = _pass(self.c)
        for i in range(100):
            self.c.execute("""INSERT INTO workout_segment (workout_id, seq, kind, duration_s)
                              VALUES (?,?,'split',60)""", (wid, i))
        svar, filer = self._kor(f"/pass {wid}")
        self.assertIsNone(svar)
        namn, rubrik, innehall = filer[0]
        self.assertEqual(namn, f"pass-{wid}.txt")
        self.assertIn(f"Pass {wid}", rubrik)
        self.assertEqual(innehall, passvisning.rendera(self.c, wid))
        self.assertIn("seq=99", innehall)

    def test_escaped_length_decides_message_or_file(self):
        # Rå text under gränsen men över den escapad får inte bli ett avvisat meddelande.
        wid = _pass(self.c, notes="<" * 1500)
        self.assertLess(len(passvisning.rendera(self.c, wid)), telegram_logg.TELEGRAM_MAX)
        svar, filer = self._kor(f"/pass {wid}")
        self.assertIsNone(svar)
        self.assertEqual(len(filer), 1)
        wid2 = _pass(self.c)
        svar, _ = self._kor(f"/pass {wid2}")
        self.assertLessEqual(len(svar), telegram_logg.TELEGRAM_MAX)
        self.assertEqual(html.unescape(svar[5:-6]), passvisning.rendera(self.c, wid2))

    def test_bad_input_gets_an_answer(self):
        for text, vantat in (("/pass", "Skriv passets id"), ("/pass abc", "Skriv passets id"),
                             ("/pass 999", "Hittar inget pass med id 999")):
            with self.subTest(text=text):
                svar, filer = self._kor(text)
                self.assertIn(vantat, svar)
                self.assertEqual(filer, [])

    def test_read_only_and_nothing_changes(self):
        wid = _pass(self.c)
        self.c.commit()
        with open(self.db, "rb") as f:
            fore = f.read()
        self._kor(f"/pass {wid}")
        with open(self.db, "rb") as f:
            self.assertEqual(f.read(), fore)

        def forsok_skriva(c, _):
            c.execute("UPDATE workout SET notes='x'")
        with patch.object(passvisning, "rendera", forsok_skriva):
            svar, _ = self._kor(f"/pass {wid}")
        self.assertIn("Kunde inte visa passet", svar)
        self.assertIn("readonly", svar)


if __name__ == "__main__":
    unittest.main()
