#!/usr/bin/env bash
# run-tests.sh on a throwaway repo: passes, fails, skips by glob. Usage: .github/scripts/test_run_tests.sh
HERE=$(cd "$(dirname "$0")" && pwd)
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ok   $1"; }
bad() { FAIL=$((FAIL+1)); echo "  FAIL $1"; }

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/.github/scripts" "$T/a/tests/blackbox" "$T/b"
cp "$HERE/run-tests.sh" "$T/.github/scripts/"
printf 'import sys\nsys.exit(0)\n' > "$T/a/test_good.py"
printf 'exit 0\n' > "$T/b/test_good.sh"
printf 'exit 0\n' > "$T/b/test-dash.sh"
printf '#!/usr/bin/env bash\nexit 0\n' > "$T/b/test-noext"
printf 'not a test\n' > "$T/b/test_notes.md"
printf 'import sys\nsys.exit(1)\n' > "$T/a/tests/blackbox/test_blackbox.py"
printf '*/tests/blackbox/test_*.py | needs docker\n' > "$T/.github/ci-skip.txt"
git -C "$T" init -q
git -C "$T" add -A

out=$(bash "$T/.github/scripts/run-tests.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "skipped failing test: exit 0" || bad "exit $rc: $out"
grep -q '^SKIP a/tests/blackbox/test_blackbox.py' <<< "$out" && ok "blackbox skipped" || bad "blackbox not skipped"
grep -q 'tests: 4 passed, 0 failed, 1 skipped' <<< "$out" && ok "summary" || bad "summary: $out"

: > "$T/.github/ci-skip.txt"
out=$(bash "$T/.github/scripts/run-tests.sh" 2>&1); rc=$?
[ "$rc" = 1 ] && ok "failing test: exit 1" || bad "exit $rc"
grep -q '^FAIL a/tests/blackbox/test_blackbox.py' <<< "$out" && ok "failure named" || bad "failure not named"
out=$(GITHUB_ACTIONS=true bash "$T/.github/scripts/run-tests.sh" 2>&1)
grep -q '^::error title=a/tests/blackbox/test_blackbox.py::' <<< "$out" && ok "GitHub annotation" || bad "no annotation: $out"

out=$(bash "$T/.github/scripts/run-tests.sh" b 2>&1)
grep -q 'tests: 3 passed, 0 failed, 0 skipped' <<< "$out" && ok "path prefix filter" || bad "filter: $out"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
