#!/usr/bin/env bash
# Install/refresh the beefy idle-watcher. Run with sudo on beefy.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
install -m 0755 "$D/beefy_idle_watcher.py"      /usr/local/sbin/beefy_idle_watcher.py
install -m 0644 "$D/beefy-idle-watcher.service" /etc/systemd/system/beefy-idle-watcher.service
[ -f /etc/beefy-idle.conf ] || install -m 0644 "$D/beefy-idle.conf" /etc/beefy-idle.conf
systemctl daemon-reload
systemctl enable --now beefy-idle-watcher.service
echo "installed. follow:  journalctl -u beefy-idle-watcher -f"
