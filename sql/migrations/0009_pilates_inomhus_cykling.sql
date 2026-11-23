-- Pilates fanns varken som sport eller alias. Passet 2026-11-19 (HAE-id
-- B0BCC175-...) hoppades över av båda parsrarna. Payloads att tolka om väljs
-- ur databasen av omtolka_hae.py, inte ur en id-lista.
-- Namnen är avlästa byte för byte: HAE kallar det 'Pilates' (raw_import 809),
-- FIT 'fitness_equipment/pilates' (import_run.skipped_detail).
-- Ingår inte i 80/20-andelen (A4): bara löpning, cykel och simning gör det.
INSERT INTO sport (code, name_sv, category, detail_table, tracks_distance, active,
                   counts_in_intensity_ratio)
VALUES ('pilates', 'Pilates', 'mobility', 'workout_other', 0, 1, 0);

INSERT INTO sport_alias (source_id, external_code, sport_id)
SELECT src.id, 'Pilates', s.id
FROM source src, sport s
WHERE src.code = 'apple_health_hae' AND s.code = 'pilates';

INSERT INTO sport_alias (source_id, external_code, sport_id)
SELECT src.id, 'fitness_equipment/pilates', s.id
FROM source src, sport s
WHERE src.code = 'healthfit_fit' AND s.code = 'pilates';

-- HAE kallar spinning 'Inomhus Cykling' (raw_import 782-794, passet 2026-11-17).
-- Passet kom in via FIT (pass 109), men HAE-sidan hoppade över det.
INSERT INTO sport_alias (source_id, external_code, sport_id)
SELECT src.id, 'Inomhus Cykling', s.id
FROM source src, sport s
WHERE src.code = 'apple_health_hae' AND s.code = 'bike_indoor';
