# traning — träningsdataarkiv

## Produktion — Claude Code KAN nå Pi:n
  ssh fredrik@100.107.27.54 "cd /home/fredrik/traning && <kommando>"
Verifiera ALLTID mot Pi:n. Windows-kopian är dev, inte drift.
Deploy: ssh fredrik@100.107.27.54 "cd /home/fredrik/traning && ./deploy.sh"
Repo: fbjurulf/auto_training (deploy key, ssh-alias github-traning)
Skriv aldrig till .env eller traning.db utan att fråga först.

## Drift
- venv heter .venv (inte venv)
- pipeline.sh var 15:e min via cron (flock /tmp/traning.lock):
  rclone från Dropbox (Apps/HealthFitExporter) → parse_hae → parse_fit
  → parse_metrics → notify. Alla steg kör .venv/bin/python
- weekly.sh söndag 20:00 (flock /tmp/traning-weekly.lock)
- traning.db ~718 MB — får ALDRIG committas
- Loggar: logs/cron.log. Logrotate ej konfigurerad ännu
- Home Assistant, Mosquitto och stock_news delar samma Pi

## Tjänster
- traning-bot: telegram_logg.py, manuell loggning via Telegram
- traning-ingest: uvicorn ingest_app:app på 0.0.0.0:8099, tar emot push
  från Health Auto Export. Token krävs (INGEST_TOKEN), fail-closed
- Service-filerna finns både i repot och i /etc/systemd/system/.
  deploy.sh stoppar om de glidit isär — synka då manuellt.
