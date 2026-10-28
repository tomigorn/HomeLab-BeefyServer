#!/usr/bin/env bash
# Install/refresh the beefy tiering mover. Run with sudo on beefy.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)

install -m 0755 "$D/tier-move"           /usr/local/sbin/tier-move
install -m 0755 "$D/tier-report"         /usr/local/sbin/tier-report
install -m 0755 "$D/tier_helper.py"      /usr/local/sbin/tier-helper
install -m 0755 "$D/beefy_mover.py"      /usr/local/sbin/beefy-mover
install -d -m 0755 /var/lib/beefy-mover
install -m 0644 "$D/beefy-mover.service" /etc/systemd/system/beefy-mover.service
install -m 0644 "$D/beefy-mover.timer"   /etc/systemd/system/beefy-mover.timer
# The repo conf is the source of truth (values in the README "Storage"); pins stay local.
install -m 0644 "$D/beefy-mover.conf" /etc/beefy-mover.conf
[ -f /etc/beefy-mover-pins ]  || printf '# One path per line, relative to /srv/video, e.g.\n# media/movies/A Favourite Film\n' > /etc/beefy-mover-pins

systemctl daemon-reload
systemctl enable beefy-mover.timer
systemctl restart beefy-mover.timer
echo "installed."
echo "  plan:          sudo beefy-mover --plan"
echo "  dry run now:   sudo beefy-mover --dry-run --no-deadline"
echo "  read it:       journalctl -u beefy-mover -n 50"
echo "  next fire:     systemctl list-timers beefy-mover.timer"
echo "  pause it:      DRY_RUN=1 or MAX_MOVE_GIB=0 in /etc/beefy-mover.conf"
