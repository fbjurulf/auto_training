-- HAE kallar vandring 'Vandring' (avläst ur raw_import 561, 566, 569).
-- Utan aliaset hoppar parse_hae över varje vandringspass som okänd sport.
INSERT INTO sport_alias (source_id, external_code, sport_id)
SELECT src.id, 'Vandring', s.id
FROM source src, sport s
WHERE src.code = 'apple_health_hae' AND s.code = 'hike';
