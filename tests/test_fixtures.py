import unittest

from tests.fixtures import build_db

# Radantal i produktionen 2026-11-03, plus det migrationerna lägger till
# (0001: +1 sport_alias). Genereras grundfilerna om ska siffrorna
# verifieras mot Pi:n på nytt.
REFERENCE_COUNTS = {"sport": 13, "sport_alias": 15, "source": 5, "hr_zone": 4}
VIEWS = ("v_workout", "v_swim", "v_run", "v_bike", "v_zon")


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.conn = build_db()

    def tearDown(self):
        self.conn.close()

    def test_reference_counts_match_production(self):
        for table, expected in REFERENCE_COUNTS.items():
            with self.subTest(table=table):
                count = self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                self.assertEqual(count, expected)

    def test_views_are_queryable(self):
        for view in VIEWS:
            with self.subTest(view=view):
                self.conn.execute(f"SELECT * FROM {view} LIMIT 1").fetchall()

    def test_reference_data_has_no_foreign_key_violations(self):
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_every_alias_points_to_a_sport(self):
        orphans = self.conn.execute(
            """SELECT a.external_code FROM sport_alias a
               LEFT JOIN sport s ON s.id = a.sport_id WHERE s.id IS NULL"""
        ).fetchall()
        self.assertEqual(orphans, [])


if __name__ == "__main__":
    unittest.main()
