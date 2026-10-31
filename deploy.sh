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

bash -n pipeline.sh && bash -n weekly.sh
.venv/bin/python -c "import parse_hae, parse_fit, parse_metrics, notify, weekly, telegram_logg, ingest_app"

sudo systemctl restart traning-bot
sudo systemctl restart traning-ingest

sleep 3
systemctl is-active --quiet traning-bot    || { echo "traning-bot startade inte"; exit 1; }
systemctl is-active --quiet traning-ingest || { echo "traning-ingest startade inte"; exit 1; }

echo "Deploy OK: $(git rev-parse --short HEAD)"
