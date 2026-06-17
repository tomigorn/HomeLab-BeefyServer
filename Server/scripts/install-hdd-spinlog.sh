#!/usr/bin/env bash
#
# Install the non-waking cold-HDD power-state logger (1-minute systemd timer).
# Run as root:  sudo bash install-hdd-spinlog.sh
#
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Must run as root (sudo)."; exit 1; }
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

apt-get install -y -qq hdparm

install -m 0755 "$SRC_DIR/hdd-spinstate.sh" /usr/local/sbin/hdd-spinstate

# Make both logs world-readable so you can review them without sudo.
touch /var/log/hdd-spinstate.log && chmod 0644 /var/log/hdd-spinstate.log
chmod 0644 /var/log/hd-idle.log 2>/dev/null || true   # hd-idle's own spindown/spinup events

cat > /etc/systemd/system/hdd-spinstate.service <<'EOF'
[Unit]
Description=Log cold HDD power state (non-waking, via hdparm -C)
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/hdd-spinstate
EOF

cat > /etc/systemd/system/hdd-spinstate.timer <<'EOF'
[Unit]
Description=Poll cold HDD power state every minute
[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=5s
Persistent=true
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now hdd-spinstate.timer
systemctl start hdd-spinstate.service   # take one sample immediately

echo "Installed. Timer:"
systemctl status hdd-spinstate.timer --no-pager | grep -E "Active|Trigger" || true
echo
echo "Review any time with:   tail -f /var/log/hdd-spinstate.log"
echo "hd-idle transitions:    less /var/log/hd-idle.log"
echo
echo "Latest sample:"
tail -n 2 /var/log/hdd-spinstate.log || true
