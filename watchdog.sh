#!/usr/bin/env bash
set -euo pipefail
# Daily cron entry: 0 8 * * * /bin/bash /home/fredrik/traning/watchdog.sh >> /home/fredrik/traning/logs/cron.log 2>&1
cd /home/fredrik/traning
set -a; source .env; set +a
.venv/bin/python watchdog.py "$@"
