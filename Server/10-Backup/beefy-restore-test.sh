#!/usr/bin/env bash
# Weekly restore test of every beefy database: latest nightly snapshot from fastpi's repo into a scratch dir, SQLite integrity_check plus key table row counts, DT5 pg_restore into a throwaway postgres (no network). Metrics for fastpi, Telegram on failure. Runs as root from beefy-restore-test.service; never touches live data.
set -uo pipefail

CONF=${CONF:-/root/.config/restic}
STATE=${STATE:-/var/lib/beefy-restore-test}
PROM=${PROM:-$STATE/restore-test.prom}
WORK_BASE=${WORK_BASE:-$STATE}
FASTPI_TCP=${FASTPI_TCP:-/dev/tcp/192.168.1.2/8009}
LOCK=${LOCK:-/run/lock/beefy-restore-test.lock}
OOB=${OOB:-/usr/local/lib/beefy-oob/scripts/oob-alert.sh}
STAGE=/var/lib/beefy-backup-staging
HOSTL=beefy
export RESTIC_REPOSITORY=${RESTIC_REPOSITORY:-rest:https://192.168.1.2:8009/beefy/}
export RESTIC_REST_USERNAME=beefy
export RESTIC_CACERT=$CONF/fastpi-rest.crt
export RESTIC_PASSWORD_FILE=$CONF/beefy.pass
export RESTIC_CACHE_DIR=${RESTIC_CACHE_DIR:-/var/cache/restic}
# name|path below /srv/appdata|table:min_rows,... (a table needs at least min_rows rows)
DB_SPEC=${DB_SPEC:-'seerr|seerr/db/db.sqlite3|user:1,media:1
jellyfin|jellyfin/data/data/jellyfin.db|Users:1,BaseItems:1
jellyfin-intro-skipper|jellyfin/data/data/introskipper/introskipper-v2.db|AnalyzedItems:1
radarr|radarr/radarr.db|Movies:1
sonarr|sonarr/sonarr.db|Series:1,Episodes:1
prowlarr|prowlarr/prowlarr.db|Indexers:1
bazarr|bazarr/db/bazarr.db|table_shows:1
sabnzbd-history|sabnzbd/admin/history1.db|history:0
floppy|floppy/db/db.sqlite3|app_item:1
lingarr|lingarr/local.db|Settings:1'}
PG_TABLES=${PG_TABLES:-PROJECT:1 COMPONENT:1 VULNERABILITY:1}

log() { echo "beefy-restore-test: $*"; }
esc() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$*"; }
FAILS=() PG="" WORK=""
declare -A DBRES=() DBENG=() OLDOK=() OLDTS=()
fail() { FAILS+=("$(esc "$*")"); log "FAIL $*"; }

load_old() {
    local l n e kind v
    while IFS= read -r l; do
        l=$(sed -nE 's/^db_restore_test_(ok|last_success_timestamp_seconds)\{host="'$HOSTL'",db="([^"]+)",engine="([^"]+)"\} ([0-9]+)$/\2|\3|\1|\4/p' <<<"$l")
        [ -n "$l" ] || continue
        IFS='|' read -r n e kind v <<<"$l"
        DBENG[$n]=$e
        if [ "$kind" = ok ]; then OLDOK[$n]=$v; else OLDTS[$n]=$v; fi
    done < <(grep -s '^db_restore_test_' "$PROM")
}
write_metric() {
    local n now ts ok; now=$(date +%s)
    {
        echo "db_restore_test_run_timestamp_seconds{host=\"$HOSTL\"} $now"
        for n in "${!DBENG[@]}"; do
            ok=${DBRES[$n]:-0}
            echo "db_restore_test_ok{host=\"$HOSTL\",db=\"$n\",engine=\"${DBENG[$n]}\"} $ok"
            ts=${OLDTS[$n]:-0}; [ "${DBRES[$n]:-}" = 1 ] && ts=$now
            echo "db_restore_test_last_success_timestamp_seconds{host=\"$HOSTL\",db=\"$n\",engine=\"${DBENG[$n]}\"} $ts"
        done | sort
    } > "$PROM.tmp"
    chmod 644 "$PROM.tmp"; mv "$PROM.tmp" "$PROM"
}
cleanup() {
    [ -n "$PG" ] && docker stop -t 5 "$PG" >/dev/null 2>&1
    [ -n "$WORK" ] && rm -rf -- "$WORK"
    [ "${SKIPPED:-0}" = 1 ] || write_metric
    if [ ${#FAILS[@]} -gt 0 ]; then
        local msg="❌ <b>beefy database restore test failed</b>" f
        for f in "${FAILS[@]}"; do msg+="
• $f"; done
        msg+="
journalctl -u beefy-restore-test on beefy; Server/10-Backup.md"
        "$OOB" "$msg" >/dev/null || log "Telegram alert not sent"
    fi
}
trap cleanup EXIT
trap 'log "terminated"; exit 1' TERM INT

exec 9>"$LOCK"
flock -n 9 || { log "already running"; SKIPPED=1; exit 0; }
if ! timeout 5 bash -c "< $FASTPI_TCP" 2>/dev/null; then
    log "fastpi backup server unreachable, skipped"; SKIPPED=1; exit 1
fi
umask 077
load_old
# every configured DB is part of this run: one that never got checked (early failure) counts as failed
while IFS='|' read -r name _; do [ -n "$name" ] && DBENG[$name]=sqlite; done <<<"$DB_SPEC"
DBENG[dt5]=postgres
RESTIC_REST_PASSWORD=$(cat "$CONF/fastpi-rest.pass") || { fail "REST password unreadable"; exit 1; }
export RESTIC_REST_PASSWORD
WORK=$(mktemp -d "$WORK_BASE/work.XXXXXX")

RESTORED=1
if ! restic restore latest --host beefy --tag nightly --target "$WORK" --include "$STAGE" >/dev/null 2>"$WORK/err"; then
    fail "restic restore of the latest snapshot failed: $(tail -2 "$WORK/err" | tr '\n' ' ')"
    RESTORED=0
fi

check_sqlite_db() {
    local name=$1 path=$2 tables=$3 f r t min n before=${#FAILS[@]}
    DBENG[$name]=sqlite
    f="$WORK$STAGE/sqlite/srv/appdata/$path"
    if [ ! -f "$f" ]; then fail "$name: dump missing from the snapshot"; DBRES[$name]=0; return; fi
    r=$(sqlite3 -readonly "$f" "pragma integrity_check" 2>&1 | head -1)
    case "$r" in *"unknown function"*) r=$(sqlite3 -readonly "$f" "pragma quick_check" 2>&1 | head -1) ;; esac
    [ "$r" = ok ] || fail "$name: integrity_check: $r"
    for t in ${tables//,/ }; do
        min=${t#*:}; t=${t%%:*}
        n=$(sqlite3 -readonly "$f" "select count(*) from \"$t\"" 2>&1)
        { [ "$n" -ge "$min" ] 2>/dev/null; } || fail "$name: table $t has ${n:-?} rows, expected at least $min"
    done
    [ ${#FAILS[@]} -eq "$before" ] && DBRES[$name]=1 || DBRES[$name]=0
}
while IFS='|' read -r name path tables; do
    [ -n "$name" ] || continue
    if [ "$RESTORED" = 1 ]; then check_sqlite_db "$name" "$path" "$tables"; else DBENG[$name]=sqlite; DBRES[$name]=0; fi
done <<<"$DB_SPEC"

check_dt5() {
    local dump="$WORK$STAGE/dumps/dt5.pgdump" before=${#FAILS[@]} img t min n
    DBENG[dt5]=postgres
    if [ ! -s "$dump" ]; then fail "dt5: dump missing from the snapshot"; DBRES[dt5]=0; return; fi
    img=$(docker inspect -f '{{.Config.Image}}' dt5-db 2>/dev/null) || img=postgres:18-alpine
    PG=beefy-restore-test-pg-$$
    if docker run -d --rm --name "$PG" --network none --memory 4g --mount "type=bind,src=$dump,dst=/dump/dt5.pgdump,readonly" \
        -e POSTGRES_USER=rt -e POSTGRES_DB=rt -e POSTGRES_PASSWORD="$(head -c 18 /dev/urandom | base64)" "$img" \
        -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_wal_size=4GB >/dev/null; then
        for _ in $(seq 90); do docker exec "$PG" pg_isready -h 127.0.0.1 -U rt -q && break; sleep 1; done
        if docker exec "$PG" sh -c 'createdb -U rt dt5 && pg_restore -U rt -d dt5 --no-owner --no-privileges --exit-on-error -j 4 /dump/dt5.pgdump' >/dev/null 2>"$WORK/pgerr"; then
            for t in $PG_TABLES; do
                min=${t#*:}; t=${t%%:*}
                n=$(docker exec "$PG" psql -U rt -d dt5 -At -c "select count(*) from \"$t\"" 2>&1)
                { [ "$n" -ge "$min" ] 2>/dev/null; } || fail "dt5: table $t has ${n:-?} rows, expected at least $min"
            done
        else
            fail "dt5: pg_restore failed: $(tail -2 "$WORK/pgerr" | tr '\n' ' ')"
        fi
    else
        fail "dt5: throwaway postgres did not start"
    fi
    docker stop -t 5 "$PG" >/dev/null 2>&1; PG=""
    [ ${#FAILS[@]} -eq "$before" ] && DBRES[dt5]=1 || DBRES[dt5]=0
}
if [ "$RESTORED" = 1 ]; then check_dt5; else DBENG[dt5]=postgres; DBRES[dt5]=0; fi

log "finished fails=${#FAILS[@]}"
[ ${#FAILS[@]} -eq 0 ]
