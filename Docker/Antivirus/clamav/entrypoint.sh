#!/bin/sh
set -e

DB_DIR=/var/lib/clamav

if [ ! -f "${DB_DIR}/main.cvd" ] && [ ! -f "${DB_DIR}/main.cld" ]; then
    echo "[entrypoint] No virus definitions found, downloading now (a few minutes)..."
    freshclam --config-file=/etc/clamav/freshclam.conf --no-dns || true
fi

echo "[entrypoint] clamd on :3310 (download hooks); scans run via: docker exec clamav /scan.sh new|all|<path>"
clamd --config-file=/etc/clamav/clamd.conf &
exec freshclam --daemon --foreground --config-file=/etc/clamav/freshclam.conf
