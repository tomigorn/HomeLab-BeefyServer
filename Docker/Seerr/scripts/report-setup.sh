#!/usr/bin/env bash
# Problem reports (README "Problem reports"): the shared secret fastpi's Traefik adds to every request (X-Report-Secret), so download-now answers only requests that passed Authentik.
# Idempotent: creates secrets/report/secret once (group 65127, 0640), never prints it. --print-for-traefik prints the Traefik middleware file for fastpi (contains the secret: pipe it, never paste it anywhere).
#   ~/Projects/Docker/Seerr/scripts/report-setup.sh [--print-for-traefik | -h]
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
D="$HERE/../secrets/report"
GID=65127

case "${1:-}" in
  -h|--help) sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  ""|--print-for-traefik) ;;
  *) echo "unknown option $1 (try -h)" >&2; exit 2 ;;
esac

sudo install -d -o buntu -g "$GID" -m 2750 "$D"
if ! sudo test -s "$D/secret"; then
  openssl rand -hex 32 | sudo install -o buntu -g "$GID" -m 0640 /dev/stdin "$D/secret"
  echo "secret created: restart download-now and update fastpi's Traefik file (--print-for-traefik)" >&2
fi
if [ "${1:-}" = "--print-for-traefik" ]; then
  printf '# GITIGNORED - the shared secret Traefik adds to report.holy-grail.ch requests (beefy Seerr scripts/report-setup.sh). Template: dynamic-example/report-secret.yml\nhttp:\n  middlewares:\n    report-secret:\n      headers:\n        customRequestHeaders:\n          X-Report-Secret: "%s"\n' "$(sudo cat "$D/secret")"
else
  sudo ls -l "$D" | awk 'NR>1{print "secret", $1, $3":"$4, $NF}'
  echo "next: cd ~/Projects/Docker/Seerr && sudo docker compose up -d download-now" >&2
fi
