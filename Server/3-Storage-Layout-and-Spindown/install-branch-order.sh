#!/usr/bin/env bash
# Install/refresh the branch-order timer (emptier hot SSD first in /srv/video and /srv/dlpool). Run with sudo on beefy.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
install -m 0755 "$D/beefy_branch_order.py"      /usr/local/sbin/beefy-branch-order
install -m 0644 "$D/beefy-branch-order.service" /etc/systemd/system/beefy-branch-order.service
install -m 0644 "$D/beefy-branch-order.timer"   /etc/systemd/system/beefy-branch-order.timer
systemctl daemon-reload
systemctl enable --now beefy-branch-order.timer
systemctl start beefy-branch-order.service
beefy-branch-order --check
echo "  log:   journalctl -u beefy-branch-order   (a line only when it swaps)"
echo "  now:   sudo systemctl start beefy-branch-order.service"
