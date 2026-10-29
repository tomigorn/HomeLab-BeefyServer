#!/usr/bin/env bash
# update-project.sh <compose-dir> <mode> [backup-paths...]
#   mode: pull | build | recreate | snapshot (saves compose + .env BEFORE you edit an image ref; the next run restores it on rollback)
# Follows _global/UPDATE-RUNBOOK.md: snapshot image IDs, pull/build, stop and back up stateful paths
# (bind dirs or "vol:<name>"), start/recreate, wait, verify every container of the
# project is running (and healthy if it has a healthcheck) with no crash loop;
# on failure restore compose/.env from the snapshot, re-tag the old images (tag refs) and recreate on the old image ids, pinned when compose still names another ref (automatic rollback).
# Before any change every old image gets <repo>:rollback-<date>-<project> (a dangling prune cannot take it); the last ROLLBACK_KEEP (3) per repo and project stay.
# Env: SERVICES (pull/build/recreate only these, --no-deps; the verify still covers the whole project), WAIT (45), CHECK (functional check, a shell command; retried CHECK_TRIES=6 times 10 s apart),
#      POST_CHECK=0 (skip the customization check: post-update-check.sh runs after CHECK whenever _global/post-update.d/<dir name>.sh exists, Docker/CUSTOMIZATIONS.md),
#      TEST_FORCE_FAIL=1 (the verify fails: rollback drill). Exit 0 ok, 1 rolled back, 3 rollback did not verify.
set -uo pipefail
# beefy: buntu is outside the docker group on purpose; by hand it goes through sudo, the auto-update service has the group.
if ! docker info >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then docker() { sudo -n docker "$@"; }; export -f docker; fi
dir=$1; mode=$2; shift 2
here=$(cd "$(dirname "$0")" && pwd)
cd "$dir" || { echo "FAIL $dir: no such dir"; exit 2; }
proj=$(docker compose config --format json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
bk="$HOME/backups/pre-update/$(date +%F)/$proj"; mkdir -p "$bk"
pending="$HOME/backups/pre-update/pending/$proj"
name=$(basename "$(pwd -P)")

# The files that define the image refs: the compose files the containers were started from (or the default names here) and .env.
compose_files() {
  local ids f; ids=$(docker compose ps -q 2>/dev/null)
  [[ -n $ids ]] && docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' $ids 2>/dev/null | tr ',' '\n' | sort -u | grep -v '^$' && return
  for f in compose.yaml compose.yml docker-compose.yaml docker-compose.yml; do [[ -f $f ]] && { echo "$PWD/$f"; break; }; done
  for f in compose.override.yaml compose.override.yml docker-compose.override.yaml docker-compose.override.yml; do [[ -f $f ]] && echo "$PWD/$f"; done
}
take_snapshot() {  # take_snapshot <dir>: copies the files with their absolute paths under <dir>/root, list in <dir>/files
  local to=$1 f; rm -rf "$to"; mkdir -p "$to/root"; chmod 700 "$to"
  { compose_files; [[ -f .env ]] && echo "$PWD/.env"; } | sort -u > "$to/files"
  while read -r f; do [[ -f $f ]] && { cp --parents "$f" "$to/root" 2>/dev/null || sudo -n cp --parents "$f" "$to/root"; }; done < "$to/files"
  touch "$to/taken"
}
restore_snapshot() {
  local f n=0; [[ -f $snap/files ]] || return 0
  while read -r f; do
    [[ -f $snap/root$f ]] && ! cmp -s "$snap/root$f" "$f" 2>/dev/null || continue
    { cp "$snap/root$f" "$f" 2>/dev/null || sudo -n cp "$snap/root$f" "$f"; } && { echo "  rollback: restored $f"; n=$((n + 1)); }
  done < "$snap/files"
  (( n )) || echo "  rollback: compose/.env unchanged since the snapshot"
}

if [[ $mode == snapshot ]]; then
  take_snapshot "$pending"; echo "snapshot $proj: $(wc -l < "$pending/files") files -> $pending (edit the image refs now, then run pull|build|recreate within 12 h)"; exit 0
fi
echo "=== $proj ($dir) mode=$mode"
snap="$bk/compose-snapshot-$(date +%H%M%S)"
if [[ -f $pending/taken ]] && (( $(date +%s) - $(stat -c %Y "$pending/taken") < 43200 )); then
  mv "$pending" "$snap"; echo "  snapshot: the one taken before the edit -> $snap"
else
  [[ -d $pending ]] && { echo "  snapshot: pending one is stale (> 12 h), not used"; rm -rf "$pending"; }
  take_snapshot "$snap"; echo "  snapshot: compose/.env -> $snap"
fi
custom=""
[[ ${POST_CHECK:-1} != 0 && -f ${POST_UPDATE_DIR:-$here/../post-update.d}/$name.sh ]] && custom="$here/post-update-check.sh $name"
# custom_check: the project's customizations (post-update-check.sh), retried like CHECK; on failure prints the missing ids.
custom_check() {
  local out i
  for i in $(seq 1 "${CHECK_TRIES:-6}"); do out=$(bash -c "$custom" 2>&1) && { echo "  custom: OK ($name)"; return 0; }; (( i < ${CHECK_TRIES:-6} )) && sleep 10; done
  echo "  custom: FAIL $(sed -n 's/^FAILED: //p' <<<"$out" | tail -1)"; grep -E '^  FAIL ' <<<"$out" | head -8 | sed 's/^/  /'; return 1
}

# snapshot: container -> "ref|imageid|containerid|restartcount|service|rollbacktag". The container id
# and RestartCount are the baseline: RestartCount is a LIFETIME counter, so the
# verify must compare the DELTA during this update, not demand it be 0 - a project
# member that restarted once days ago and is NOT recreated here would otherwise
# trip a false rollback.
declare -A oldid rbid
while read -r cname ref id rc svc _; do
  iid=$(docker inspect -f '{{.Image}}' "$cname")
  repo=${ref%%@*}; [[ ${repo##*/} == *:* ]] && repo=${repo%:*}
  rb="$repo:rollback-$(date +%F)-$proj"; [[ -n ${rbid[$rb]:-} && ${rbid[$rb]} != "$iid" ]] && rb+="-$cname"
  if [[ -n $iid && $ref != sha256:* ]] && docker tag "$iid" "$rb" 2>/dev/null; then rbid[$rb]=$iid; else rb=""; fi
  oldid[$cname]="$ref|$iid|$id|$rc|$svc|$rb"
done < <(ids=$(docker compose ps -q); [[ -n $ids ]] && docker inspect -f '{{.Name}} {{.Config.Image}} {{.Id}} {{.RestartCount}} {{index .Config.Labels "com.docker.compose.service"}} {{index .Config.Labels "com.docker.compose.project.config_files"}}' $ids | sed 's|^/||')
for n in "${!oldid[@]}"; do echo "  before: $n ${oldid[$n]}"; done
# keep the newest ROLLBACK_KEEP rollback tags per repo for this project
for rb in "${!rbid[@]}"; do
  docker image ls --format '{{.Repository}}:{{.Tag}}' "${rb%:*}" 2>/dev/null | grep -E ":rollback-[0-9]{4}-[0-9]{2}-[0-9]{2}-$proj\$" | sort -r | tail -n +$(( ${ROLLBACK_KEEP:-3} + 1 )) | xargs -r docker image rm >/dev/null 2>&1
done

# Traefik: the new tag must load the local plugins before it may replace the edge (Traefik README "Updating Traefik").
if [[ -x scripts/traefik-plugin-test.sh ]]; then
  t=$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["traefik"]["image"].rsplit(":",1)[1])')
  scripts/traefik-plugin-test.sh "$t" || { echo "FAIL $proj: plugin test failed on traefik:$t, nothing changed"; exit 1; }
fi

case $mode in
  pull)     docker compose pull -q ${SERVICES:-} 2>&1 | grep -viE 'pulling|pulled|^$' | head -5 ;;
  build)    docker compose build --pull -q ${SERVICES:-} 2>&1 | grep -viE '^\s*$' | tail -3 ;;
  recreate) : ;;
esac

# backups: cold (services stopped after the pull), so a live database is never copied mid-write
if (( $# )); then docker compose stop ${SERVICES:-} 2>&1 | grep -E 'Stopped|Error|error' | head -8; fi
for b in "$@"; do
  if [[ $b == vol:* ]]; then v=${b#vol:}; docker run --rm -v "$v":/from:ro -v "$bk":/to alpine tar czf "/to/vol-$v.tgz" -C /from . && echo "  backup: volume $v -> $bk"
  else [[ -e $b ]] && sudo tar czf "$bk/$(basename "$b").tgz" -C "$(dirname "$b")" "$(basename "$b")" && echo "  backup: $b -> $bk"; fi
done

docker compose up -d --remove-orphans ${SERVICES:+--no-deps $SERVICES} 2>&1 | grep -E 'Recreat|Creat|Error|error' | head -8

sleep "${WAIT:-45}"
ok=1; svcs=""
for n in "${!oldid[@]}"; do
  IFS='|' read -r _ oldimg oldcid oldrc _ _ <<< "${oldid[$n]}"
  info=$(docker inspect -f '{{.State.Status}}|{{if index .State "Health"}}{{(index .State "Health").Status}}{{else}}nohc{{end}}|{{.Image}}|{{.Id}}|{{.RestartCount}}' "$n" 2>/dev/null || echo "missing|-|-|-|0")
  IFS='|' read -r status health newimg newcid newrc <<< "$info"
  # A recreated container is a NEW container (id changed) whose count starts at 0;
  # an untouched one keeps its old id and lifetime count. A crash loop shows up as
  # restarts DURING this window: >=2 on a fresh container, or any rise on an
  # untouched one.
  if [[ "$newcid" == "$oldcid" ]]; then crashloop=$(( newrc > oldrc )); else crashloop=$(( newrc >= 2 )); fi
  upd=$( [[ "$newimg" == "$oldimg" ]] && echo unchanged || echo UPDATED )
  echo "  after : $n $status $health rc=$oldrc->$newrc  image $upd"
  [[ $upd == UPDATED || $status != running ]] && svcs+=" $(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$n" 2>/dev/null)"
  [[ $status == running && ( $health == healthy || $health == nohc || $health == starting ) && $crashloop -eq 0 ]] || ok=0
done
if (( ok )) && [[ -n ${CHECK:-} ]]; then
  for i in $(seq 1 "${CHECK_TRIES:-6}"); do bash -c "$CHECK" >/dev/null 2>&1 && break; (( i < ${CHECK_TRIES:-6} )) && sleep 10; done \
    && echo "  check : OK ($CHECK)" || { echo "  check : FAIL ($CHECK)"; ok=0; }
fi
if (( ok )) && [[ -n $custom ]]; then custom_check || ok=0; fi
[[ ${TEST_FORCE_FAIL:-0} == 1 ]] && { echo "  check : FAIL (forced by TEST_FORCE_FAIL)"; ok=0; }
if (( ok )); then echo "OK $proj"; exit 0; fi

echo "ROLLBACK $proj${svcs:+ (only:$svcs)}"
restore_snapshot
# a tag ref points at the old image again; a digest ref still names it
for n in "${!oldid[@]}"; do IFS='|' read -r ref iid _ _ _ _ <<< "${oldid[$n]}"; [[ $ref == *@* || $ref == sha256:* ]] || docker tag "$iid" "$ref" 2>/dev/null; done
# only what changed or is down; nothing changed (a forced drill) = SERVICES, else the whole project
svcs=${svcs:-${SERVICES:-}}
# compose still naming another ref than the old container ran (edited without a snapshot): pin that service to its rollback tag
want=$(docker compose config --format json 2>/dev/null | python3 -c 'import json,sys; [print(k, v.get("image","")) for k, v in json.load(sys.stdin)["services"].items()]' 2>/dev/null)
pins=""
for n in "${!oldid[@]}"; do
  IFS='|' read -r ref _ _ _ svc rb <<< "${oldid[$n]}"
  [[ -z $svcs || " $svcs " == *" $svc "* ]] || continue
  now=$(awk -v s="$svc" '$1==s {print $2}' <<<"$want")
  [[ -n $now && $now != "$ref" ]] || continue
  if [[ -n $rb ]]; then pins+="  $svc:"$'\n'"    image: $rb"$'\n'"    pull_policy: never"$'\n'; echo "  rollback: WARN compose names $now for $svc, the old container ran $ref: pinned to $rb until you revert that edit (the next up -d moves it)"
  else echo "  rollback: WARN compose names $now for $svc, the old container ran $ref, and there is no rollback tag"; fi
done
fargs=()
if [[ -n $pins ]]; then
  printf 'services:\n%s' "$pins" > "$bk/rollback-override.yaml"
  while read -r f; do fargs+=(-f "$f"); done < <(compose_files)
  fargs=(-p "$proj" "${fargs[@]}" -f "$bk/rollback-override.yaml")
fi
docker compose "${fargs[@]}" up -d --force-recreate ${svcs:+--no-deps $svcs} 2>&1 | tail -3; sleep "${ROLLBACK_WAIT:-20}"
docker compose ps --format '{{.Name}} {{.Status}}'
back=1
for n in "${!oldid[@]}"; do
  IFS='|' read -r _ oldimg _ _ _ _ <<< "${oldid[$n]}"
  [[ $(docker inspect -f '{{.State.Status}} {{.Image}}' "$n" 2>/dev/null) == "running $oldimg" ]] || { echo "  rollback: $n not running on its old image"; back=0; }
done
if (( back )) && [[ -n ${CHECK:-} ]]; then
  for i in $(seq 1 "${CHECK_TRIES:-6}"); do bash -c "$CHECK" >/dev/null 2>&1 && break; (( i < ${CHECK_TRIES:-6} )) && sleep 10; done || { echo "  rollback: check still failing"; back=0; }
fi
if (( back )) && [[ -n $custom ]]; then custom_check || { echo "  rollback: customizations still missing"; back=0; }; fi
(( back )) && { echo "ROLLED BACK $proj"; exit 1; }
echo "ROLLBACK FAILED $proj"
exit 3
