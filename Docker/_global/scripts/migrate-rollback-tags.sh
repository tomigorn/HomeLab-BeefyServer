#!/usr/bin/env bash
# Moves the old <repo>:rollback-<rest> tags to rollback.invalid/<repo>:<date>-<project> (the one namespace Cup excludes, Docker/_global/UPDATE-RUNBOOK.md "Rollback tags").
# Re-tags first, checks the new tag names the same image, then removes only the old tag name: images are never removed.
# <rest> that does not start with a date gets the tag's date in front. A tag a container runs by name is left alone. Idempotent.
# Usage: migrate-rollback-tags.sh [--dry-run]   Env: DOCKER (docker command)
set -uo pipefail
DRY=0; [[ ${1:-} == --dry-run ]] && DRY=1
DOCKER=${DOCKER:-docker}
if ! $DOCKER info >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then DOCKER="sudo -n docker"; fi
inuse=$($DOCKER ps -a --format '{{.Image}}')
moved=0 failed=0
while read -r ref; do
  tag=${ref##*:}; repo=${ref%:*}
  [[ $ref == *:* && $tag == rollback-* && $repo != rollback.invalid/* ]] || continue
  grep -qxF -- "$ref" <<<"$inuse" && { echo "kept $ref (a container runs it by this name)"; continue; }
  read -r id tagged < <($DOCKER image inspect -f '{{.Id}} {{.Metadata.LastTagTime}}' "$ref")
  rest=${tag#rollback-}
  if [[ ! $rest =~ ^[0-9]{4}-?[0-9]{2}-?[0-9]{2} ]]; then
    d=${tagged:0:10}; [[ $d =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ && $d != 0001-01-01 ]] || d=$(date +%F); rest="$d-$rest"
  fi
  new="rollback.invalid/${repo//:/-}:$rest"
  if ((DRY)); then echo "would move $ref -> $new"; continue; fi
  if $DOCKER tag "$id" "$new" && [[ $($DOCKER image inspect -f '{{.Id}}' "$new" | cut -d' ' -f1) == "$id" ]] && $DOCKER image rm "$ref" >/dev/null; then
    echo "moved $ref -> $new"; moved=$((moved + 1))
  else echo "FAILED $ref (old tag kept)"; failed=$((failed + 1)); fi
done < <($DOCKER image ls --format '{{.Repository}}:{{.Tag}}' | sort -u)
echo "done: $moved moved, $failed failed"
((failed == 0))
