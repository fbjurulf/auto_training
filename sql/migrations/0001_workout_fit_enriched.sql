-- A3: FIT är sanningskälla för allt FIT levererar. parse_fit sätter
-- fit_enriched=1 på varje pass det skapar eller berikar, och parse_hae får
-- då bara fylla tomma fält - aldrig ersätta segment, simstatistik eller
-- löpdetaljer.
ALTER TABLE workout ADD COLUMN fit_enriched INTEGER NOT NULL DEFAULT 0
    CHECK (fit_enriched IN (0,1));

-- Befintliga pass: FIT-källa, eller HAE-pass där FIT skrivit hr_min (bara
-- parse_fit skriver hr_min). Verifierat 2026-11-03: täcker exakt de 26
-- HAE-pass som har en FIT-fil.
UPDATE workout SET fit_enriched = 1
WHERE source_id = (SELECT id FROM source WHERE code = 'healthfit_fit')
   OR hr_min IS NOT NULL;
