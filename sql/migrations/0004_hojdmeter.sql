-- Del B: höjdmeter upp och ner, härledda ur höjdserien vid tolkning.
--
-- workout: ascent_m/descent_m för alla sporter med höjddata, ascent_apple_m
-- som kontroll (FIT session.total_ascent / HAE elevationUp).
--
-- workout_segment byggs om: kind får 'km' (kilometrar räknade ur record vid
-- tolkning - records sparas inte, så de går inte att räkna fram i efterhand)
-- och elevation_loss_m läggs till. CHECK går inte att ändra i SQLite utan
-- ombyggnad.
--
-- UTGÅENDE: workout_run.elevation_gain_m och elevation_loss_m skrivs inte
-- längre (gain var Apples värde, loss skrevs aldrig). Droppas när en vecka
-- med rena körningar har gått.

PRAGMA foreign_keys=OFF;
BEGIN;

ALTER TABLE workout ADD COLUMN ascent_m REAL;
ALTER TABLE workout ADD COLUMN descent_m REAL;
ALTER TABLE workout ADD COLUMN ascent_apple_m REAL;

CREATE TABLE workout_segment_ny (
    id            INTEGER PRIMARY KEY,
    workout_id    INTEGER NOT NULL REFERENCES workout(id) ON DELETE CASCADE,
    parent_id     INTEGER REFERENCES workout_segment(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    kind          TEXT    NOT NULL CHECK (kind IN ('lap','length','interval','rest','split','set','segment','km')),

    started_at    TEXT,
    duration_s    REAL,
    distance_m    REAL,
    hr_avg        INTEGER,
    hr_max        INTEGER,
    pace_s_per_km REAL,
    speed_kmh     REAL,
    cadence       REAL,
    power_w       REAL,
    elevation_gain_m REAL,
    elevation_loss_m REAL,

    -- Sportspecifikt per segment: swolf, stroke_count, stroke_style, ...
    extra         TEXT,

    UNIQUE (workout_id, seq)
);
INSERT INTO workout_segment_ny
    (id, workout_id, parent_id, seq, kind, started_at, duration_s, distance_m,
     hr_avg, hr_max, pace_s_per_km, speed_kmh, cadence, power_w, elevation_gain_m, extra)
SELECT id, workout_id, parent_id, seq, kind, started_at, duration_s, distance_m,
       hr_avg, hr_max, pace_s_per_km, speed_kmh, cadence, power_w, elevation_gain_m, extra
FROM workout_segment;
DROP TABLE workout_segment;
ALTER TABLE workout_segment_ny RENAME TO workout_segment;
CREATE INDEX ix_segment_workout ON workout_segment(workout_id, seq);
CREATE INDEX ix_segment_parent  ON workout_segment(parent_id);

COMMIT;
PRAGMA foreign_keys=ON;
PRAGMA foreign_key_check;
