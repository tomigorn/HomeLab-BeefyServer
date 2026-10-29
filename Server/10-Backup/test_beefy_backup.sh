#!/usr/bin/env bash
# Runs beefy-backup.sh against fake restic, docker and Telegram with real SQLite files. Usage: ./test_beefy_backup.sh
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1 ${2:-}"; }

mkdir -p "$T/bin"
cat > "$T/bin/restic" <<'STUB'
#!/usr/bin/env bash
echo "restic $*" >> "$FAKE_LOG"
echo "env repo=$RESTIC_REPOSITORY user=$RESTIC_REST_USERNAME cacert=$RESTIC_CACERT pwset=${RESTIC_REST_PASSWORD:+yes}" >> "$FAKE_LOG"
[[ " $* " == *" backup "* ]] && find "$FAKE_STAGE" -type f | sort > "$FAKE_STAGED"
[[ " $* " == *" backup "* ]] && echo '{"message_type":"summary","total_bytes_processed":123,"data_added":45,"snapshot_id":"abcdef12"}'
exit "${FAKE_RESTIC_RC:-0}"
STUB
cat > "$T/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
[[ $* == *pg_dump* ]] || exit 0
[[ -n ${FAKE_PG_FAIL:-} ]] && exit 1
printf 'PGDMP fake'
STUB
cat > "$T/bin/oob" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$FAKE_MSG"
STUB
chmod +x "$T/bin/"*

S="$HERE/beefy-backup.sh"
DBS="seerr/db/db.sqlite3 jellyfin/data/data/jellyfin.db jellyfin/data/data/introskipper/introskipper-v2.db radarr/radarr.db sonarr/sonarr.db prowlarr/prowlarr.db bazarr/db/bazarr.db sabnzbd/admin/history1.db floppy/db/db.sqlite3"
setup() {
    rm -rf "$T/app" "$T/conf" "$T/stage" "$T/log" "$T/msg" "$T/staged"
    mkdir -p "$T/conf"
    printf 'restpw-not-on-cmdline' > "$T/conf/fastpi-rest.pass"; : > "$T/conf/beefy.pass"; : > "$T/conf/fastpi-rest.crt"
    for d in $DBS; do mkdir -p "$T/app/$(dirname "$d")"; sqlite3 "$T/app/$d" "create table t(x); insert into t values (1);"; done
    touch "$T/up"
}
run() {
    env PATH="$T/bin:$PATH" FAKE_LOG="$T/log" FAKE_MSG="$T/msg" FAKE_STAGE="$T/stage" FAKE_STAGED="$T/staged" \
        CONF="$T/conf" STAGE="$T/stage" APP="$T/app" FASTPI_TCP="$T/up" LOCK="$T/lock" OOB="$T/bin/oob" "$@" "$S" > "$T/out" 2>&1
}

setup; run; rc=$?
[[ $rc == 0 && ! -s $T/msg ]] && ok "healthy backup passes quietly" || bad "healthy backup passes quietly" "rc=$rc $(cat "$T/out")"
[[ $(grep -c '\.db\|sqlite3' "$T/staged") == 9 ]] && ok "nine SQLite dumps staged before restic runs" || bad "nine SQLite dumps staged before restic runs" "$(cat "$T/staged")"
grep -q "dumps/dt5.pgdump" "$T/staged" && ok "DT5 postgres dump staged" || bad "DT5 postgres dump staged"
grep -q -- "-Fc -Z0" "$T/log" && ok "DT5 dump uncompressed so restic dedups it" || bad "DT5 dump uncompressed so restic dedups it"
grep -q -- "--exclude $T/app/jellyfin/data/data/jellyfin.db-wal" "$T/log" && grep -q -- "--exclude $T/app/seerr/db/db.sqlite3 " "$T/log" && ok "live DB files and their WAL excluded" || bad "live DB files and their WAL excluded" "$(grep backup "$T/log")"
grep -q "repo=rest:https://192.168.1.2:8009/beefy/ user=beefy cacert=$T/conf/fastpi-rest.crt pwset=yes" "$T/log" && ok "REST repo over pinned TLS with login" || bad "REST repo over pinned TLS with login" "$(cat "$T/log")"
! grep -q "restpw-not-on-cmdline" "$T/log" && ok "REST password never on the command line" || bad "REST password never on the command line"
grep -q -- "--host beefy --tag nightly" "$T/log" && ok "snapshot tagged nightly for host beefy" || bad "snapshot tagged nightly for host beefy"
! grep -qE "restic .*(forget|prune)" "$T/log" && ok "never forgets or prunes (append-only, fastpi does it)" || bad "never forgets or prunes"
[[ ! -e $T/stage ]] && ok "staging removed" || bad "staging removed"

setup; rm "$T/up"; run; rc=$?
[[ $rc != 0 ]] && grep -q "fastpi" "$T/msg" && ! grep -qs "restic" "$T/log" && ok "fastpi unreachable: alert, no restic" || bad "fastpi unreachable: alert, no restic" "rc=$rc $(cat "$T/msg")"

setup; run FAKE_RESTIC_RC=1; rc=$?
[[ $rc != 0 ]] && grep -q "beefy backup" "$T/msg" && ok "restic failure alerts" || bad "restic failure alerts" "rc=$rc"

setup; run FAKE_PG_FAIL=1; rc=$?
[[ $rc != 0 ]] && grep -q "DT5" "$T/msg" && grep -q "restic .*backup" "$T/log" && ok "failed DT5 dump alerts, rest still backed up" || bad "failed DT5 dump alerts, rest still backed up" "rc=$rc $(cat "$T/msg")"

setup; rm "$T/app/sonarr/sonarr.db"; run; rc=$?
[[ $rc != 0 ]] && grep -q "sonarr.db" "$T/msg" && ok "missing DB alerts" || bad "missing DB alerts" "$(cat "$T/msg")"

setup; run FAKE_RESTIC_RC=3; rc=$?
[[ $rc != 0 ]] && grep -q "unreadable" "$T/msg" && ok "rc 3 (unreadable files) reported" || bad "rc 3 (unreadable files) reported" "$(cat "$T/msg")"

L=$(env CONF="$T/conf" "$S" --list)
for want in "sqlite /srv/appdata/jellyfin/data/data/jellyfin.db" "sqlite /srv/appdata/seerr/db/db.sqlite3" "sqlite /srv/appdata/floppy/db/db.sqlite3" \
    "path /srv/appdata" "path /home/buntu/Projects" "path /etc" "path /var/lib/beefy-mover" \
    "exclude /srv/appdata/dt5-db" "exclude /srv/appdata/jellyfin/data/data/trickplay" "exclude /srv/appdata/jellyfin/data/metadata/People" "exclude /srv/appdata/jellyfin/cache"; do
    grep -qxF "$want" <<<"$L" && ok "plan: $want" || bad "plan: $want"
done
grep -q "^exclude /home/buntu/backups" <<<"$L" || ! grep -q "^path /home/buntu$" <<<"$L" && ok "fastpi's own repo on beefy not backed up back" || bad "fastpi's own repo on beefy not backed up back"

echo "passed $PASS, failed $FAIL"
[[ $FAIL == 0 ]]
