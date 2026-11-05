-- A4: vilka sporter 80/20-andelen räknas på. Tolv timmars vandring i Z1 dränker
-- annars fördelningen. Övriga sporter visas i zontabellen men ingår inte, och
-- en ny sport räknas inte förrän någon valt det.
ALTER TABLE sport ADD COLUMN counts_in_intensity_ratio INTEGER NOT NULL DEFAULT 0
    CHECK (counts_in_intensity_ratio IN (0,1));
UPDATE sport SET counts_in_intensity_ratio = 1
WHERE code IN ('run', 'run_treadmill', 'bike', 'bike_indoor', 'swim_pool', 'swim_open');
