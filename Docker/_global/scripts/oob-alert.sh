#!/usr/bin/env bash
# Out-of-band alert: Telegram directly, independent of the edge.
# DNS is resolved over DoH so this still works when AdGuard (the LAN DNS) is down,
# and the request never touches Traefik/the tunnel (outbound to api.telegram.org).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$here/../secrets/oob-alert.env"

msg="${1:?usage: oob-alert.sh <message>}"
token="$(cat "$TOKEN_FILE")"

curl -sS --fail --max-time 20 \
  --doh-url https://1.1.1.1/dns-query \
  "https://api.telegram.org/bot${token}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=${msg}" \
  --data "parse_mode=HTML" \
  --output /dev/null \
  && echo "oob-alert: sent" \
  || { echo "oob-alert: FAILED to send" >&2; exit 1; }
