"""Omtolkning av HAE-payloads som hoppats över innan aliaset fanns, som Pilates 2026-11-19."""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

os.environ.setdefault("TRANING_PARSE_LOG", os.path.join(tempfile.gettempdir(), "traning-test-parse.log"))

import omtolka_hae
import parse_hae
from tests.fixtures import build_db
from tests.replay import DATA_DIR

PILATES = DATA_DIR / "hae" / "2026-11-19_pilates_raw809.json.gz"
SIM = DATA_DIR / "hae" / "2026-09-30_swim_pool_raw95.json.gz"


def _workout(fixtur, **andra):
    w = json.loads(gzip.decompress(fixtur.read_bytes()))
    w.update(andra)
    return w


class OmtolkaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="traning-test-")
        self.db = os.path.join(self.tmp, "t.db")
        c = build_db(self.db)
        # Läget före migration 0009: Pilates saknar HAE-alias.
        c.execute("DELETE FROM sport_alias WHERE external_code='Pilates'")
        c.commit()
        c.close()
        p = patch.object(parse_hae, "DB_PATH", self.db)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _payload(self, *workouts):
        text = json.dumps({"data": {"workouts": list(workouts)}}, ensure_ascii=False)
        c = sqlite3.connect(self.db)
        rid = c.execute(
            """INSERT INTO raw_import (source_id, received_at, payload, payload_sha256,
                 byte_size, workout_count)
               VALUES ((SELECT id FROM source WHERE code='apple_health_hae'),
                       '2026-11-20T06:52:19Z', ?, ?, ?, ?)""",
            (text, hashlib.sha256(text.encode()).hexdigest(), len(text), len(workouts))).lastrowid
        c.commit()
        c.close()
        return rid

    def _alias(self):
        c = sqlite3.connect(self.db)
        c.execute("""INSERT INTO sport_alias (source_id, external_code, sport_id)
                     SELECT src.id, 'Pilates', s.id FROM source src, sport s
                     WHERE src.code='apple_health_hae' AND s.code='pilates'""")
        c.commit()
        c.close()

    def _omtolka(self, *namn):
        with redirect_stdout(StringIO()):
            return omtolka_hae.main(list(namn))

    def _rader(self, sql):
        c = sqlite3.connect(self.db)
        try:
            return [tuple(r) for r in c.execute(sql)]
        finally:
            c.close()

    def test_skipped_payloads_are_found_in_the_database_and_become_a_workout(self):
        pilates = _workout(PILATES)
        tva = self._payload(pilates)
        parse_hae.main()                       # hoppas över, payloaden är förbrukad
        tre = self._payload(_workout(SIM), pilates)
        parse_hae.main()
        bara_sim = self._payload(_workout(SIM, id="SIM-2"))
        parse_hae.main()
        self.assertEqual(self._rader("SELECT count(*) FROM v_workout WHERE sport_code='pilates'"), [(0,)])

        self._alias()
        parse_hae.main()
        self.assertEqual(self._rader("SELECT count(*) FROM v_workout WHERE sport_code='pilates'"), [(0,)],
                         "ett alias ensamt ger inget pass")

        self.assertEqual(self._omtolka("Pilates"), [tva, tre])
        self.assertNotIn(bara_sim, [tva, tre])
        self.assertEqual(self._rader("""SELECT external_id, started_at FROM v_workout
                                        WHERE sport_code='pilates'"""),
                         [(pilates["id"], "2026-11-19T17:11:35Z")])
        self.assertEqual(self._rader("SELECT count(*) FROM raw_import WHERE status<>'parsed'"), [(0,)])

    def test_second_run_finds_nothing(self):
        self._payload(_workout(PILATES))
        parse_hae.main()
        self._alias()
        self.assertEqual(len(self._omtolka("Pilates")), 1)
        self.assertEqual(self._omtolka("Pilates"), [])

    def test_workout_that_already_exists_is_not_reparsed(self):
        # Som Inomhus Cykling 2026-11-17: FIT-passet med samma UUID finns redan.
        w = _workout(PILATES, name="Inomhus Cykling")
        self._payload(w)
        c = sqlite3.connect(self.db)
        c.execute("DELETE FROM sport_alias WHERE external_code='Inomhus Cykling'")
        c.commit()
        c.close()
        parse_hae.main()
        c = sqlite3.connect(self.db)
        c.execute("""INSERT INTO workout (source_id, external_id, sport_id, started_at,
                       ended_at, local_date, duration_s, fit_enriched)
                     VALUES ((SELECT id FROM source WHERE code='healthfit_fit'), ?,
                             (SELECT id FROM sport WHERE code='bike_indoor'),
                             '2026-11-19T17:11:35Z', '2026-11-19T17:46:51Z', '2026-11-19', 2113, 1)""",
                  (w["id"],))
        c.execute("""INSERT INTO sport_alias (source_id, external_code, sport_id)
                     SELECT src.id, 'Inomhus Cykling', s.id FROM source src, sport s
                     WHERE src.code='apple_health_hae' AND s.code='bike_indoor'""")
        c.commit()
        c.close()
        self.assertEqual(self._omtolka("Inomhus Cykling"), [])

    def test_name_without_alias_stops_before_writing(self):
        rid = self._payload(_workout(PILATES))
        parse_hae.main()
        with self.assertRaises(SystemExit):
            self._omtolka("Pilates")
        self.assertEqual(self._rader(f"SELECT status FROM raw_import WHERE id={rid}"), [("parsed",)])


if __name__ == "__main__":
    unittest.main()
