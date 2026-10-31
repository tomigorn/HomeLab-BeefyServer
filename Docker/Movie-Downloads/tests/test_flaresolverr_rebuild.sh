#!/usr/bin/env bash
# Runs scripts/flaresolverr-rebuild.sh against a fake docker: build, swap, verify, rollback.
# Usage: tests/test_flaresolverr_rebuild.sh
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/../scripts/flaresolverr-rebuild.sh"
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }

mkdir -p "$T/bin"
cat > "$T/bin/docker" <<'SH'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "buildx imagetools") echo '"sha256:feed"' ;;
  "image inspect") echo "sha256:prev-${5##*:}" ;;
  "build "*) [ -z "$FAKE_BUILD_FAIL" ] || exit 1 ;;
  "run --rm")
    img=${5}
    case "$4" in
      sh) v="FAKE_FP_${img##*:}"; echo "${!v:-fp-$img}" ;;
      cat) echo "driver-of-$img" ;;
    esac ;;
esac
exit 0
SH
chmod +x "$T/bin/docker"

run() { # run <verify cmd> [args]
  : > "$T/log"; : > "$T/alerts"
  local v=$1; shift
  PATH="$T/bin:$PATH" FAKE_LOG="$T/log" FS_STATE="$T/state" FS_DRIVER_DIR=/srv/appdata/flaresolverr \
    FS_VERIFY="$v" FS_ALERT="$T/alert" bash "$SCRIPT" "$@" > "$T/out" 2>&1
}
printf '#!/bin/sh\necho "$1" >> "%s/alerts"\n' "$T" > "$T/alert"; chmod +x "$T/alert"
calls() { grep -E "^($1)" "$T/log" | tr '\n' ';'; }

echo "flaresolverr-rebuild.sh"
export FAKE_FP_candidate=same FAKE_FP_current=same
run true; rc=$?
assert_eq "unchanged image: exit 0" 0 "$rc"
assert_eq "builds from the newest upstream digest with Debian upgrades" "build --network host --pull --no-cache --build-arg FLARESOLVERR_BASE=ghcr.io/flaresolverr/flaresolverr@sha256:feed -t flaresolverr-local:candidate $(cd "$HERE/.." && pwd)/flaresolverr;" "$(calls "build ")"
assert_eq "unchanged image: nothing swapped or recreated" "" "$(calls 'tag|compose')"
assert_eq "unchanged image: candidate removed" "rmi flaresolverr-local:candidate;" "$(calls rmi)"

export FAKE_FP_candidate=new
run true; rc=$?
assert_eq "changed + verified: exit 0" 0 "$rc"
assert_eq "old image kept as rollback, candidate becomes current" "tag flaresolverr-local:current flaresolverr-local:rollback;tag flaresolverr-local:candidate flaresolverr-local:current;" "$(calls tag)"
assert_eq "recreates only flaresolverr" "compose up -d --no-deps flaresolverr;" "$(calls compose)"
assert_eq "driver copied from the new image as 65119, old one kept" "run --rm --user 0 --network none --entrypoint sh -v /srv/appdata/flaresolverr:/d flaresolverr-local:current -c cp -p /d/chromedriver /d/chromedriver.rollback && install -o 65119 -g 65119 -m 0755 /app/chromedriver /d/chromedriver;" "$(calls 'run --rm --user')"
assert_eq "no alert" "" "$(cat "$T/alerts")"
assert_eq "candidate tag and the previous rollback image removed" "rmi flaresolverr-local:candidate;rmi sha256:prev-rollback;" "$(calls rmi)"

run false; rc=$?
assert_eq "verify fails: exit 1" 1 "$rc"
assert_eq "rolled back to the old image" "tag flaresolverr-local:current flaresolverr-local:rollback;tag flaresolverr-local:candidate flaresolverr-local:current;tag flaresolverr-local:rollback flaresolverr-local:current;" "$(calls tag)"
assert_eq "recreated twice (new, then old)" "compose up -d --no-deps flaresolverr;compose up -d --no-deps flaresolverr;" "$(calls compose)"
assert_eq "driver written twice, the old one back last" "2 /d/chromedriver.rollback /d/chromedriver" "$(grep -c 'run --rm --user 0' "$T/log") $(grep 'run --rm --user 0' "$T/log" | tail -1 | sed 's/.*0755 //')"
assert_eq "alert sent" "1" "$(grep -c 'rolled back' "$T/alerts")"
assert_eq "failed candidate removed" "rmi flaresolverr-local:candidate;" "$(calls rmi)"

FAKE_BUILD_FAIL=1 run true; rc=$?
assert_eq "build fails: exit 1" 1 "$rc"
assert_eq "build fails: nothing swapped" "" "$(calls 'tag|compose')"
assert_eq "build fails: alert" "1" "$(grep -c 'build failed' "$T/alerts")"

export FAKE_FP_candidate=same
run true --force; rc=$?
assert_eq "--force swaps an unchanged image" "0 compose up -d --no-deps flaresolverr;" "$rc $(calls compose)"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
