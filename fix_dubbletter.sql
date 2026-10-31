-- Städar bort dubbletter där samma pass finns från både HAE och FIT.
-- FIT-raden behålls: den har varv, längder, pulszoner och min-puls.
-- ON DELETE CASCADE tar segment, sim-/löpdetaljer och zoner med sig,
-- därför måste foreign_keys vara på.

PRAGMA foreign_keys=ON;

-- 1. Visa vad som kommer att tas bort.
SELECT 'TAS BORT' flagga, w.id, s.code kalla, w.started_at,
       ROUND(w.distance_m) m, w.hr_avg
FROM workout w
JOIN source s ON s.id = w.source_id AND s.code = 'apple_health_hae'
WHERE EXISTS (
    SELECT 1 FROM workout f
    JOIN source fs ON fs.id = f.source_id AND fs.code = 'healthfit_fit'
    WHERE f.external_id = w.external_id AND f.id <> w.id)
ORDER BY w.started_at;

-- 2. Ta bort dem.
DELETE FROM workout WHERE id IN (
    SELECT w.id FROM workout w
    JOIN source s ON s.id = w.source_id AND s.code = 'apple_health_hae'
    WHERE EXISTS (
        SELECT 1 FROM workout f
        JOIN source fs ON fs.id = f.source_id AND fs.code = 'healthfit_fit'
        WHERE f.external_id = w.external_id AND f.id <> w.id));

-- 3. Kvarvarande dubbletter på starttid (om något saknade UUID).
SELECT 'KVAR ATT KOLLA' flagga, w.id, s.code, w.started_at, ROUND(w.distance_m) m
FROM workout w JOIN source s ON s.id = w.source_id
WHERE EXISTS (
    SELECT 1 FROM workout x WHERE x.id <> w.id
      AND x.sport_id = w.sport_id
      AND ABS(strftime('%s', x.started_at) - strftime('%s', w.started_at)) < 300)
ORDER BY w.started_at;
