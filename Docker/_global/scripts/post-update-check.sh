#!/usr/bin/env bash
# post-update-check.sh <Project|all> | --list
# Verifies that a project's customizations (registry: Docker/CUSTOMIZATIONS.md) survived an image update.
# Checks: Docker/_global/post-update.d/<Project>.sh, one `check <id> <regex> "<actual>"` per customization, `if want <service...>; then ...; fi` scopes a block to compose services.
# Read-only; values are cut to 70 characters and no check prints a secret. Env: SERVICES (only the want-blocks of these services, as update-project.sh sets it), POST_UPDATE_DIR.
# Exit 0 all pass, 1 something missing or broken (last line "FAILED: <ids>"), 2 unknown project.
set -uo pipefail
DIR=${POST_UPDATE_DIR:-$(cd "$(dirname "$0")/.." && pwd)/post-update.d}
# beefy: buntu is outside the docker group on purpose; by hand docker goes through sudo.
if ! docker info >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then docker() { sudo -n docker "$@"; }; export -f docker; fi

projects() { for f in "$DIR"/*.sh; do [[ -e $f ]] && basename "$f" .sh; done; }
[[ ${1:-} == --list || -z ${1:-} ]] && { projects; [[ -n ${1:-} ]]; exit $?; }

PASS=0; FAILED=()
check() { # check <id> <expected-regex> <actual>
  if [[ $3 =~ $2 ]]; then printf '  PASS %-34s %s\n' "$1" "${3:0:70}"; PASS=$((PASS+1))
  else printf '  FAIL %-34s got: %s\n' "$1" "${3:0:70}"; FAILED+=("$1"); fi; }
want() { # want <service...>: true when SERVICES is empty or names one of them
  [[ -z ${SERVICES:-} ]] && return 0
  local s w; for w in "$@"; do for s in $SERVICES; do [[ $s == "$w" ]] && return 0; done; done; return 1; }

run() { echo "== $1"; # shellcheck disable=SC1090
  source "$DIR/$1.sh"; }
if [[ ${1,,} == all ]]; then for p in $(projects); do run "$p"; done
else
  p=$(projects | grep -ixF -- "$1" | head -1)
  [[ -n $p ]] || { echo "no customization checks for $1 (have: $(projects | xargs))"; exit 2; }
  run "$p"
fi
echo "post-update: ${#FAILED[@]} failed, $PASS passed"
(( ${#FAILED[@]} == 0 )) || { echo "FAILED: ${FAILED[*]}"; exit 1; }
