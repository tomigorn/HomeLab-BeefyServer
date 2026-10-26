#!/usr/bin/env bash
# Runs image-cleanup.sh against a fake docker. Usage: scripts/test_image_cleanup.sh
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1 ${2:-}"; }
has()  { grep -q -- "$2" "$3" && ok "$1" || bad "$1" "no [$2]"; }
hasnt() { grep -q -- "$2" "$3" && bad "$1" "unexpected [$2]" || ok "$1"; }

cat > "$T/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "info "*) exit 0 ;;
  "container ls") echo c1; echo c2 ;;
  "inspect -f") echo sha256:used; echo sha256:stopped ;;
  "image ls") cat "$FAKE_DIR/ids" ;;
  "image inspect") cat "$FAKE_DIR/inspect" ;;
  "image rm") [[ -n ${FAKE_RM_FAIL:-} ]] && exit 1; cat "$FAKE_DIR/ids" > /dev/null ;;
  "builder prune") echo "Total:	1.5GB" ;;
  "system df") echo "Images|${FAKE_SIZE:-10GB}"; echo "Build Cache|1GB" ;;
  *) echo "unexpected: $*" >&2; exit 9 ;;
esac
STUB
chmod +x "$T/docker"
mkdir -p "$T/repo"; echo "    image: keep/me:3" > "$T/repo/compose.yaml"
export REPO_DIR="$T/repo" DOCKER="$T/docker" FAKE_LOG="$T/log" FAKE_DIR="$T" IMAGE_CLEANUP_STATE="$T/state" IMAGE_CLEANUP_TEXTFILE="$T/tf" KEEP_DAYS=14
NOW=$(date +%s); old=$(date -u -d @$((NOW - 40*86400)) +%FT%TZ); new=$(date -u -d @$((NOW - 2*86400)) +%FT%TZ)
printf '%s\n' sha256:used sha256:stopped sha256:oldtagged sha256:olddangling sha256:newunused sha256:superseded | sort > "$T/ids"
cat > "$T/inspect" <<EOF2
sha256:used|$old|0001-01-01T00:00:00Z|100|app:1
sha256:stopped|$old|0001-01-01T00:00:00Z|100|jenkins:lts
sha256:oldtagged|$old|0001-01-01T00:00:00Z|500|old:1,old:latest
sha256:olddangling|$old|0001-01-01T00:00:00Z|300|
sha256:newunused|$new|$new|200|fresh:1
sha256:superseded|$old|0001-01-01T00:00:00Z|400|app:0
EOF2
echo "sha256:superseded $((NOW - 3*86400))" > /dev/null

echo "first run (bootstrap: build date counts)"
bash "$HERE/image-cleanup.sh" > "$T/out" 2>&1; rc=$?
[[ $rc == 0 ]] && ok "exit 0" || bad "exit $rc"
has "tagged unused removed with all its tags" '^image rm old:1 old:latest$' "$T/log"
echo "sha256:oldtagged" > /dev/null
hasnt "bootstrap: dangling image starts its window now" '^image rm sha256:olddangling' "$T/log"
hasnt "bootstrap: older tag of a repo in use starts its window now" 'rm app:0' "$T/log"
hasnt "image used by a running container kept" 'rm app:1' "$T/log"
hasnt "image of a stopped container kept" 'jenkins' "$T/log"
hasnt "recent unused image kept" 'fresh:1' "$T/log"
has "build cache pruned with threshold" '^builder prune -f --filter until=336h$' "$T/log"
for w in volume network 'system prune' 'image prune' ' -f image' 'rm -f' 'rmi' 'container rm' 'container prune'; do hasnt "never: $w" "$w" "$T/log"; done
has "metric removed count" '^docker_image_cleanup_removed_images 1$' "$T/tf/image_cleanup.prom"
has "metric rollback window" '^docker_image_cleanup_rollback_window_images 3$' "$T/tf/image_cleanup.prom"
has "metric timestamp" '^docker_image_cleanup_last_run_timestamp_seconds ' "$T/tf/image_cleanup.prom"
has "metric freed" '^docker_image_cleanup_freed_bytes ' "$T/tf/image_cleanup.prom"

echo "later run (clock: not seen before = starts now, nothing removed)"
cat > "$T/inspect" <<EOF2
sha256:used|$old|0001-01-01T00:00:00Z|100|app:1
sha256:superseded2|$old|0001-01-01T00:00:00Z|400|app:2
EOF2
printf '%s\n' sha256:used sha256:superseded2 | sort > "$T/ids"
: > "$T/log"; bash "$HERE/image-cleanup.sh" > "$T/out" 2>&1
hasnt "newly superseded image kept during rollback window" 'image rm' "$T/log"
has "tracked in state" 'sha256:superseded2' "$IMAGE_CLEANUP_STATE/first-seen"
echo "sha256:superseded2 $((NOW - 15*86400))" > "$IMAGE_CLEANUP_STATE/first-seen"
: > "$T/log"; bash "$HERE/image-cleanup.sh" > "$T/out" 2>&1
has "removed once unused for KEEP_DAYS" '^image rm app:2$' "$T/log"

echo "image referenced by a compose file is kept"
printf '%s\n' sha256:used sha256:refd | sort > "$T/ids"
printf 'sha256:used|%s|0001-01-01T00:00:00Z|100|app:1\nsha256:refd|%s|0001-01-01T00:00:00Z|100|keep/me:3\n' "$old" "$old" > "$T/inspect"
echo "sha256:refd $((NOW - 90*86400))" > "$IMAGE_CLEANUP_STATE/first-seen"
: > "$T/log"; bash "$HERE/image-cleanup.sh" > "$T/out" 2>&1
hasnt "referenced image never removed" '^image rm' "$T/log"
printf '%s\n' sha256:used sha256:rb | sort > "$T/ids"
printf 'sha256:used|%s|0001-01-01T00:00:00Z|100|app:1\nsha256:rb|%s|0001-01-01T00:00:00Z|100|db:9,db:rollback-9-pre-1\n' "$old" "$old" > "$T/inspect"
echo "sha256:rb $((NOW - 90*86400))" > "$IMAGE_CLEANUP_STATE/first-seen"
: > "$T/log"; bash "$HERE/image-cleanup.sh" > "$T/out" 2>&1
hasnt "rollback-tagged image never removed" '^image rm' "$T/log"
echo "dangling image removed by id once its window is over"
echo "sha256:olddangling $((NOW - 20*86400))" >> "$IMAGE_CLEANUP_STATE/first-seen"
printf '%s\n' sha256:used sha256:olddangling | sort > "$T/ids"
printf 'sha256:used|%s|0001-01-01T00:00:00Z|100|app:1\nsha256:olddangling|%s|0001-01-01T00:00:00Z|300|\n' "$old" "$old" > "$T/inspect"
: > "$T/log"; bash "$HERE/image-cleanup.sh" > "$T/out" 2>&1
echo "x" > /dev/null
has "dangling removed by id" '^image rm sha256:olddangling$' "$T/log"
printf '%s\n' sha256:used sha256:superseded2 | sort > "$T/ids"
printf 'sha256:used|%s|0001-01-01T00:00:00Z|100|app:1\nsha256:superseded2|%s|0001-01-01T00:00:00Z|400|app:2\n' "$old" "$old" > "$T/inspect"

echo "dry run and failures"
echo "sha256:superseded2 $((NOW - 15*86400))" > "$IMAGE_CLEANUP_STATE/first-seen"
: > "$T/log"; bash "$HERE/image-cleanup.sh" --dry-run > "$T/out" 2>&1
hasnt "dry run removes nothing" 'image rm\|builder prune' "$T/log"
has "dry run reports" 'would remove app:2' "$T/out"
: > "$T/log"; FAKE_RM_FAIL=1 bash "$HERE/image-cleanup.sh" > "$T/out" 2>&1
has "failed removal is kept and reported" 'could not remove app:2' "$T/out"
has "failed removal stays tracked" 'sha256:superseded2' "$IMAGE_CLEANUP_STATE/first-seen"

echo "$PASS passed, $FAIL failed"; [[ $FAIL == 0 ]]
