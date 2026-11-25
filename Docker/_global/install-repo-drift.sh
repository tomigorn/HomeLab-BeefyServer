#!/usr/bin/env bash
# Install the nightly repo-vs-live drift check on beefy. Idempotent.
set -euo pipefail
HERE=/home/buntu/Projects/Docker/_global
install -d -m 0755 /home/buntu/.local/state/repo-drift
sudo install -m 0644 "$HERE"/systemd/repo-drift.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now repo-drift.timer
systemctl list-timers repo-drift.timer --no-pager
echo "Run now: sudo systemctl start repo-drift.service"
