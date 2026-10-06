#!/usr/bin/env bash
# Install/refresh the beefy tiering mover. Run with sudo on beefy.
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)

install -m 0755 "$D/tier-move"           /usr/local/sbin/tier-move
install -m 0755 "$D/tier-report"         /usr/local/sbin/tier-report
install -m 0755 "$D/beefy-mover"         /usr/local/sbin/beefy-mover
install -m 0644 "$D/beefy-mover.service" /etc/systemd/system/beefy-mover.service
install -m 0644 "$D/beefy-mover.timer"   /etc/systemd/system/beefy-mover.timer
# Never clobber local tuning on reinstall.
[ -f /etc/beefy-mover.conf ]  || install -m 0644 "$D/beefy-mover.conf" /etc/beefy-mover.conf
[ -f /etc/beefy-mover-pins ]  || printf '# One path per line, relative to /srv/video, e.g.\n# media/movies/A Favourite Film\n' > /etc/beefy-mover-pins

systemctl daemon-reload
systemctl enable beefy-mover.timer
systemctl restart beefy-mover.timer
echo "installed. NOTE: DRY_RUN=1 by default in /etc/beefy-mover.conf"
echo "  dry run now:   sudo systemctl start beefy-mover.service"
echo "  read it:       journalctl -u beefy-mover -n 50"
echo "  next fire:     systemctl list-timers beefy-mover.timer"
echo "  arm for real:  set DRY_RUN=0 in /etc/beefy-mover.conf"
