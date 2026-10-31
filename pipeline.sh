#!/usr/bin/env bash
set -euo pipefail
cd /home/fredrik/traning
set -a; source .env; set +a
rclone copy "dropbox:Apps/HealthFitExporter" fit --include "*.fit" --quiet
.venv/bin/python parse_hae.py
.venv/bin/python parse_fit.py
.venv/bin/python parse_metrics.py
.venv/bin/python notify.py
