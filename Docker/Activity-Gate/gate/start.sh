#!/bin/sh
# Writes the upstream keys from the secret files into /tmp/keys.conf (tmpfs), then runs nginx. A missing or odd key stops the start.
set -eu
key() {
  v=$(tr -d '\r\n' < "/run/secrets/gate/$1")
  case "$v" in
    '' | *[!A-Za-z0-9]*) echo "activity-gate: $1 missing or not alphanumeric" >&2; exit 1 ;;
  esac
  printf '%s' "$v"
}
jf=$(key jellyfin.apikey)
ra=$(key radarr.apikey)
so=$(key sonarr.apikey)
umask 077
cat > /tmp/keys.conf <<KEYS
map \$host \$jellyfin_auth { default 'MediaBrowser Client="activity-gate", Device="activity-gate", DeviceId="activity-gate", Version="1", Token="$jf"'; }
map \$host \$radarr_key { default "$ra"; }
map \$host \$sonarr_key { default "$so"; }
KEYS
unset jf ra so
exec nginx -c /gate/nginx.conf -g 'daemon off;'
