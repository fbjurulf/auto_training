-- Vyerna byggs om så att de nya FIT-kolumnerna kommer med.
-- En vy har fast kolumnlista och påverkas inte av ALTER TABLE.

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

-- Andel av passets tid per pulszon.
DROP VIEW IF EXISTS v_zon;
CREATE VIEW v_zon AS
SELECT w.id workout_id, w.local_date, s.code sport_code, z.zone, z.seconds,
       ROUND(100.0 * z.seconds / SUM(z.seconds) OVER (PARTITION BY w.id), 1) andel
FROM workout_zone z
JOIN workout w ON w.id = z.workout_id
JOIN sport s ON s.id = w.sport_id;
