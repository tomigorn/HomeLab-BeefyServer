#!/usr/bin/env bash
# Install the ClamAV scan units (nightly new-files, weekly whole-host) on beefy. Idempotent.
set -euo pipefail
HERE=/home/buntu/Projects/Docker/Antivirus
sudo install -d -m 0755 /var/lib/clamav-scan
sudo install -d -o 1000 -g 1000 -m 0755 /var/lib/clamav-scan/textfile
sudo install -d -o 1000 -g 1000 -m 0750 /srv/.disks/ssd-hot/quarantine
# Background scan queue: SABnzbd/qBittorrent (group media) add jobs, clamav-queue (65112) removes them.
sudo install -d -o 65112 -g 1100 -m 2770 /srv/.disks/ssd-hot/.clamav-queue
sudo install -m 0644 "$HERE"/systemd/clamav-{scan,hostscan}.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clamav-scan.timer clamav-hostscan.timer
systemctl list-timers "clamav-*.timer" --no-pager
echo "Run once now: sudo systemctl start clamav-scan.service; journalctl -u clamav-scan -n 20"
