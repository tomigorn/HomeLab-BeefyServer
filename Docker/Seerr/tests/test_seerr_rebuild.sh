#!/usr/bin/env bash
# Runs scripts/seerr-rebuild.sh against a local fake upstream (git), a file:// API and a fake docker.
# Usage: tests/test_seerr_rebuild.sh
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT="$HERE/../scripts/seerr-rebuild.sh"
PASS=0; FAIL=0
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

ok()  { PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$1"; [ -n "$2" ] && printf '       %s\n' "$2"; }
assert_eq() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2] got [$3]"; fi; }
assert_has() { if grep -qF -- "$2" <<<"$3"; then ok "$1"; else bad "$1" "[$2] not in [$3]"; fi; }
assert_not() { if grep -qF -- "$2" <<<"$3"; then bad "$1" "[$2] found"; else ok "$1"; fi; }

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
g() { git -C "$T/up" -c commit.gpgsign=false -c tag.gpgsign=false -c core.hooksPath=/dev/null "$@" >/dev/null 2>&1; }
mkdir -p "$T/up/src" "$T/up/docs"
printf '{"name":"seerr","version":"1.0.0","pnpm":{"overrides":{"a":"1"}}}\n' > "$T/up/package.json"
printf 'FROM node:22-alpine@sha256:abc AS base\n' > "$T/up/Dockerfile"
printf 'one\n' > "$T/up/src/a.txt"
git -C "$T/up" init -q -b main
g add -A; g commit -m base; g tag v1.0.0
g checkout -b develop; printf 'dev\n' > "$T/up/src/dev.txt"; g add -A; g commit -m dev
g checkout -b pr; printf 'oidc\n' > "$T/up/src/oidc.txt"; printf 'doc\n' > "$T/up/docs/oidc.md"; g add -A; g commit -m oidc
PR=$(git -C "$T/up" rev-parse HEAD)
g update-ref refs/pull/2715/head "$PR"
g checkout main

mkdir -p "$T/api/releases" "$T/api/pulls" "$T/repo/image/patches" "$T/repo/image/resolutions"
api() { # api <tag> <state> <merged> [merge sha]
  printf '{"tag_name":"%s"}' "$1" > "$T/api/releases/latest"
  printf '{"state":"%s","merged":%s,"merge_commit_sha":"%s","head":{"sha":"%s"}}' "$2" "$3" "${4:-}" "$PR" > "$T/api/pulls/2715"
}
printf '{"overrides":{"handlebars@<4.7.10":"4.7.10"},"scripts":{"build:next":"next build --webpack"}}\n' > "$T/repo/image/dependency-overrides.json"
echo "$PR" > "$T/repo/image/pr-reviewed"

mkdir -p "$T/bin"
cat > "$T/bin/docker" <<'SH'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$*" in
  "image inspect -f"*seerr-local:current) echo "${FAKE_TREE:-}" ;;
  "image inspect"*) echo "sha256:img-${*##*:}" ;;
  "build "*"--target build"*) [ -z "$FAKE_BUILD_FAIL" ] || exit 1 ;;
  "build "*) [ -z "$FAKE_BUILD_FAIL" ] || exit 1 ;;
  "run --rm --network none seerr-local:test"*) [ -z "$FAKE_TEST_FAIL" ] || exit 1 ;;
  *"tar -C /d -czf"*) [ -z "$FAKE_BACKUP_FAIL" ] || exit 1 ;;
  "compose up"*) [ -z "$FAKE_UP_FAIL" ] || exit 1 ;;
  *"tar -tzf"*) echo ./settings.json; echo ./db/db.sqlite3 ;;
esac
exit 0
SH
chmod +x "$T/bin/docker"
printf '#!/bin/sh\necho "$1" >> "%s/alerts"\n' "$T" > "$T/alert"; chmod +x "$T/alert"

run() { # run <verify cmd> [args]
  : > "$T/log"; : > "$T/alerts"
  local v=$1; shift
  PATH="$T/bin:$PATH" FAKE_LOG="$T/log" SR_WORK="$T/work" SR_STATE="$T/state" SR_BACKUPS="$T/backups" \
    SR_GIT="$T/up" SR_API="file://$T/api" SR_IMAGE_DIR="$T/repo/image" SR_SEERR_DIR="$T/repo" SR_APPDATA=/srv/appdata/seerr \
    SR_VERIFY="$v" SR_DBCHECK=true SR_ALERT="$T/alert" bash "$SCRIPT" "$@" > "$T/out" 2>&1
}
calls() { grep -E "^($1)" "$T/log" | tr '\n' ';'; }
SHA7=${PR:0:7}

echo "seerr-rebuild.sh"
api v1.0.0 open false
run true; rc=$?
assert_eq "open PR, applies: exit 0" 0 "$rc"
assert_eq "PR file applied onto the stable tag" "oidc" "$(cat "$T/work/seerr/src/oidc.txt" 2>/dev/null)"
assert_eq "develop-only change not taken" "no" "$([ -e "$T/work/seerr/src/dev.txt" ] && echo yes || echo no)"
assert_eq "upstream docs left out" "no" "$([ -e "$T/work/seerr/docs/oidc.md" ] && echo yes || echo no)"
assert_eq "dependency overrides merged, upstream ones kept" '{"a":"1","handlebars@<4.7.10":"4.7.10"}' "$(jq -c .pnpm.overrides "$T/work/seerr/package.json")"
assert_eq "script overrides merged" "next build --webpack" "$(jq -r '.scripts["build:next"]' "$T/work/seerr/package.json")"
assert_has "lockfile refreshed in the Dockerfile's node image" "node:22-alpine@sha256:abc corepack pnpm install --lockfile-only --ignore-scripts" "$(calls run)"
assert_has "upstream tests run in the build stage" "run --rm --network none seerr-local:test sh -c pnpm typecheck && pnpm test" "$(calls run)"
b=$(grep -E '^build ' "$T/log" | grep -v -- '--target' )
assert_has "image tagged <upstream>-oidc<pr sha>" "-t seerr-local:v1.0.0-oidc$SHA7 -t seerr-local:candidate" "$b"
assert_has "commit tag passed to the build" "COMMIT_TAG=v1.0.0-oidc$SHA7" "$b"
TREE=$(sed -n 's/.*holy-grail.seerr.tree=\([0-9a-f]*\).*/\1/p' <<<"$b")
assert_eq "source tree recorded as a label" "40" "${#TREE}"
assert_eq "Seerr stopped before the backup, then swapped and started" "compose stop seerr;compose up -d --no-deps seerr;" "$(calls compose)"
assert_has "backup taken as root in a throwaway container" "-v /srv/appdata/seerr:/d:ro" "$(calls 'run --rm --user 0')"
assert_eq "old image kept as rollback, candidate becomes current" "tag seerr-local:current seerr-local:rollback;tag seerr-local:candidate seerr-local:current;" "$(calls tag)"
assert_eq "no alert" "" "$(cat "$T/alerts")"

FAKE_TREE=$TREE run true; rc=$?
assert_eq "same source tree: exit 0" 0 "$rc"
assert_eq "same source tree: nothing built or swapped" "" "$(calls 'build|tag|compose')"

FAKE_TREE=$TREE run true --force; rc=$?
assert_eq "--force rebuilds the same tree" "0 1" "$rc $(grep -c -E '^build .*candidate' "$T/log")"

run true --no-deploy; rc=$?
assert_eq "--no-deploy: built, nothing swapped" "0 " "$rc $(calls 'tag seerr-local:candidate|compose')"

run false; rc=$?
assert_eq "verify fails: exit 1" 1 "$rc"
assert_eq "rolled back to the old image" "tag seerr-local:current seerr-local:rollback;tag seerr-local:candidate seerr-local:current;tag seerr-local:rollback seerr-local:current;" "$(calls tag)"
assert_has "config restored from the backup" "tar -C /d -xzpf" "$(calls 'run --rm --user 0')"
assert_eq "stopped, started new, stopped, started old" "compose stop seerr;compose up -d --no-deps seerr;compose stop seerr;compose up -d --no-deps seerr;" "$(calls compose)"
assert_has "alert sent" "rolled back" "$(cat "$T/alerts")"

FAKE_UP_FAIL=1 run false; rc=$?
assert_eq "compose up fails: exit 1, rolled back" "1 tag seerr-local:rollback seerr-local:current;" "$rc $(calls tag | sed "s/.*;\\(tag seerr-local:rollback[^;]*;\\)/\\1/")"
assert_has "compose up fails: alert" "rolled back" "$(cat "$T/alerts")"

FAKE_TEST_FAIL=1 run true; rc=$?
assert_eq "upstream tests fail: exit 1, nothing swapped" "1 " "$rc $(calls 'tag|compose')"
assert_has "upstream tests fail: alert" "tests failed" "$(cat "$T/alerts")"

FAKE_BUILD_FAIL=1 run true; rc=$?
assert_eq "build fails: exit 1, nothing swapped" "1 " "$rc $(calls 'tag|compose')"
assert_has "build fails: alert" "build failed" "$(cat "$T/alerts")"

FAKE_BACKUP_FAIL=1 run true; rc=$?
assert_eq "backup fails: exit 1, no swap, Seerr started again" "1 compose stop seerr;compose up -d --no-deps seerr;" "$rc $(calls 'tag|compose')"
assert_has "backup fails: alert" "backup failed" "$(cat "$T/alerts")"

# the PR head moves past the reviewed commit: the reviewed one is built, the owner told once
REVIEWED=$PR
g checkout pr; printf 'moved\n' > "$T/up/src/moved.txt"; g add -A; g commit -m "unreviewed"
PR=$(git -C "$T/up" rev-parse HEAD); g update-ref refs/pull/2715/head "$PR"; g checkout main
api v1.0.0 open false
run true --no-deploy; rc=$?
assert_eq "moved PR: exit 0" 0 "$rc"
assert_has "moved PR: the reviewed commit is built" "-t seerr-local:v1.0.0-oidc${REVIEWED:0:7} -t seerr-local:candidate" "$(grep -E '^build ' "$T/log" | grep -v -- --target)"
assert_eq "moved PR: unreviewed file not built" "no" "$([ -e "$T/work/seerr/src/moved.txt" ] && echo yes || echo no)"
assert_has "moved PR: owner asked to review" "review" "$(cat "$T/alerts")"
run true --no-deploy
assert_eq "moved PR: told only once" "" "$(cat "$T/alerts")"
mv "$T/repo/image/pr-reviewed" "$T/pr-reviewed.bak"
run true --no-deploy; rc=$?
assert_eq "no reviewed commit: exit 2, nothing built" "2 " "$rc $(calls 'build|tag|compose')"
mv "$T/pr-reviewed.bak" "$T/repo/image/pr-reviewed"

# a new stable release that changes the line the PR touches
g checkout pr; printf 'one-pr\n' > "$T/up/src/a.txt"; g add -A; g commit -m "pr touches a"
PR=$(git -C "$T/up" rev-parse HEAD); g update-ref refs/pull/2715/head "$PR"; echo "$PR" > "$T/repo/image/pr-reviewed"; g checkout main
printf 'one-release\n' > "$T/up/src/a.txt"; g add -A; g commit -m release; g tag v1.0.1
api v1.0.1 open false
run true; rc=$?
assert_eq "conflict: exit 2, nothing built" "2 " "$rc $(calls 'build|tag|compose')"
assert_has "conflict: alert names the file" "src/a.txt" "$(cat "$T/alerts")"
assert_has "conflict: alert says the image stays" "current image kept" "$(cat "$T/alerts")"

# the committed resolution for exactly this tag + PR head is used instead
cat > "$T/repo/image/resolutions/v1.0.1-${PR:0:12}.patch" <<EOF
diff --git a/src/a.txt b/src/a.txt
--- a/src/a.txt
+++ b/src/a.txt
@@ -1 +1 @@
-one-release
+one-release-pr
diff --git a/src/oidc.txt b/src/oidc.txt
new file mode 100644
--- /dev/null
+++ b/src/oidc.txt
@@ -0,0 +1 @@
+oidc
EOF
run true --no-deploy; rc=$?
assert_eq "resolution used: exit 0" 0 "$rc"
assert_eq "resolution applied" "one-release-pr oidc" "$(cat "$T/work/seerr/src/a.txt") $(cat "$T/work/seerr/src/oidc.txt")"
rm "$T/repo/image/resolutions/"*.patch

# our own in-image patch on top
printf 'diff --git a/src/ours.txt b/src/ours.txt\nnew file mode 100644\n--- /dev/null\n+++ b/src/ours.txt\n@@ -0,0 +1 @@\n+ours\n' > "$T/repo/image/patches/10-ours.patch"
api v1.0.0 open false
run true --no-deploy; rc=$?
assert_eq "own patch applied after the PR" "0 ours" "$rc $(cat "$T/work/seerr/src/ours.txt" 2>/dev/null)"
rm "$T/repo/image/patches/10-ours.patch"

# PR merged and in a release: plain release, owner told once
g checkout develop; g merge --no-ff -m "merge pr" pr; M=$(git -C "$T/up" rev-parse HEAD)
g checkout main; g merge -X theirs -m "release" develop; g tag v1.1.0
api v1.1.0 closed true "$M"
run true --no-deploy; rc=$?
assert_eq "released: exit 0" 0 "$rc"
assert_has "released: plain upstream tag" "-t seerr-local:v1.1.0 -t seerr-local:candidate" "$(grep -E '^build ' "$T/log" | grep -v -- --target)"
assert_has "released: owner told to go back to the official image" "official image" "$(cat "$T/alerts")"
run true --no-deploy
assert_eq "released: told only once" "" "$(cat "$T/alerts")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
