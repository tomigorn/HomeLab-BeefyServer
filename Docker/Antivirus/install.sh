#!/usr/bin/env bash
# Install the nightly ClamAV scan units on beefy. Idempotent.
set -euo pipefail
HERE=/home/buntu/Projects/Docker/Antivirus
sudo install -d -m 0755 /var/lib/clamav-scan
sudo install -d -o 1000 -g 1000 -m 0755 /var/lib/clamav-scan/textfile
sudo install -d -o 1000 -g 1000 -m 0750 /srv/.disks/ssd-hot/quarantine
sudo install -m 0644 "$HERE"/systemd/clamav-scan.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clamav-scan.timer
systemctl list-timers clamav-scan.timer --no-pager
echo "Run once now: sudo systemctl start clamav-scan.service; journalctl -u clamav-scan -n 20"
