#!/usr/bin/env bash
set -euo pipefail

exec 9>/tmp/traning.lock
flock -w 300 9 || { echo "Kunde inte ta låset på 5 min"; exit 1; }

cd /home/fredrik/traning
git pull --ff-only

while IFS= read -r line; do
  key="${line%%=*}"
  [[ -z "$key" || "$key" == \#* ]] && continue
  grep -q "^${key}=" .env || { echo "SAKNAS i .env: $key"; exit 1; }
done < .env.example

for svc in traning-bot traning-ingest; do
  if ! diff -q "$svc.service" "/etc/systemd/system/$svc.service" >/dev/null 2>&1; then
    echo "Service-filen $svc har ändrats. Kör manuellt:"
    echo "  sudo cp $svc.service /etc/systemd/system/ && sudo systemctl daemon-reload"
    exit 1
  fi
done

bash -n pipeline.sh && bash -n weekly.sh && bash -n watchdog.sh

set -a; source .env; set +a
if [[ -z "${TELEGRAM_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
  echo "TELEGRAM_TOKEN och TELEGRAM_CHAT_ID måste vara ifyllda för watchdog"
  exit 1
fi

if ! sqlite3 traning.db "PRAGMA table_info(import_run)" |
    cut -d'|' -f2 | grep -qx skipped_detail; then
  sqlite3 traning.db "ALTER TABLE import_run ADD COLUMN skipped_detail TEXT"
fi

.venv/bin/python -c "import parse_hae, parse_fit, parse_metrics, notify, weekly, telegram_logg, ingest_app, watchdog"

sudo systemctl restart traning-bot
sudo systemctl restart traning-ingest

sleep 3
systemctl is-active --quiet traning-bot    || { echo "traning-bot startade inte"; exit 1; }
systemctl is-active --quiet traning-ingest || { echo "traning-ingest startade inte"; exit 1; }

watchdog_cron='0 8 * * * /bin/bash /home/fredrik/traning/watchdog.sh >> /home/fredrik/traning/logs/cron.log 2>&1'
(crontab -l 2>/dev/null | grep -vF '/home/fredrik/traning/watchdog.sh' || true
 printf '%s\n' "$watchdog_cron") | crontab -

echo "Deploy OK: $(git rev-parse --short HEAD)"
