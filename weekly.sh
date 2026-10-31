#!/usr/bin/env bash
set -euo pipefail
cd /home/fredrik/traning
set -a; source .env; set +a
.venv/bin/python weekly.py "$@"
