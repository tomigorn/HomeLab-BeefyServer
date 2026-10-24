#!/usr/bin/env bash
# Install the nightly image auto-update units on beefy. Idempotent.
set -euo pipefail
HERE=/home/buntu/Projects/Docker/_global
sudo install -m 0644 "$HERE"/systemd/auto-update.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now auto-update.timer
systemctl list-timers auto-update.timer --no-pager
echo "Dry run: AUTO_UPDATE_STATE=/tmp/au AUTO_UPDATE_TEXTFILE=/tmp/au python3 $HERE/scripts/auto_update.py --dry-run"
