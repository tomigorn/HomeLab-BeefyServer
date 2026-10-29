#!/usr/bin/env bash
# Install the beefy backup on beefy (run as buntu, uses sudo). Idempotent. Needs /root/.config/restic/{beefy.pass,fastpi-rest.pass,fastpi-rest.crt} from fastpi's Server/5-Backup/setup-beefy-repo.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
V=0.19.1
SUM=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c
if [ "$(restic version 2>/dev/null | awk '{print $2}')" != "$V" ]; then
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/r.bz2" "https://github.com/restic/restic/releases/download/v$V/restic_${V}_linux_amd64.bz2"
    echo "$SUM  $tmp/r.bz2" | sha256sum -c -
    python3 -c 'import bz2, sys; open(sys.argv[2], "wb").write(bz2.open(sys.argv[1]).read())' "$tmp/r.bz2" "$tmp/r"
    sudo install -m 0755 "$tmp/r" /usr/local/bin/restic
    rm -rf "$tmp"
fi
for f in beefy.pass fastpi-rest.pass fastpi-rest.crt; do sudo test -s "/root/.config/restic/$f" || { echo "missing /root/.config/restic/$f (run setup-beefy-repo.sh on fastpi)"; exit 1; }; done
sudo install -m 0750 -o root -g root "$HERE/beefy-backup.sh" /usr/local/sbin/beefy-backup
# root-owned copy of the OOB alert and its config (root never runs or sources a buntu-writable file); the token itself stays where it is
G="$HERE/../../Docker/_global"
sudo install -d -m 0755 -o root -g root /usr/local/lib/beefy-oob /usr/local/lib/beefy-oob/scripts
sudo install -d -m 0700 -o root -g root /usr/local/lib/beefy-oob/secrets
sudo install -m 0755 -o root -g root "$G/scripts/oob-alert.sh" /usr/local/lib/beefy-oob/scripts/oob-alert.sh
sudo install -m 0600 -o root -g root "$G/secrets/oob-alert.env" /usr/local/lib/beefy-oob/secrets/oob-alert.env
sudo install -m 0644 "$HERE/beefy-backup.service" "$HERE/beefy-backup.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now beefy-backup.timer
systemctl list-timers beefy-backup.timer --no-pager
