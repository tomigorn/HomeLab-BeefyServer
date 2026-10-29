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
printf 'sha256:used|%s|0001-01-01T00:00:00Z|100|app:1\nsha256:rb|%s|0001-01-01T00:00:00Z|100|db:9,db:9-rollback,db-rollback:9\n' "$old" "$old" > "$T/inspect"
echo "sha256:rb $((NOW - 90*86400))" > "$IMAGE_CLEANUP_STATE/first-seen"
: > "$T/log"; bash "$HERE/image-cleanup.sh" > "$T/out" 2>&1
hasnt "other tags with rollback in them never removed" '^image rm' "$T/log"

echo ":rollback-* tags: kept for KEEP_DAYS, then cleaned"
d3=$(date -d @$((NOW - 3*86400)) +%F); d40=$(date -d @$((NOW - 40*86400)) +%F)
printf '%s\n' sha256:used sha256:rbyoung sha256:rbold sha256:rbref sha256:rbt2 sha256:rbt40 | sort > "$T/ids"
cat > "$T/inspect" <<EOF2
sha256:used|$old|0001-01-01T00:00:00Z|100|app:1
sha256:rbyoung|$old|0001-01-01T00:00:00Z|100|img:rollback-$d3-demo
sha256:rbold|$old|0001-01-01T00:00:00Z|100|img:rollback-$d40-demo
sha256:rbref|$old|0001-01-01T00:00:00Z|100|keep/me:3,keep/me:rollback-$d40-demo
sha256:rbt2|$old|$new|100|cf/x:rollback-2026.10.4
sha256:rbt40|$old|$old|100|cf/y:rollback-2026.9.1
EOF2
printf 'sha256:%s %s\n' rbyoung $((NOW - 90*86400)) rbold $((NOW - 90*86400)) rbref $((NOW - 90*86400)) rbt2 $((NOW - 90*86400)) rbt40 $((NOW - 90*86400)) > "$IMAGE_CLEANUP_STATE/first-seen"
: > "$T/log"; bash "$HERE/image-cleanup.sh" > "$T/out" 2>&1
hasnt "rollback tag younger than KEEP_DAYS kept (date in the tag)" "rollback-$d3" "$T/log"
has "rollback tag older than KEEP_DAYS removed" "^image rm img:rollback-$d40-demo$" "$T/log"
has "expired rollback tag of a referenced image only untagged" "^image rm keep/me:rollback-$d40-demo$" "$T/log"
hasnt "referenced tag of that image kept" 'rm keep/me:3' "$T/log"
hasnt "undated rollback tag kept while its tag time is recent" 'cf/x' "$T/log"
has "undated rollback tag removed once its tag time is old" '^image rm cf/y:rollback-2026.9.1$' "$T/log"
grep -q 'sha256:rbyoung' "$IMAGE_CLEANUP_STATE/first-seen" && [[ $(awk '$1=="sha256:rbyoung"{print $2}' "$IMAGE_CLEANUP_STATE/first-seen") -lt $((NOW - 80*86400)) ]] && ok "unused clock keeps running under a rollback tag" || bad "unused clock keeps running under a rollback tag"
echo "rollback.invalid/<repo>:<date>-<project> tags: kept for KEEP_DAYS, then cleaned; manual rollback tags kept"
printf '%s\n' sha256:used sha256:nsyoung sha256:nsold sha256:nsref sha256:manual | sort > "$T/ids"
cat > "$T/inspect" <<EOF2
sha256:used|$old|0001-01-01T00:00:00Z|100|app:1
sha256:nsyoung|$old|0001-01-01T00:00:00Z|100|rollback.invalid/lscr.io/x/img:$d3-demo
sha256:nsold|$old|0001-01-01T00:00:00Z|100|rollback.invalid/lscr.io/x/img:$d40-demo
sha256:nsref|$old|0001-01-01T00:00:00Z|100|keep/me:3,rollback.invalid/keep/me:$d40-demo
sha256:manual|$old|0001-01-01T00:00:00Z|100|avatar-picker:v1.13.0-rollback,traefik-rollback:v3.7.16-20261027
EOF2
printf 'sha256:%s %s\n' nsyoung $((NOW - 90*86400)) nsold $((NOW - 90*86400)) nsref $((NOW - 90*86400)) manual $((NOW - 90*86400)) > "$IMAGE_CLEANUP_STATE/first-seen"
: > "$T/log"; bash "$HERE/image-cleanup.sh" > "$T/out" 2>&1
hasnt "namespaced rollback tag younger than KEEP_DAYS kept" "img:$d3" "$T/log"
has "namespaced rollback tag older than KEEP_DAYS removed" "^image rm rollback.invalid/lscr.io/x/img:$d40-demo$" "$T/log"
has "expired namespaced tag of a referenced image only untagged" "^image rm rollback.invalid/keep/me:$d40-demo$" "$T/log"
hasnt "referenced tag of that image kept" 'rm keep/me:3' "$T/log"
hasnt "manual tags with rollback in them never removed" 'avatar-picker\|traefik-rollback' "$T/log"
printf '%s\n' sha256:used sha256:kp | sort > "$T/ids"
printf 'sha256:used|%s|0001-01-01T00:00:00Z|100|app:1\nsha256:kp|%s|0001-01-01T00:00:00Z|100|scan/tool:latest\n' "$old" "$old" > "$T/inspect"
echo "sha256:kp $((NOW - 90*86400))" > "$IMAGE_CLEANUP_STATE/first-seen"
: > "$T/log"; IMAGE_CLEANUP_KEEP="scan/tool other/x" bash "$HERE/image-cleanup.sh" > "$T/out" 2>&1
hasnt "IMAGE_CLEANUP_KEEP repository never removed" '^image rm' "$T/log"
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
