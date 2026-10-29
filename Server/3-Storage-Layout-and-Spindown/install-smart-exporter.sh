#!/usr/bin/env bash
# Install/refresh the SMART exporter on beefy (run as buntu, uses sudo). Root runs a root-owned copy, never the buntu-writable repo file; host.root-copies (post-update-check.sh all) reports drift.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
sudo install -d -m 0755 -o root -g root /usr/local/lib/smart-exporter
sudo install -m 0755 -o root -g root "$D/smart_exporter.py" /usr/local/lib/smart-exporter/smart_exporter.py
sudo install -m 0644 -o root -g root "$D/smart-exporter.service" "$D/smart-exporter.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smart-exporter.timer
sudo systemctl start smart-exporter.service
systemctl show -p Result --value smart-exporter.service
