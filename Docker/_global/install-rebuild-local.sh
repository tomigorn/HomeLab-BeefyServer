#!/usr/bin/env bash
# Install the weekly local image rebuild units on beefy. Idempotent.
set -euo pipefail
HERE=/home/buntu/Projects/Docker/_global
sudo install -m 0644 "$HERE"/systemd/rebuild-local.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rebuild-local.timer
systemctl list-timers rebuild-local.timer --no-pager
echo "Dry run: $HERE/scripts/rebuild_local.py --dry-run [project]"
