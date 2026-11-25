#!/usr/bin/env bash
# Runs beefy-restore-test.sh against fake restic, docker and Telegram. Usage: ./test_beefy_restore_test.sh
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1 ${2:-}"; }

mkdir -p "$T/bin"
cat > "$T/bin/restic" <<'STUB'
#!/usr/bin/env bash
echo "restic $*" >> "$FAKE_LOG"
[[ -n ${FAKE_RESTORE_FAIL:-} ]] && { echo "Fatal: repository unreachable" >&2; exit 1; }
t=$(sed -E 's/.*--target ([^ ]+).*/\1/' <<<"$*")
cp -a "$FAKE_SNAP/." "$t/"
STUB
cat > "$T/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  inspect) echo "postgres:18.7-alpine" ;;
  run) echo cid ;;
  stop) ;;
  exec)
    case "$*" in
      *pg_isready*) ;;
      *pg_restore*) exit "${FAKE_PGR_RC:-0}" ;;
      *psql*) echo "${FAKE_PSQL:-5}" ;;
    esac ;;
esac
STUB
cat > "$T/bin/oob" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$FAKE_MSG"
STUB
chmod +x "$T/bin/"*

S="$HERE/beefy-restore-test.sh"
SPEC='app|app/app.db|t:1,u:2
hist|app/hist.db|h:0'
setup() {
    rm -rf "$T/snap" "$T/state" "$T/msg" "$T/log" "$T/lock"
    local B="$T/snap/var/lib/beefy-backup-staging"
    mkdir -p "$B/sqlite/srv/appdata/app" "$B/dumps" "$T/state" "$T/conf"
    sqlite3 "$B/sqlite/srv/appdata/app/app.db" "create table t(x); insert into t values (1); create table u(x); insert into u values (1),(2);"
    sqlite3 "$B/sqlite/srv/appdata/app/hist.db" "create table h(x);"
    echo PGDMP > "$B/dumps/dt5.pgdump"
    echo pw > "$T/conf/fastpi-rest.pass"; touch "$T/up"
}
run() {
    env PATH="$T/bin:$PATH" CONF="$T/conf" STATE="$T/state" FAKE_LOG="$T/log" FAKE_MSG="$T/msg" FAKE_SNAP="$T/snap" OOB="$T/bin/oob" \
        FASTPI_TCP="$T/up" LOCK="$T/lock" DB_SPEC="$SPEC" RESTIC_CACHE_DIR="$T/cache" "$@" "$S" > "$T/out" 2>&1
}
P="$T/state/restore-test.prom"
dbok() { awk -v k="db_restore_test_ok{host=\"beefy\",db=\"$1\",engine=\"$2\"}" '$1==k{print $2}' "$P"; }
dbts() { awk -v k="db_restore_test_last_success_timestamp_seconds{host=\"beefy\",db=\"$1\",engine=\"$2\"}" '$1==k{print $2}' "$P"; }

setup; run; rc=$?
[[ $rc == 0 && $(dbok app sqlite) == 1 && $(dbok hist sqlite) == 1 && $(dbok dt5 postgres) == 1 ]] && ok "healthy backup: every DB ok" || bad "healthy backup: every DB ok" "rc=$rc $(cat "$T/out") $(cat "$P" 2>&1)"
[[ $(dbts app sqlite) -gt 0 && $(dbts dt5 postgres) -gt 0 ]] && ok "last success timestamps written" || bad "last success timestamps written"
[[ $(stat -c %a "$P") == 644 ]] && ok "metric file 0644 (buntu reads it for the pull)" || bad "metric file 0644"
grep -q -- "--network none" "$T/log" && grep -q "docker stop" "$T/log" && ok "throwaway postgres: no network, stopped afterwards" || bad "throwaway postgres: no network, stopped afterwards"
grep -q "restic restore latest --host beefy --tag nightly" "$T/log" && ok "restores the latest nightly snapshot of beefy" || bad "restores the latest nightly snapshot of beefy" "$(cat "$T/log")"
[[ -z $(ls -A "$T/state" | grep -v restore-test.prom) ]] && ok "scratch dir removed" || bad "scratch dir removed" "$(ls -A "$T/state")"
[[ ! -e $T/msg ]] && ok "no Telegram on success" || bad "no Telegram on success"

setup; sqlite3 "$T/snap/var/lib/beefy-backup-staging/sqlite/srv/appdata/app/app.db" "delete from u where x=2;"; run; rc=$?
[[ $rc != 0 && $(dbok app sqlite) == 0 && $(dbok hist sqlite) == 1 ]] && grep -q "app: table u has 1 rows, expected at least 2" "$T/msg" && ok "too few rows fails that DB only, names the table, Telegram" || bad "too few rows fails that DB only" "$(cat "$T/msg") $(cat "$P")"

setup; echo garbage > "$T/snap/var/lib/beefy-backup-staging/sqlite/srv/appdata/app/app.db"; run
[[ $(dbok app sqlite) == 0 ]] && ok "corrupt SQLite dump fails that DB" || bad "corrupt SQLite dump fails that DB"

setup; rm "$T/snap/var/lib/beefy-backup-staging/sqlite/srv/appdata/app/hist.db"; run
[[ $(dbok hist sqlite) == 0 ]] && grep -q "hist: dump missing" "$T/msg" && ok "missing dump fails that DB" || bad "missing dump fails that DB" "$(cat "$T/msg")"

setup; run FAKE_PGR_RC=1
[[ $(dbok dt5 postgres) == 0 && $(dbok app sqlite) == 1 ]] && grep -q "dt5: pg_restore failed" "$T/msg" && ok "failed pg_restore fails dt5 only" || bad "failed pg_restore fails dt5 only" "$(cat "$T/msg")"
grep -q "docker stop" "$T/log" && ok "throwaway postgres stopped after a failure" || bad "throwaway postgres stopped after a failure"

setup; run FAKE_PSQL=0
[[ $(dbok dt5 postgres) == 0 ]] && grep -q "dt5: table PROJECT has 0 rows" "$T/msg" && ok "empty DT5 table fails dt5" || bad "empty DT5 table fails dt5" "$(cat "$T/msg")"

setup; echo broken > "$T/snap/var/lib/beefy-backup-staging/dumps/dt5.pgdump"; : > "$T/snap/var/lib/beefy-backup-staging/dumps/dt5.pgdump"; run
[[ $(dbok dt5 postgres) == 0 ]] && ok "empty DT5 dump fails dt5" || bad "empty DT5 dump fails dt5"

setup; run; first=$(dbts app sqlite); sleep 1; sqlite3 "$T/snap/var/lib/beefy-backup-staging/sqlite/srv/appdata/app/app.db" "delete from t;"; run
[[ $(dbok app sqlite) == 0 && $(dbts app sqlite) == "$first" ]] && ok "last success timestamp kept after a failed restore" || bad "last success timestamp kept after a failed restore"

setup; run FAKE_RESTORE_FAIL=1
[[ $(dbok app sqlite) == 0 && $(dbok dt5 postgres) == 0 ]] && grep -q "restic restore" "$T/msg" && ok "failed restic restore fails every DB" || bad "failed restic restore fails every DB" "$(cat "$T/msg")"

setup; rm "$T/conf/fastpi-rest.pass"; run
[[ $(dbok app sqlite) == 0 && $(dbok hist sqlite) == 0 && $(dbok dt5 postgres) == 0 ]] && grep -q "REST password" "$T/msg" && ok "early failure marks every DB failed" || bad "early failure marks every DB failed" "$(cat "$T/msg") $(cat "$P")"

setup; run; first=$(dbts app sqlite); rm "$T/up"; run; rc=$?
[[ $rc != 0 && $(dbok app sqlite) == 1 && $(dbts app sqlite) == "$first" ]] && ok "fastpi down: skipped, metrics untouched" || bad "fastpi down: skipped, metrics untouched" "$(cat "$T/out")"
[[ ! -e $T/msg ]] && ok "fastpi down: no false DB alert" || bad "fastpi down: no false DB alert"

echo "passed $PASS, failed $FAIL"
[[ $FAIL == 0 ]]
