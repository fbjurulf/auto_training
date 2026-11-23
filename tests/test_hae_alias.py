"""HAE-namn genom den riktiga parse_hae: känt alias ger ett pass, okänt namn sparas för larm."""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("TRANING_NOTIFY_LOG", os.path.join(tempfile.gettempdir(), "traning-test-notify.log"))
os.environ.setdefault("TRANING_PARSE_LOG", os.path.join(tempfile.gettempdir(), "traning-test-parse.log"))

import fitstatus
import notify
import parse_hae
import passvisning
from tests.fixtures import build_db
from tests.replay import DATA_DIR

FIXTUR = DATA_DIR / "hae" / "2026-09-30_swim_pool_raw95.json.gz"
# Pilates 2026-11-19: ingen rutt, ingen banlängd, bara puls och energi.
PILATES = DATA_DIR / "hae" / "2026-11-19_pilates_raw809.json.gz"


class HaeNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="traning-test-")
        self.db = os.path.join(self.tmp, "t.db")
        build_db(self.db).close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tolka(self, namn, fixtur=FIXTUR):
        # Ett HAE-pass utan banlängd, som raw_import 17-19 för öppet vatten.
        w = json.loads(gzip.decompress(fixtur.read_bytes()))
        w["name"] = namn
        w.pop("lapLength", None)
        text = json.dumps({"data": {"workouts": [w]}}, ensure_ascii=False)
        c = sqlite3.connect(self.db)
        c.execute("""INSERT INTO raw_import (source_id, received_at, payload, payload_sha256,
                       byte_size, workout_count)
                     VALUES ((SELECT id FROM source WHERE code='apple_health_hae'),
                             '2026-09-30T07:00:00Z', ?, ?, ?, 1)""",
                  (text, hashlib.sha256(text.encode()).hexdigest(), len(text)))
        c.commit()
        c.close()
        with patch.object(parse_hae, "DB_PATH", self.db):
            parse_hae.main()
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        return c

    def test_open_water_from_hae_becomes_a_workout(self):
        c = self._tolka("Öppet vatten Simning")
        rad = c.execute("SELECT id, sport_code FROM v_workout").fetchone()
        self.assertEqual(rad["sport_code"], "swim_open")
        self.assertIn(fitstatus.OPPET_VATTEN, notify.build_message(c, rad["id"], "swim_open"))
        self.assertEqual(c.execute("SELECT skipped_detail FROM import_run").fetchone()[0], "")
        c.close()

    def test_unknown_name_is_saved_and_no_workout_is_created(self):
        c = self._tolka("Utomhus Cykling")
        self.assertEqual(c.execute("SELECT count(*) FROM v_workout").fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT skipped_detail FROM import_run").fetchone()[0], "Utomhus Cykling")
        c.close()


    def test_pilates_from_hae_only_becomes_a_workout_in_workout_other(self):
        # Pilates 2026-11-19 saknade sport och alias och hoppades över. FIT-filen
        # finns också, men passet ska gå att få fram ur HAE ensamt.
        c = self._tolka("Pilates", PILATES)
        rad = c.execute("SELECT id, sport_code, started_at, duration_s FROM v_workout").fetchone()
        self.assertEqual((rad["sport_code"], rad["started_at"], rad["duration_s"]),
                         ("pilates", "2026-11-19T17:11:35Z", 2113))
        self.assertEqual(c.execute("SELECT skipped_detail FROM import_run").fetchone()[0], "")
        self.assertEqual(c.execute(
            "SELECT counts_in_intensity_ratio FROM sport WHERE code='pilates'").fetchone()[0], 0)
        self.assertIn("== workout_other: inga rader ==", passvisning.rendera(c, rad["id"]))
        c.close()

    def test_every_hae_sport_in_workout_other_becomes_a_workout(self):
        # Nästa sport av samma slag behöver bara en migration med sport och
        # alias: den tas med här utan att testet ändras. Pilates-fixturen har
        # inget som hör till en viss sport, så den bär vilket namn som helst.
        c = sqlite3.connect(self.db)
        namn = [r for r in c.execute(
            """SELECT a.external_code, s.code FROM sport_alias a
               JOIN sport s ON s.id = a.sport_id JOIN source src ON src.id = a.source_id
               WHERE src.code = 'apple_health_hae' AND s.detail_table = 'workout_other'""")]
        c.close()
        self.assertIn(("Pilates", "pilates"), namn)
        for alias, sport in namn:
            with self.subTest(alias=alias):
                self.tearDown()
                self.setUp()
                c = self._tolka(alias, PILATES)
                self.assertEqual([tuple(r) for r in c.execute("SELECT sport_code FROM v_workout")],
                                 [(sport,)])
                for tabell in ("workout_swim", "workout_run", "workout_bike"):
                    self.assertEqual(c.execute(f"SELECT count(*) FROM {tabell}").fetchone()[0], 0)
                c.close()


if __name__ == "__main__":
    unittest.main()
