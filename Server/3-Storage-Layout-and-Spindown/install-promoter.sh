#!/usr/bin/env bash
# Install/refresh the beefy promoter. Run with sudo on beefy.
#
# NOTE: the detail-view TRIGGER is not built (see the module docstring). This
# installs the engine; nothing will call it until Jellyfin exists and a webhook is
# pointed at POST /promote. Until then it is useful by hand:
#   curl -XPOST localhost:9002/promote -d '{"path":"media/movies/Some Film"}'
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
install -m 0755 "$D/beefy_promoter.py"      /usr/local/sbin/beefy_promoter.py
install -m 0644 "$D/beefy-promoter.service" /etc/systemd/system/beefy-promoter.service
[ -f /etc/beefy-promoter.conf ] || install -m 0644 "$D/beefy-promoter.conf" /etc/beefy-promoter.conf
systemctl daemon-reload
systemctl enable beefy-promoter.service
systemctl restart beefy-promoter.service
echo "installed. NOTE: DRY_RUN=1 by default in /etc/beefy-promoter.conf"
echo "  follow:  journalctl -u beefy-promoter -f"
echo "  status:  curl -s localhost:9002/status | python3 -m json.tool"
