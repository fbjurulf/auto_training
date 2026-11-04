"""A3a: pass vars FIT-segment skrivits över av HAE repareras genom att
FIT-filen tolkas om. I produktionen gäller det pass 83, 88 och 91 - de två
senare finns i fixturerna (2026-10-18 och 2026-10-21)."""
import gzip
import json
import shutil
import unittest

from tests.replay import DATA_DIR, replay

conn = tmp = None
# FIT-filerna slås upp på prefix: HealthFit skriver hårt mellanslag i
# "Apple Watch".
CASES = {
    "2026-10-18": ("run", "2026-10-18_run_raw592.json.gz", "2026-10-18-144945-"),
    "2026-10-21": ("swim", "2026-10-21_swim_pool_raw616.json.gz", "2026-10-21-073119-"),
}


def setUpModule():
    global conn, tmp
    conn, tmp = replay()


def tearDownModule():
    conn.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _fit_top_segments(fit_name):
    import parse_fit
    m = parse_fit.read_fit(DATA_DIR / "fit" / fit_name)
    is_swim = m["session"][0].get("sport") == "swimming"
    blocks = [] if is_swim else parse_fit.timer_blocks(m.get("event", []), m.get("record", []))
    return len(m.get("lap", [])) + len(blocks)


def _hae_segments(workout_id):
    from tests.test_facit import HAE_ORIGIN
    return conn.execute(
        f"SELECT count(*) FROM workout_segment WHERE workout_id=? AND {HAE_ORIGIN}",
        (workout_id,)).fetchone()[0]


def _top_segment_count(workout_id):
    # Varv och block - kilometrarna (del B) är en egen nivå.
    return conn.execute(
        """SELECT count(*) FROM workout_segment WHERE workout_id=? AND parent_id IS NULL
           AND kind != 'km'""", (workout_id,)).fetchone()[0]


class RepairTests(unittest.TestCase):
    def test_reparsed_fit_restores_fit_segments(self):
        import parse_fit
        import parse_hae
        for day, (sport, hae_file, fit_prefix) in CASES.items():
            fit_name = next((DATA_DIR / "fit").glob(fit_prefix + "*.fit")).name
            with self.subTest(day=day):
                wid = conn.execute(
                    "SELECT id FROM v_workout WHERE local_date=? AND sport_code LIKE ?",
                    (day, sport + "%")).fetchone()[0]

                # Produktionens skadade tillstånd: den gamla vägen där HAE:s
                # detaljer skrevs ovanpå FIT:s.
                w = json.loads(gzip.decompress((DATA_DIR / "hae" / hae_file).read_bytes()))
                if sport == "run":
                    parse_hae.save_run(conn, wid, w, parse_hae.parse_dt(w["start"]))
                else:
                    parse_hae.save_swim(conn, wid, w)
                conn.commit()
                expected = _fit_top_segments(fit_name)
                self.assertNotEqual(_top_segment_count(wid), expected,
                                    "skadan gick inte att återskapa")
                self.assertGreater(_hae_segments(wid), 0,
                                   "ursprungskontrollen känner inte igen HAE:s segment")

                shutil.copy(DATA_DIR / "fit" / fit_name, tmp / "fit" / fit_name)
                raw_rows = conn.execute("SELECT count(*) FROM raw_import").fetchone()[0]
                parse_fit.main([tmp / "fit" / fit_name])
                self.assertEqual(_top_segment_count(wid), expected)
                self.assertEqual(_hae_segments(wid), 0)
                self.assertEqual(conn.execute("SELECT count(*) FROM raw_import").fetchone()[0],
                                 raw_rows, "omtolkningen lade en dubblett i raw_import")


if __name__ == "__main__":
    unittest.main()
