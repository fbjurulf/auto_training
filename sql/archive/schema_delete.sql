-- Dölj pass som inte var träning - klockan startar ibland av sig själv.
--
-- Raden tas inte bort: external_id måste finnas kvar, annars skapar
-- importen passet på nytt vid nästa körning. deleted_at gör den osynlig
-- i alla vyer och beräkningar men behåller spärren mot återimport.

ALTER TABLE workout ADD COLUMN deleted_at TEXT;

DROP VIEW IF EXISTS v_workout;
CREATE VIEW v_workout AS
SELECT w.*, s.code AS sport_code, s.name_sv AS sport_name, src.code AS source_code
FROM workout w
JOIN sport s   ON s.id = w.sport_id
JOIN source src ON src.id = w.source_id
WHERE w.duplicate_of_id IS NULL AND w.deleted_at IS NULL;

-- Vyerna nedan bygger på v_workout och måste skapas om efter den.
DROP VIEW IF EXISTS v_swim;
CREATE VIEW v_swim AS
SELECT v.*, d.pool_length_m, d.length_count, d.swolf_avg, d.swolf_best,
       d.stroke_count_total, d.strokes_per_length_avg, d.pace_s_per_100m,
       d.primary_stroke, d.rest_s_total, d.active_s,
       d.longest_continuous_m, d.freestyle_share
FROM v_workout v JOIN workout_swim d ON d.workout_id = v.id;

DROP VIEW IF EXISTS v_run;
CREATE VIEW v_run AS
SELECT v.*, d.pace_s_per_km, d.elevation_gain_m, d.elevation_loss_m,
       d.cadence_avg_spm, d.ground_contact_ms, d.vertical_oscillation_cm,
       d.stride_length_m, d.power_avg_w
FROM v_workout v JOIN workout_run d ON d.workout_id = v.id;

DROP VIEW IF EXISTS v_bike;
CREATE VIEW v_bike AS
SELECT v.*, d.power_avg_w, d.power_max_w, d.power_normalized_w,
       d.cadence_avg_rpm, d.elevation_gain_m, d.speed_avg_kmh, d.speed_max_kmh
FROM v_workout v JOIN workout_bike d ON d.workout_id = v.id;

DROP VIEW IF EXISTS v_week;
CREATE VIEW v_week AS
SELECT strftime('%Y-%W', local_date) AS iso_week,
       sport_code,
       COUNT(*)              AS pass,
       SUM(duration_s)/3600.0 AS timmar,
       SUM(distance_m)/1000.0 AS km,
       ROUND(AVG(hr_avg))     AS puls_snitt
FROM v_workout
GROUP BY 1, 2;

DROP VIEW IF EXISTS v_zon;
CREATE VIEW v_zon AS
SELECT w.id workout_id, w.local_date, s.code sport_code, z.zone, z.seconds,
       ROUND(100.0 * z.seconds / SUM(z.seconds) OVER (PARTITION BY w.id), 1) andel
FROM workout_zone z
JOIN workout w ON w.id = z.workout_id
JOIN sport s ON s.id = w.sport_id
WHERE w.deleted_at IS NULL;

SELECT 'dolda pass', COUNT(*) FROM workout WHERE deleted_at IS NOT NULL;
