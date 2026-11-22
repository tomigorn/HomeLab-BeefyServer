#!/usr/bin/env bash
# Report site "recently watched" sort (README "Problem reports"): the shared secret between Jellyfin's popular backend (GET /watched on :8100) and seerr-download-now.
# Idempotent: creates it once, keeps both copies equal, never prints it. Needs restarts: jellyfin-popular and seerr-download-now (printed).
#   ~/Projects/Docker/Seerr/scripts/watched-setup.sh [-h]
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SEERR="$HERE/../secrets/report/watched"
POP="$HERE/../../Jellyfin/secrets/popular/watched.secret"

case "${1:-}" in
  -h|--help) sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") ;;
  *) echo "unknown option $1 (try -h)" >&2; exit 2 ;;
esac

value=$(sudo cat "$SEERR" 2>/dev/null || sudo cat "$POP" 2>/dev/null || openssl rand -hex 32)
printf '%s\n' "$value" | sudo install -o buntu -g 65127 -m 0640 /dev/stdin "$SEERR"
printf '%s\n' "$value" | sudo install -o buntu -g 65128 -m 0640 /dev/stdin "$POP"
sudo ls -l "$SEERR" "$POP" | awk '{print "secret", $1, $3":"$4, $NF}'
echo "next: cd ~/Projects/Docker/Jellyfin && sudo docker compose up -d popular; cd ~/Projects/Docker/Seerr && sudo docker compose up -d download-now" >&2
