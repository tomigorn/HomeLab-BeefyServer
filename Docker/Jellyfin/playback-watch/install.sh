#!/usr/bin/env bash
# Install/refresh the playback watch. Run as buntu on beefy (uses sudo).
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
sudo install -m 0644 "$D/playback-watch.service" /etc/systemd/system/playback-watch.service
sudo systemctl daemon-reload
sudo systemctl enable --now playback-watch.service
sudo systemctl restart playback-watch.service
mkdir -p "$HOME/.local/bin"
ln -sfn "$D/playback-watch" "$HOME/.local/bin/playback-watch"
echo "installed and (re)started. follow: journalctl -u playback-watch -f"
