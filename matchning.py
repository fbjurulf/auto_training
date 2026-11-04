"""
Matchning av ett inkommande pass mot de som redan finns - samma regel för
FIT och HAE (A8).

Steg 1 är HealthKit-UUID. Steg 2 är samma sport och starttid inom fem
minuter, för FIT-filer som saknar UUID och därför får en filhash som
external_id. Tidsmatchningar är de riskabla: i teorin kan två olika pass av
samma sport som startar inom fem minuter slås ihop. Därför sparas varje
matchning mellan källor i workout_match, och vakthunden larmar på dem som
gjorts på tid.
"""

MATCH_FONSTER_S = 300


def find_workout(conn, hk_uuid, start_utc, sport_id):
    """Ger (workout_id, 'uuid' | 'tid') eller (None, None)."""
    if hk_uuid:
        # Söker i alla källor: passet kan redan ha skapats av den andra.
        #
        # FILTRERA INTE BORT DOLDA PASS HÄR (deleted_at / duplicate_of_id).
        # Ett dolt pass ligger kvar just för att dess external_id ska blockera
        # återimport. Filtreras raden bort hittar matchningen inget, parsern
        # skapar passet på nytt vid nästa körning, och ett pass du dolt - en
        # klocka som startade av sig själv, en dubblett - kommer tillbaka.
        # Tidssteget nedan filtrerar, eftersom det inte är en identitet.
        rad = conn.execute(
            "SELECT id FROM workout WHERE external_id = ? ORDER BY id LIMIT 1",
            (hk_uuid,)).fetchone()
        if rad:
            return rad[0], "uuid"

    rad = conn.execute(
        """SELECT id, ABS(strftime('%s', started_at) - strftime('%s', ?)) diff
           FROM workout WHERE sport_id = ? AND diff <= ?
             AND deleted_at IS NULL AND duplicate_of_id IS NULL
           ORDER BY diff LIMIT 1""",
        (start_utc, sport_id, MATCH_FONSTER_S)).fetchone()
    return (rad[0], "tid") if rad else (None, None)


def record_match(conn, workout_id, source_id, external_id, method):
    """Spara att källan kopplats till ett befintligt pass - en gång per par."""
    conn.execute(
        """INSERT INTO workout_match (workout_id, source_id, external_id, method)
           SELECT ?, ?, ?, ?
           WHERE NOT EXISTS (SELECT 1 FROM workout_match
                             WHERE workout_id = ? AND source_id = ? AND external_id = ?)""",
        (workout_id, source_id, external_id, method, workout_id, source_id, external_id))
