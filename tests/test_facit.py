"""Riktiga pass genom hela kedjan, jämförda mot Träningsappen.

Facit står i tests/data/facit.json. Stämmer inte ett värde är det en
avvikelse att rapportera - ändra inte facit eller toleranser för att få
testet grönt. Mått som inte finns än hoppas över med skäl, inte grönt.
"""
import json
import re
import shutil
import unittest
from datetime import date, timedelta

from tests.replay import DATA_DIR, replay

FACIT = json.loads((DATA_DIR / "facit.json").read_text(encoding="utf-8"))
conn = tmp = None


def setUpModule():
    global conn, tmp
    conn, tmp = replay()


def tearDownModule():
    conn.close()
    # Parsrarnas loggfiler hålls öppna av sina handlers - på Windows går
    # katalogen då inte att ta bort helt.
    shutil.rmtree(tmp, ignore_errors=True)


def _workout(local_date, sport_prefix):
    rows = conn.execute(
        "SELECT * FROM v_workout WHERE local_date = ? AND sport_code LIKE ?",
        (local_date, sport_prefix + "%")).fetchall()
    if len(rows) != 1:
        raise AssertionError(f"{len(rows)} pass {sport_prefix} {local_date}, väntade 1")
    return rows[0]


def _swim(workout_id):
    return conn.execute("SELECT * FROM workout_swim WHERE workout_id = ?",
                        (workout_id,)).fetchone()


def _top_segments(workout_id):
    return conn.execute(
        """SELECT * FROM workout_segment WHERE workout_id = ? AND parent_id IS NULL
           ORDER BY seq""", (workout_id,)).fetchall()


def _report_section(local_date, sport_name):
    import weekly
    d = date.fromisoformat(local_date)
    monday = d - timedelta(days=d.weekday())
    lines = weekly.build_report(conn, monday, monday + timedelta(days=6)).splitlines()
    start = next(i for i, l in enumerate(lines)
                 if l.startswith("### ") and local_date in l and sport_name in l)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
    return "\n".join(lines[start:end])


def _has_column(table, column):
    return column in [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


class SwimTests(unittest.TestCase):
    def test_2026_09_30_longest_continuous(self):
        f = FACIT["swim_2026_09_30"]
        w = _workout(f["local_date"], "swim")
        self.assertEqual(_swim(w["id"])["longest_continuous_m"], f["longest_continuous_m"])

    def test_2026_09_30_set_structure(self):
        f = FACIT["swim_2026_09_30"]
        w = _workout(f["local_date"], "swim")
        laps = [(s["kind"], s["distance_m"], round(s["duration_s"]))
                for s in _top_segments(w["id"])]
        first, rest, second = f["set"]
        expected = [("interval", first["distance_m"], first["duration_s"]),
                    ("rest", None, rest["rest_s"]),
                    ("interval", second["distance_m"], second["duration_s"])]
        found = any(laps[i:i + 3] == expected for i in range(len(laps) - 2))
        self.assertTrue(found, f"{expected} finns inte i varven {laps}")

    def test_2026_10_07_longest_continuous(self):
        f = FACIT["swim_2026_10_07"]
        w = _workout(f["local_date"], "swim")
        self.assertEqual(_swim(w["id"])["longest_continuous_m"], f["longest_continuous_m"])

    def test_2026_10_07_longest_with_short_stops(self):
        if not _has_column("workout_swim", "longest_with_short_stops_m"):
            self.skipTest("A6: workout_swim.longest_with_short_stops_m finns inte än")
        f = FACIT["swim_2026_10_07"]
        w = _workout(f["local_date"], "swim")
        self.assertEqual(_swim(w["id"])["longest_with_short_stops_m"],
                         f["longest_with_short_stops_m"])

    def test_2026_10_19_distance(self):
        f = FACIT["swim_2026_10_19"]
        w = _workout(f["local_date"], "swim")
        self.assertEqual(w["distance_m"], f["distance_m"])

    def test_2026_10_19_longest_with_short_stops(self):
        if not _has_column("workout_swim", "longest_with_short_stops_m"):
            self.skipTest("A6: workout_swim.longest_with_short_stops_m finns inte än")
        f = FACIT["swim_2026_10_19"]
        w = _workout(f["local_date"], "swim")
        self.assertEqual(_swim(w["id"])["longest_with_short_stops_m"],
                         f["longest_with_short_stops_m"])

    def test_2026_10_21_keeps_fit_laps_after_hae_rerun(self):
        # Regressionsspärr (A3): replayen kör HAE-payload 616 efter FIT-filen.
        # parse_hae får inte ersätta FIT:s varv med sina minutbaserade set.
        f = FACIT["swim_2026_10_21"]
        w = _workout(f["local_date"], "swim")
        laps = _top_segments(w["id"])
        self.assertEqual(len(laps), f["fit_laps"],
                         f"varv: {[(s['kind'], s['distance_m']) for s in laps]}")


class RunTests(unittest.TestCase):
    def test_2026_10_22_lap_2(self):
        f = FACIT["run_2026_10_22"]
        w = _workout(f["local_date"], "run")
        splits = [s for s in _top_segments(w["id"]) if s["kind"] == "split"]
        lap = splits[f["lap"] - 1]
        self.assertEqual(round(lap["distance_m"] / 1000, 2), f["lap_distance_km"])
        self.assertEqual(round(lap["duration_s"]), f["lap_duration_s"])

    def test_2026_10_22_blocks_sum_or_absent(self):
        f = FACIT["run_2026_10_22"]
        w = _workout(f["local_date"], "run")
        blocks = [s for s in _top_segments(w["id"]) if s["kind"] == "interval"]
        if not blocks:
            return
        total = sum(b["distance_m"] or 0 for b in blocks)
        self.assertAlmostEqual(total, f["blocks_total_m"],
                               delta=f["blocks_total_m"] * f["blocks_tolerance"],
                               msg=f"{len(blocks)} block summerar till {total:.0f} m")

    def test_2026_10_18_pause(self):
        f = FACIT["run_2026_10_18"]
        w = _workout(f["local_date"], "run")
        self.assertEqual(w["elapsed_s"] - w["duration_s"], f["pause_s"])

    def test_2026_10_18_no_integrity_warning(self):
        f = FACIT["run_2026_10_18"]
        section = _report_section(f["local_date"], "Löpning")
        self.assertEqual("Obs: varven summerar" in section, f["integrity_warning"], section)
        # A2a: distansvarv summerar till aktiv tid.
        self.assertIn("Varven räknas på aktiv tid", section)

    def test_2026_10_22_laps_counted_on_elapsed_time(self):
        # A2a-fynd, inte Träningsappen: egna varvtryck spänner över pauserna.
        section = _report_section(FACIT["run_2026_10_22"]["local_date"], "Löpning")
        self.assertIn("Varven räknas på förfluten tid", section)
        self.assertNotIn("Obs: varven summerar", section)

    def test_2026_10_04_single_workout(self):
        f = FACIT["run_2026_10_04"]
        rows = conn.execute("SELECT * FROM v_workout WHERE local_date = ?",
                            (f["local_date"],)).fetchall()
        self.assertEqual(len(rows), f["workout_count"])
        self.assertEqual(round(rows[0]["distance_m"] / 1000, 2), f["distance_km"])
        self.assertEqual(rows[0]["duration_s"], f["duration_s"])


# HAE:s segment känns igen på innehållet: löpsplittar har pace_s_per_km,
# simset har pace_s_per_100m i extra. FIT skriver aldrig någotdera.
HAE_ORIGIN = "(pace_s_per_km IS NOT NULL OR extra LIKE '%pace_s_per_100m%')"


class OriginTests(unittest.TestCase):
    def test_fit_enriched_workouts_have_no_hae_segments(self):
        # A3: kontrollen jämförde först antal segment, och missade pass 90 och
        # 93 där HAE och FIT råkade ge lika många. Ursprunget är det som räknas.
        rows = conn.execute(
            f"""SELECT w.local_date, w.sport_code, COUNT(g.id) n FROM v_workout w
                JOIN workout_segment g ON g.workout_id = w.id AND {HAE_ORIGIN}
                WHERE w.fit_enriched = 1 GROUP BY w.id""").fetchall()
        self.assertEqual([tuple(r) for r in rows], [])

    def test_fit_enriched_workouts_have_fit_laps(self):
        rows = conn.execute(
            """SELECT w.local_date, w.sport_code FROM v_workout w WHERE w.fit_enriched = 1
               AND NOT EXISTS (SELECT 1 FROM workout_segment g WHERE g.workout_id = w.id
                               AND g.extra LIKE '%lap_trigger%')""").fetchall()
        self.assertEqual([tuple(r) for r in rows], [])


class WeekTests(unittest.TestCase):
    def test_week_42_excluded_time(self):
        f = FACIT["week_42"]
        marks = ",".join("?" * len(f["zone_share_excludes"]))
        sek = conn.execute(
            f"""SELECT SUM(duration_s) FROM v_workout WHERE local_date BETWEEN ? AND ?
                AND sport_code IN ({marks})""",
            (f["start"], f["end"], *f["zone_share_excludes"])).fetchone()[0]
        self.assertEqual(round(sek / 3600), f["excluded_hours"])

    def test_week_42_zone_basis_counts_only_run_bike_swim(self):
        # Andelen går inte att testa i v42 - all zontid ligger i Z1-Z2 med
        # och utan vandring. Underlagsraden gör det: där syns vandringen.
        import weekly
        f = FACIT["week_42"]
        marks = ",".join("?" * len(f["zone_basis_sports"]))
        params = (f["start"], f["end"], *f["zone_basis_sports"])
        zone_s = conn.execute(
            f"""SELECT SUM(z.seconds) FROM workout_zone z JOIN v_workout w ON w.id = z.workout_id
                WHERE w.local_date BETWEEN ? AND ? AND w.sport_code IN ({marks})""",
            params).fetchone()[0]
        trained_s = conn.execute(
            f"""SELECT SUM(w.duration_s) FROM v_workout w
                WHERE w.local_date BETWEEN ? AND ? AND w.sport_code IN ({marks})
                AND EXISTS (SELECT 1 FROM workout_zone z WHERE z.workout_id = w.id)""",
            params).fetchone()[0]

        lines = "\n".join(weekly.zone_section(conn, date.fromisoformat(f["start"]),
                                              date.fromisoformat(f["end"])))
        match = re.search(r"Underlaget är (\S+) av (\S+) träningstid", lines)
        self.assertIsNotNone(match, lines)
        self.assertEqual(match.groups(), (weekly.hms(zone_s), weekly.hms(trained_s)))


if __name__ == "__main__":
    unittest.main()
