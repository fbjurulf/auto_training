-- Utökning för FIT-import. Kör en gång.

-- Minpuls saknades - FIT ger den per pass och per varv.
ALTER TABLE workout ADD COLUMN hr_min INTEGER;

-- Simfält som bara FIT kan fylla.
ALTER TABLE workout_swim ADD COLUMN longest_continuous_m REAL;
ALTER TABLE workout_swim ADD COLUMN freestyle_share REAL;
ALTER TABLE workout_swim ADD COLUMN active_s INTEGER;

-- Konfigurerbara pulszoner. Ändras med UPDATE, inte med kodändring.
CREATE TABLE IF NOT EXISTS hr_zone (
    zone       TEXT    PRIMARY KEY,
    hr_min     INTEGER NOT NULL,
    hr_max     INTEGER NOT NULL,
    label      TEXT
);

INSERT OR REPLACE INTO hr_zone (zone, hr_min, hr_max, label) VALUES
    ('Z1-Z2',   0, 157, 'Lugnt'),
    ('Z3',    158, 164, 'Medel'),
    ('Z4',    165, 176, 'Tröskel'),
    ('Z5',    177, 250, 'Max');

CREATE TABLE IF NOT EXISTS workout_zone (
    workout_id INTEGER NOT NULL REFERENCES workout(id) ON DELETE CASCADE,
    zone       TEXT    NOT NULL REFERENCES hr_zone(zone),
    seconds    REAL    NOT NULL,
    PRIMARY KEY (workout_id, zone)
);

-- FIT-källan.
INSERT OR IGNORE INTO source (code, name, kind)
VALUES ('healthfit_fit', 'HealthFit FIT-filer via Dropbox', 'file');

INSERT OR IGNORE INTO sport_alias (source_id, external_code, sport_id)
SELECT s.id, x.column1, sp.id
FROM (VALUES
    ('swimming/lap_swimming',  'swim_pool'),
    ('swimming/open_water',    'swim_open'),
    ('running/generic',        'run'),
    ('running/treadmill',      'run_treadmill'),
    ('cycling/generic',        'bike'),
    ('cycling/indoor_cycling', 'bike_indoor'),
    ('training/strength_training', 'strength'),
    ('tennis/generic',         'tennis'),
    ('hiking/generic',         'hike'),
    ('walking/generic',        'walk')
) AS x
JOIN sport sp  ON sp.code = x.column2
JOIN source s  ON s.code = 'healthfit_fit';

SELECT 'zoner', COUNT(*) FROM hr_zone
UNION ALL SELECT 'fit-alias', COUNT(*) FROM sport_alias
WHERE source_id = (SELECT id FROM source WHERE code='healthfit_fit');
