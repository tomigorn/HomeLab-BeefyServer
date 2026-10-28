#!/usr/bin/env bash
# Install the ClamAV scan units (nightly new-files, weekly whole-host) on beefy. Idempotent.
set -euo pipefail
HERE=/home/buntu/Projects/Docker/Antivirus
sudo install -d -m 0755 /var/lib/clamav-scan
# setgid textfile-writers (65118): the hook (SABnzbd, qBittorrent) and clamav-queue write clamav-hook.prom and clamav-hits.tsv here.
sudo install -d -o 1000 -g 65118 -m 2775 /var/lib/clamav-scan/textfile
# One quarantine and one queue per hot SSD branch: a file is moved (rename) and linked on its own filesystem.
for branch in ssd-hot ssd-hot2; do
  sudo install -d -o 1000 -g 1100 -m 2770 "/srv/.disks/$branch/quarantine"
  # Background scan queue: SABnzbd/qBittorrent (group media) add jobs, clamav-queue (65112) removes them.
  sudo install -d -o 65112 -g 1100 -m 2770 "/srv/.disks/$branch/.clamav-queue"
done
# fsck makes lost+found root-only; scan.sh's find reports "Permission denied" (a scan error) unless the scan user can read it, as on ssd-hot.
sudo chown buntu:media /srv/.disks/ssd-hot2/lost+found
sudo chmod 2775 /srv/.disks/ssd-hot2/lost+found
sudo install -m 0644 "$HERE"/systemd/clamav-{scan,hostscan}.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clamav-scan.timer clamav-hostscan.timer
systemctl list-timers "clamav-*.timer" --no-pager
echo "Run once now: sudo systemctl start clamav-scan.service; journalctl -u clamav-scan -n 20"
