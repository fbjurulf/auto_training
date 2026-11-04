-- A6: längsta sträcka där stopp under 30 s inte bryter (passdata.KORTA_STOPP_S),
-- bredvid longest_continuous_m där stopp på 12 s eller mer bryter. Fylls av
-- parse_fit; befintliga pass räknas om genom att FIT-filerna tolkas om.
ALTER TABLE workout_swim ADD COLUMN longest_with_short_stops_m REAL;

-- v_swim har en fast kolumnlista och tar inte med nya kolumner av sig själv.
DROP VIEW v_swim;
CREATE VIEW v_swim AS
SELECT v.*, d.pool_length_m, d.length_count, d.swolf_avg, d.swolf_best,
       d.stroke_count_total, d.strokes_per_length_avg, d.pace_s_per_100m,
       d.primary_stroke, d.rest_s_total, d.active_s,
       d.longest_continuous_m, d.longest_with_short_stops_m, d.freestyle_share
FROM v_workout v JOIN workout_swim d ON d.workout_id = v.id;
