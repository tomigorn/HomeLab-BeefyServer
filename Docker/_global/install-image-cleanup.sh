#!/usr/bin/env bash
# Install the weekly image cleanup units on beefy. Idempotent.
set -euo pipefail
HERE=/home/buntu/Projects/Docker/_global
sudo install -m 0644 "$HERE"/systemd/image-cleanup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now image-cleanup.timer
systemctl list-timers image-cleanup.timer --no-pager
echo "Dry run: $HERE/scripts/image-cleanup.sh --dry-run"
