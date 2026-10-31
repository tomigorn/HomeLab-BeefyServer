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
# Logs in as admin with the WebUI password (QBIT_PASS_FILE, mounted read-only): localhost has no auth bypass (B-02).
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
PASS_FILE="${QBIT_PASS_FILE:-/run/secrets/qbit-webui.password}"
JAR=$(mktemp "${TMPDIR:-/tmp}/qbit-port.XXXXXX")
BODY=$(mktemp "${TMPDIR:-/tmp}/qbit-port.XXXXXX")
trap 'rm -f "$JAR" "$BODY"' EXIT

urlencode() {
    s=$1
    while [ -n "$s" ]; do
        c=${s%"${s#?}"}
        s=${s#?}
        case "$c" in
            [A-Za-z0-9._~-]) printf '%s' "$c" ;;
            *) printf '%%%02X' "'$c" ;;
        esac
    done
}

# 0 = logged in, 1 = qBittorrent not reachable yet, 3 = refused or no password
login() {
    [ -r "$PASS_FILE" ] || { echo "qbit-port.sh: no readable password file $PASS_FILE" >&2; return 3; }
    printf 'username=%s&password=%s' "${QBIT_USER:-admin}" "$(urlencode "$(tr -d '\r\n' < "$PASS_FILE")")" > "$BODY"
    rc=0
    r=$(wget -qO- --save-cookies "$JAR" --keep-session-cookies --post-file "$BODY" "$QBIT/api/v2/auth/login" 2>/dev/null) || rc=$?
    : > "$BODY"
    [ "$rc" -eq 4 ] && return 1
    if [ "$rc" -ne 0 ] || [ "$r" = "Fails." ] || ! grep -q SID "$JAR"; then
        echo "qbit-port.sh: qBittorrent login refused" >&2
        return 3
    fi
}

post() {
    wget -qO- --load-cookies "$JAR" --post-data "$2" "$QBIT/api/v2/$1" >/dev/null 2>&1
}

# On a stack start gluetun forwards the port before qBittorrent is up; wait for it (max 2 min).
tries=0
while :; do
    rc=0
    login || rc=$?
    [ "$rc" -eq 3 ] && exit 1
    [ "$rc" -eq 0 ] && post app/setPreferences "json={\"listen_port\":${PORT}}" && break
    tries=$((tries + 1))
    [ "$tries" -ge 24 ] && break
    sleep 5
done

if [ "$tries" -lt 24 ]; then
    echo "qbit-port.sh: qBittorrent listen_port set to ${PORT}"
    post torrents/reannounce "hashes=all" \
        && echo "qbit-port.sh: reannounced all torrents" \
        || echo "qbit-port.sh: reannounce failed (non-fatal)" >&2
else
    # Non-fatal on purpose: gluetun should keep the tunnel up even if
    # qBittorrent is still starting. gluetun re-runs this on the next change.
    echo "qbit-port.sh: FAILED to set listen_port=${PORT} (is qBittorrent up?)" >&2
    exit 1
fi
