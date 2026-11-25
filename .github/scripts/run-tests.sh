#!/usr/bin/env bash
# Runs every test_* / test-* script (.py, .sh or no extension) of the repo, except those in .github/ci-skip.txt (need Docker, a live host or root). Usage: .github/scripts/run-tests.sh [PATH_PREFIX ...]
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SKIP="$ROOT/.github/ci-skip.txt"
LIMIT=${TEST_TIMEOUT:-180}
export CI=true
cd "$ROOT" || exit 2

skipped() {
    local glob
    while IFS= read -r glob; do
        glob=${glob%%|*}; glob=${glob%"${glob##*[![:space:]]}"}
        # shellcheck disable=SC2254
        case "$1" in $glob) return 0 ;; esac
    done < <(grep -vE '^[[:space:]]*(#|$)' "$SKIP" 2>/dev/null)
    return 1
}

mapfile -t TESTS < <(git ls-files | grep -v '/plugins-local/' | grep -E '(^|/)test[_-][^/.]*(\.py|\.sh)?$' | sort)
pass=0; fail=0; skip=0; failed=()
for t in "${TESTS[@]}"; do
    if [ "$#" -gt 0 ]; then
        match=0
        for p in "$@"; do case "$t" in "$p"*) match=1 ;; esac; done
        [ "$match" = 1 ] || continue
    fi
    if skipped "$t"; then skip=$((skip+1)); echo "SKIP $t"; continue; fi
    case "$t" in
        *.py) cmd=(python3 "$t") ;;
        *) cmd=(bash "$t") ;;
    esac
    if out=$(timeout "$LIMIT" "${cmd[@]}" 2>&1 </dev/null); then
        pass=$((pass+1)); echo "ok   $t"
    else
        rc=$?
        fail=$((fail+1)); failed+=("$t"); echo "FAIL $t (exit $rc)"
        printf '%s\n' "$out" | tail -n 25 | sed 's/^/     | /'
        [ "${GITHUB_ACTIONS:-}" = true ] && echo "::error title=$t::$(printf '%s' "$out" | tail -n 8 | sed 's/%/%25/g' | sed ':a;N;$!ba;s/\n/%0A/g')"
    fi
done
echo "tests: $pass passed, $fail failed, $skip skipped"
[ "$fail" -eq 0 ] || { printf 'failed: %s\n' "${failed[@]}"; exit 1; }
