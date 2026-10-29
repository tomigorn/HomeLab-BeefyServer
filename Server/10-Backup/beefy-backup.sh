#!/usr/bin/env bash
# Nightly encrypted restic backup of beefy to fastpi's append-only REST server (TLS pinned, own login). Runs as root from beefy-backup.service; beefy cannot delete snapshots, retention runs on fastpi. --list prints what is backed up.
set -uo pipefail

CONF=${CONF:-/root/.config/restic}
STAGE=${STAGE:-/var/lib/beefy-backup-staging}
APP=${APP:-/srv/appdata}
FASTPI_TCP=${FASTPI_TCP:-/dev/tcp/192.168.1.2/8009}
LOCK=${LOCK:-/run/lock/beefy-backup.lock}
OOB=${OOB:-/home/buntu/Projects/Docker/_global/scripts/oob-alert.sh}
H=/home/buntu

SQLITE_DBS=(
    seerr/db/db.sqlite3
    jellyfin/data/data/jellyfin.db
    jellyfin/data/data/introskipper/introskipper-v2.db
    radarr/radarr.db
    sonarr/sonarr.db
    prowlarr/prowlarr.db
    bazarr/db/bazarr.db
    sabnzbd/admin/history1.db
    floppy/db/db.sqlite3
)
PATHS=(
    "$STAGE" /srv/appdata "$H/Projects" "$H/.ssh" "$H/.config/systemd"
    "$H/.claude/CLAUDE.md" "$H/.claude/machine.md" "$H/.claude/settings.json" "$H/.claude/projects/-home-buntu/memory"
    /etc /var/lib/beefy-mover /var/spool/cron/crontabs
)
A=/srv/appdata
EXCLUDES=(
    "$A/dt5-db" "$A/dependency-track" "$A/dependency-track-db" "$A/*.tgz"
    "$A/jellyfin/cache" "$A/jellyfin/log" "$A/jellyfin/data/transcodes" "$A/jellyfin/data/data/trickplay"
    "$A/jellyfin/data/metadata/People" "$A/jellyfin/data/plugins/Jellyfin.Plugin.ImdbRatingsNg/imdbratings.db"
    "$A/*/logs" "$A/*/logs.db*" "$A/*/MediaCover" "$A/sabnzbd/Downloads" "$A/qbittorrent/qBittorrent/logs"
    "$A/floppy/backups" "$A/floppy-redis" "$A/flaresolverr" "$A/recyclarr/resources" "$A/seerr/cache"
    "$H/Projects/**/node_modules" "$H/Projects/**/__pycache__" "$H/Projects/**/.venv"
)

if [ "${1:-}" = --list ]; then
    for d in "${SQLITE_DBS[@]}"; do echo "sqlite /srv/appdata/$d"; done
    echo "pg_dump dt5-db"
    for p in "${PATHS[@]}"; do echo "path $p"; done
    for e in "${EXCLUDES[@]}"; do echo "exclude $e"; done
    exit 0
fi

export RESTIC_REPOSITORY=rest:https://192.168.1.2:8009/beefy/
export RESTIC_REST_USERNAME=beefy
export RESTIC_CACERT=$CONF/fastpi-rest.crt
export RESTIC_PASSWORD_FILE=$CONF/beefy.pass
export RESTIC_CACHE_DIR=${RESTIC_CACHE_DIR:-/var/cache/restic}
ERRF=$(mktemp)

log() { echo "beefy-backup: $*"; }
esc() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$*"; }
FAILS=()
fail() { FAILS+=("$(esc "$*")"); log "FAIL $*"; }
finish() {
    rm -rf "$STAGE" "$ERRF"
    if [ ${#FAILS[@]} -gt 0 ]; then
        local msg="❌ <b>beefy backup to fastpi failed</b>"
        for f in "${FAILS[@]}"; do msg+="
• $f"; done
        msg+="
journalctl -u beefy-backup on beefy; restore: Server/10-Backup.md"
        "$OOB" "$msg" >/dev/null || log "Telegram alert not sent"
    fi
}
trap finish EXIT
trap 'fail "terminated (time limit?)"; exit 1' TERM INT

exec 9>"$LOCK"
flock -n 9 || { log "already running"; exit 0; }

if ! timeout 5 bash -c "< $FASTPI_TCP" 2>/dev/null; then
    fail "fastpi backup server 192.168.1.2:8009 unreachable"; exit 1
fi

umask 077
rm -rf "$STAGE"; install -d -m 700 "$STAGE" "$STAGE/sqlite" "$STAGE/dumps"
EXCL=(--exclude-caches)
for e in "${EXCLUDES[@]}"; do EXCL+=(--exclude "$e"); done
for d in "${SQLITE_DBS[@]}"; do
    db=$APP/$d
    [ -f "$db" ] || { fail "missing $db"; continue; }
    install -d -m 700 "$STAGE/sqlite$(dirname "$db")"
    sqlite3 "$db" ".timeout 30000" ".backup '$STAGE/sqlite$db'" || { fail "SQLite backup failed: $db"; continue; }
    EXCL+=(--exclude "$db" --exclude "$db-wal" --exclude "$db-shm")
done

if ! docker exec dt5-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -Z0' > "$STAGE/dumps/dt5.pgdump" 2>>"$ERRF" || [ ! -s "$STAGE/dumps/dt5.pgdump" ]; then
    fail "DT5 pg_dump failed"
fi

EXISTING=(); for p in "${PATHS[@]}"; do [ -e "$p" ] && EXISTING+=("$p"); done
RESTIC_REST_PASSWORD=$(cat "$CONF/fastpi-rest.pass") || { fail "REST password unreadable"; exit 1; }
export RESTIC_REST_PASSWORD
OUT=$(restic backup --json --one-file-system --host beefy --tag nightly "${EXCL[@]}" "${EXISTING[@]}" 2>"$ERRF")
RC=$?
SUM=$(printf '%s\n' "$OUT" | grep '"message_type":"summary"' | tail -1)
[ -n "$SUM" ] && log "snapshot $(jq -r '.snapshot_id[0:8]' <<<"$SUM"): processed=$(jq -r .total_bytes_processed <<<"$SUM") added=$(jq -r .data_added <<<"$SUM")"
# rc 3 = some source files unreadable; the snapshot exists
if [ $RC -eq 3 ]; then fail "snapshot saved, some files unreadable: $(head -3 "$ERRF")"
elif [ $RC -ne 0 ]; then fail "restic backup rc=$RC: $(tail -3 "$ERRF")"; fi

log "finished fails=${#FAILS[@]}"
[ ${#FAILS[@]} -eq 0 ]
