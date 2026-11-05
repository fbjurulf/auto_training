-- HAE kallar öppet vatten 'Öppet vatten Simning' (avläst ur raw_import 17-19).
-- Utan aliaset hoppar parse_hae över passet som okänd sport.
INSERT INTO sport_alias (source_id, external_code, sport_id)
SELECT src.id, 'Öppet vatten Simning', s.id
FROM source src, sport s
WHERE src.code = 'apple_health_hae' AND s.code = 'swim_open';
