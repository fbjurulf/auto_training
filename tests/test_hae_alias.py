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
from tests.fixtures import build_db
from tests.replay import DATA_DIR

FIXTUR = DATA_DIR / "hae" / "2026-09-30_swim_pool_raw95.json.gz"


class HaeNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="traning-test-")
        self.db = os.path.join(self.tmp, "t.db")
        build_db(self.db).close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tolka(self, namn):
        # Ett HAE-pass utan banlängd, som raw_import 17-19 för öppet vatten.
        w = json.loads(gzip.decompress(FIXTUR.read_bytes()))
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


if __name__ == "__main__":
    unittest.main()
