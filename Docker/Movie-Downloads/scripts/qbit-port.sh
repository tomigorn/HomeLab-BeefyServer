#!/bin/sh
# Tell qBittorrent which port gluetun just had forwarded, then reannounce every torrent so the trackers learn it.
#
# Runs INSIDE the gluetun container, via VPN_PORT_FORWARDING_UP_COMMAND, every
# time the forwarded port changes. qBittorrent's WebUI is on 127.0.0.1 here
# because it shares this network namespace.
#
# This exists as a file rather than an inline compose command for two reasons:
# the inline form needs three levels of quote escaping inside YAML to send JSON,
# and a real script can be read, tested and version-controlled. It replaces the
# bespoke port-updater container the older `mouse` stack runs on fastpi - and
# with it, that container's docker.sock access.
#
# PREREQUISITE, or this silently never works:
#   qBittorrent -> Options -> WebUI -> "Bypass authentication for clients on
#   localhost" must be ENABLED. Without it the API answers 403 and the forwarded
#   port is never applied, so torrents stay unconnectable.
#
# Verify by hand with:
#   docker exec movie-gluetun /scripts/qbit-port.sh 12345
set -eu

PORT="${1:?usage: qbit-port.sh <port>}"

case "$PORT" in
    ''|*[!0-9]*)
        echo "qbit-port.sh: refusing non-numeric port '$PORT'" >&2
        exit 2
        ;;
esac

QBIT="${QBIT_URL:-http://127.0.0.1:8080}"
API="$QBIT/api/v2/app/setPreferences"

# On a stack start gluetun forwards the port before qBittorrent is up; wait for it (max 2 min).
tries=0
until wget -qO- --post-data "json={\"listen_port\":${PORT}}" "$API" >/dev/null 2>&1; do
    tries=$((tries + 1))
    [ "$tries" -ge 24 ] && break
    sleep 5
done

if [ "$tries" -lt 24 ]; then
    echo "qbit-port.sh: qBittorrent listen_port set to ${PORT}"
    wget -qO- --post-data "hashes=all" "$QBIT/api/v2/torrents/reannounce" >/dev/null 2>&1 \
        && echo "qbit-port.sh: reannounced all torrents" \
        || echo "qbit-port.sh: reannounce failed (non-fatal)" >&2
else
    # Non-fatal on purpose: gluetun should keep the tunnel up even if
    # qBittorrent is still starting. gluetun re-runs this on the next change.
    echo "qbit-port.sh: FAILED to set listen_port=${PORT} (is qBittorrent up," \
         "and is 'bypass authentication for localhost' enabled?)" >&2
    exit 1
fi
