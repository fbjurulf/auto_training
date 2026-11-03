CREATE TABLE source (
    id          INTEGER PRIMARY KEY,
    code        TEXT    NOT NULL UNIQUE,   -- 'apple_health_hae', 'garmin_connect', 'manual'
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('push','pull','manual','file')),
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE sport (
    id              INTEGER PRIMARY KEY,
    code            TEXT    NOT NULL UNIQUE,   -- 'swim_pool', 'run_outdoor', 'strength'
    name_sv         TEXT    NOT NULL,
    category        TEXT    NOT NULL CHECK (category IN ('endurance','strength','ballsport','mobility','other')),
    detail_table    TEXT,                      -- 'workout_swim' | 'workout_run' | ... | NULL
    tracks_distance INTEGER NOT NULL DEFAULT 0 CHECK (tracks_distance IN (0,1)),
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);
CREATE TABLE sport_alias (
    source_id     INTEGER NOT NULL REFERENCES source(id),
    external_code TEXT    NOT NULL,            -- 'HKWorkoutActivityTypeSwimming', 'lap_swimming'
    sport_id      INTEGER NOT NULL REFERENCES sport(id),
    PRIMARY KEY (source_id, external_code)
);
CREATE TABLE exercise (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    muscle_group TEXT,
    active       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);
CREATE TABLE raw_import (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES source(id),
    received_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    payload       TEXT    NOT NULL,
    payload_sha256 TEXT   NOT NULL,
    byte_size     INTEGER,
    workout_count INTEGER,
    status        TEXT    NOT NULL DEFAULT 'received'
                          CHECK (status IN ('received','parsed','partial','failed','ignored')),
    error         TEXT
);
CREATE INDEX ix_raw_import_hash ON raw_import(payload_sha256);
CREATE INDEX ix_raw_import_received ON raw_import(source_id, received_at);
CREATE TABLE import_run (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES source(id),
    run_type      TEXT    NOT NULL CHECK (run_type IN ('push','poll','backfill','manual')),
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    status        TEXT    NOT NULL DEFAULT 'running'
                          CHECK (status IN ('running','ok','failed')),
    new_count     INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    error         TEXT
, skipped_detail TEXT);
CREATE INDEX ix_import_run_started ON import_run(source_id, started_at);
CREATE TABLE workout (
    id              INTEGER PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES source(id),
    external_id     TEXT    NOT NULL,          -- HKWorkout UUID / Garmin activityId / 'manual:<uuid>'
    raw_import_id   INTEGER REFERENCES raw_import(id),
    sport_id        INTEGER NOT NULL REFERENCES sport(id),

    started_at      TEXT    NOT NULL,          -- UTC
    ended_at        TEXT    NOT NULL,
    tz_offset_min   INTEGER,                   -- lokal offset vid passet, för resor
    local_date      TEXT    NOT NULL,          -- 'YYYY-MM-DD' lokalt; veckogruppering utan tz-matte

    duration_s      INTEGER NOT NULL CHECK (duration_s > 0),  -- aktiv tid
    elapsed_s       INTEGER,                                  -- inkl. pauser
    distance_m      REAL    CHECK (distance_m IS NULL OR distance_m >= 0),
    hr_avg          INTEGER CHECK (hr_avg IS NULL OR hr_avg BETWEEN 25 AND 250),
    hr_max          INTEGER CHECK (hr_max IS NULL OR hr_max BETWEEN 25 AND 250),
    kcal_active     REAL,
    kcal_total      REAL,
    rpe             INTEGER CHECK (rpe IS NULL OR rpe BETWEEN 1 AND 10),
    notes           TEXT,

    device          TEXT,                      -- 'Apple Watch Series 9', 'Garmin Forerunner 965'
    indoor          INTEGER CHECK (indoor IN (0,1)),

    -- Sätts när samma fysiska pass finns från två källor (t.ex. övergångsperiod
    -- Apple -> Garmin). Raden ligger kvar men räknas inte i statistiken.
    duplicate_of_id INTEGER REFERENCES workout(id),

    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')), notified_at TEXT, hr_min INTEGER, deleted_at TEXT,

    UNIQUE (source_id, external_id)            -- <- idempotensen
);
CREATE INDEX ix_workout_started   ON workout(started_at);
CREATE INDEX ix_workout_local_date ON workout(local_date);
CREATE INDEX ix_workout_sport_date ON workout(sport_id, local_date);
CREATE INDEX ix_workout_overlap   ON workout(sport_id, started_at, duration_s);
CREATE TRIGGER trg_workout_updated_at
AFTER UPDATE ON workout FOR EACH ROW
BEGIN
    UPDATE workout SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
    WHERE id = NEW.id;
END;
CREATE TABLE workout_swim (
    workout_id          INTEGER PRIMARY KEY REFERENCES workout(id) ON DELETE CASCADE,
    pool_length_m       REAL,
    length_count        INTEGER,
    swolf_avg           REAL,
    swolf_best          REAL,
    stroke_count_total  INTEGER,
    strokes_per_length_avg REAL,
    pace_s_per_100m     REAL,
    primary_stroke      TEXT,      -- 'freestyle','breaststroke','backstroke','butterfly','mixed'
    rest_s_total        INTEGER
, longest_continuous_m REAL, freestyle_share REAL, active_s INTEGER);
CREATE TABLE workout_run (
    workout_id          INTEGER PRIMARY KEY REFERENCES workout(id) ON DELETE CASCADE,
    pace_s_per_km       REAL,
    elevation_gain_m    REAL,
    elevation_loss_m    REAL,
    cadence_avg_spm     REAL,
    ground_contact_ms   REAL,
    vertical_oscillation_cm REAL,
    stride_length_m     REAL,
    power_avg_w         REAL
);
CREATE TABLE workout_bike (
    workout_id          INTEGER PRIMARY KEY REFERENCES workout(id) ON DELETE CASCADE,
    power_avg_w         REAL,
    power_max_w         REAL,
    power_normalized_w  REAL,
    cadence_avg_rpm     REAL,
    elevation_gain_m    REAL,
    speed_avg_kmh       REAL,
    speed_max_kmh       REAL
);
CREATE TABLE workout_strength (
    workout_id       INTEGER PRIMARY KEY REFERENCES workout(id) ON DELETE CASCADE,
    total_volume_kg  REAL,
    exercise_count   INTEGER,
    set_count        INTEGER
);
CREATE TABLE strength_set (
    id           INTEGER PRIMARY KEY,
    workout_id   INTEGER NOT NULL REFERENCES workout(id) ON DELETE CASCADE,
    exercise_id  INTEGER NOT NULL REFERENCES exercise(id),
    seq          INTEGER NOT NULL,
    set_no       INTEGER NOT NULL,
    reps         INTEGER,
    weight_kg    REAL,
    rpe          INTEGER CHECK (rpe IS NULL OR rpe BETWEEN 1 AND 10),
    notes        TEXT,
    UNIQUE (workout_id, seq)
);
CREATE INDEX ix_strength_set_exercise ON strength_set(exercise_id);
CREATE TABLE workout_other (
    workout_id INTEGER PRIMARY KEY REFERENCES workout(id) ON DELETE CASCADE,
    sub_type   TEXT,
    extra      TEXT    -- JSON, t.ex. {"motstandare":"Anders","set":"6-4 3-6 7-5"}
);
CREATE TABLE workout_segment (
    id            INTEGER PRIMARY KEY,
    workout_id    INTEGER NOT NULL REFERENCES workout(id) ON DELETE CASCADE,
    parent_id     INTEGER REFERENCES workout_segment(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    kind          TEXT    NOT NULL CHECK (kind IN ('lap','length','interval','rest','split','set','segment')),

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

    -- Sportspecifikt per segment: swolf, stroke_count, stroke_style, ...
    extra         TEXT,

    UNIQUE (workout_id, seq)
);
CREATE INDEX ix_segment_workout ON workout_segment(workout_id, seq);
CREATE INDEX ix_segment_parent  ON workout_segment(parent_id);
CREATE TABLE daily_metric (
    local_date TEXT    NOT NULL,
    metric     TEXT    NOT NULL,
    qty        REAL,
    units      TEXT,
    extra      TEXT,
    source_id  INTEGER NOT NULL REFERENCES source(id),
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (local_date, metric)
);
CREATE INDEX ix_daily_metric ON daily_metric(metric, local_date);
CREATE TABLE hr_zone (
    zone       TEXT    PRIMARY KEY,
    hr_min     INTEGER NOT NULL,
    hr_max     INTEGER NOT NULL,
    label      TEXT
);
CREATE TABLE workout_zone (
    workout_id INTEGER NOT NULL REFERENCES workout(id) ON DELETE CASCADE,
    zone       TEXT    NOT NULL REFERENCES hr_zone(zone),
    seconds    REAL    NOT NULL,
    PRIMARY KEY (workout_id, zone)
);
CREATE VIEW v_workout AS
SELECT w.*, s.code AS sport_code, s.name_sv AS sport_name, src.code AS source_code
FROM workout w
JOIN sport s   ON s.id = w.sport_id
JOIN source src ON src.id = w.source_id
WHERE w.duplicate_of_id IS NULL AND w.deleted_at IS NULL
/* v_workout(id,source_id,external_id,raw_import_id,sport_id,started_at,ended_at,tz_offset_min,local_date,duration_s,elapsed_s,distance_m,hr_avg,hr_max,kcal_active,kcal_total,rpe,notes,device,indoor,duplicate_of_id,created_at,updated_at,notified_at,hr_min,deleted_at,sport_code,sport_name,source_code) */;
CREATE VIEW v_swim AS
SELECT v.*, d.pool_length_m, d.length_count, d.swolf_avg, d.swolf_best,
       d.stroke_count_total, d.strokes_per_length_avg, d.pace_s_per_100m,
       d.primary_stroke, d.rest_s_total, d.active_s,
       d.longest_continuous_m, d.freestyle_share
FROM v_workout v JOIN workout_swim d ON d.workout_id = v.id
/* v_swim(id,source_id,external_id,raw_import_id,sport_id,started_at,ended_at,tz_offset_min,local_date,duration_s,elapsed_s,distance_m,hr_avg,hr_max,kcal_active,kcal_total,rpe,notes,device,indoor,duplicate_of_id,created_at,updated_at,notified_at,hr_min,deleted_at,sport_code,sport_name,source_code,pool_length_m,length_count,swolf_avg,swolf_best,stroke_count_total,strokes_per_length_avg,pace_s_per_100m,primary_stroke,rest_s_total,active_s,longest_continuous_m,freestyle_share) */;
CREATE VIEW v_run AS
SELECT v.*, d.pace_s_per_km, d.elevation_gain_m, d.elevation_loss_m,
       d.cadence_avg_spm, d.ground_contact_ms, d.vertical_oscillation_cm,
       d.stride_length_m, d.power_avg_w
FROM v_workout v JOIN workout_run d ON d.workout_id = v.id
/* v_run(id,source_id,external_id,raw_import_id,sport_id,started_at,ended_at,tz_offset_min,local_date,duration_s,elapsed_s,distance_m,hr_avg,hr_max,kcal_active,kcal_total,rpe,notes,device,indoor,duplicate_of_id,created_at,updated_at,notified_at,hr_min,deleted_at,sport_code,sport_name,source_code,pace_s_per_km,elevation_gain_m,elevation_loss_m,cadence_avg_spm,ground_contact_ms,vertical_oscillation_cm,stride_length_m,power_avg_w) */;
CREATE VIEW v_bike AS
SELECT v.*, d.power_avg_w, d.power_max_w, d.power_normalized_w,
       d.cadence_avg_rpm, d.elevation_gain_m, d.speed_avg_kmh, d.speed_max_kmh
FROM v_workout v JOIN workout_bike d ON d.workout_id = v.id
/* v_bike(id,source_id,external_id,raw_import_id,sport_id,started_at,ended_at,tz_offset_min,local_date,duration_s,elapsed_s,distance_m,hr_avg,hr_max,kcal_active,kcal_total,rpe,notes,device,indoor,duplicate_of_id,created_at,updated_at,notified_at,hr_min,deleted_at,sport_code,sport_name,source_code,power_avg_w,power_max_w,power_normalized_w,cadence_avg_rpm,elevation_gain_m,speed_avg_kmh,speed_max_kmh) */;
CREATE VIEW v_week AS
SELECT strftime('%Y-%W', local_date) AS iso_week,
       sport_code,
       COUNT(*)              AS pass,
       SUM(duration_s)/3600.0 AS timmar,
       SUM(distance_m)/1000.0 AS km,
       ROUND(AVG(hr_avg))     AS puls_snitt
FROM v_workout
GROUP BY 1, 2
/* v_week(iso_week,sport_code,pass,timmar,km,puls_snitt) */;
CREATE VIEW v_zon AS
SELECT w.id workout_id, w.local_date, s.code sport_code, z.zone, z.seconds,
       ROUND(100.0 * z.seconds / SUM(z.seconds) OVER (PARTITION BY w.id), 1) andel
FROM workout_zone z
JOIN workout w ON w.id = z.workout_id
JOIN sport s ON s.id = w.sport_id
WHERE w.deleted_at IS NULL
/* v_zon(workout_id,local_date,sport_code,zone,seconds,andel) */;
