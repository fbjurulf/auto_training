-- Rimlighetskontrollen: vad som redan varnats för passet. NULL = aldrig
-- kontrollerat, '' = inget att anmärka. Kör i samma lås följt av
-- `.venv/bin/python rimlighet.py --baslinje`, annars larmar nästa
-- pipelinekörning för alla gamla pass utanför gränserna.
ALTER TABLE workout ADD COLUMN rimlighet_varnad TEXT;
