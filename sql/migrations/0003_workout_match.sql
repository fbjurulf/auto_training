-- A8: varje gång en källa kopplas till ett pass som en annan källa redan
-- skapat sparas hur - på UUID eller på starttid. Tidsmatchningar kan i
-- teorin slå ihop två olika pass, så vakthunden larmar på dem.
CREATE TABLE workout_match (
    id          INTEGER PRIMARY KEY,
    workout_id  INTEGER NOT NULL REFERENCES workout(id) ON DELETE CASCADE,
    source_id   INTEGER NOT NULL REFERENCES source(id),
    external_id TEXT    NOT NULL,
    method      TEXT    NOT NULL CHECK (method IN ('uuid','tid')),
    matched_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX ix_workout_match_workout ON workout_match(workout_id);
