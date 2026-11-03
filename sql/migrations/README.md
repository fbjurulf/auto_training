# Schemamigrationer

## Regler

- Varje schemaändring läggs som en numrerad migrationsfil här och körs mot
  Pi:n i samma steg. Ingen ALTER för hand direkt mot traning.db.
- `../schema_base.sql` och `../seed_reference.sql` skrivs aldrig för hand.
  De genereras bara om från produktionen när vi vill ha en ny utgångspunkt.
  Migrationer som då redan ingår i produktionens schema tas bort härifrån i
  samma commit.

En databas byggs alltså av `schema_base.sql`, sedan `seed_reference.sql`,
sedan migrationerna här i nummerordning.

## Migrationsfiler

Namn: `0001_kort_beskrivning.sql`, `0002_...`. Nummer återanvänds aldrig.

Köra mot Pi:n (efter deploy, så att filen finns där). Under samma lås som
pipeline.sh, annars kan den krocka med cron-körningen:

    ssh fredrik@100.107.27.54 "cd /home/fredrik/traning && \
      flock /tmp/traning.lock sqlite3 traning.db < sql/migrations/0001_kort_beskrivning.sql"

## Generera om grundfilerna

Kör från repots rot i bash (inte PowerShell 5.1 — dess `>` skriver UTF-16):

    ssh fredrik@100.107.27.54 "cd /home/fredrik/traning && \
      sqlite3 -readonly traning.db '.schema'" > sql/schema_base.sql

    ssh fredrik@100.107.27.54 "cd /home/fredrik/traning && \
      sqlite3 -readonly traning.db '.dump sport sport_alias source hr_zone exercise'" \
      | grep '^INSERT' > sql/seed_reference.sql

Verifiera sedan att radantalen i sport, sport_alias, source och hr_zone
stämmer mot Pi:n, och att vyerna v_workout, v_swim, v_run, v_bike och v_zon
går att fråga i en databas byggd av filerna.

## Känt om referensdatan

- `exercise` och `strength_set` är tomma även i produktionen (2026-11-03).
  Styrkefixturer måste därför ta med egna övningar.
