#!/usr/bin/env bash
# Weekly Docker image cleanup. Removes images no container (running or stopped) uses once they have been unused for KEEP_DAYS,
# plus build cache not used for KEEP_DAYS. Never touches volumes, networks or containers.
# Rollback safety: update-project.sh rolls back by re-tagging the previous image ID, so a superseded image must stay until the update is long verified.
# First run (no state yet): only images of repositories no container uses are dated by build/pull time; dangling images and older tags of a repository in use (the likely previous versions) start their window now.
# A plain "image prune --filter until" dates by image build time, not by when it became unused, so this keeps its own first-seen-unused clock per image ID.
# Any image carrying a tag with "rollback" in it (deliberate manual rollback tags) is never removed.
# Images whose reference appears in the repo's compose files or scripts (REPO_DIR) are kept as well: they are something the stack pulls or runs on demand (scanners, parked projects).
# IMAGE_CLEANUP_KEEP: space-separated repositories to always keep (images another host's scheduled job runs here).
# Usage: image-cleanup.sh [--dry-run]
# Env: KEEP_DAYS (14), REPO_DIR (the repo root), IMAGE_CLEANUP_STATE (/var/lib/image-cleanup), IMAGE_CLEANUP_TEXTFILE (/var/lib/node-exporter/textfile), DOCKER (docker command)
set -uo pipefail
KEEP_DAYS=${KEEP_DAYS:-14}
STATE=${IMAGE_CLEANUP_STATE:-/var/lib/image-cleanup}
TEXTFILE=${IMAGE_CLEANUP_TEXTFILE:-/var/lib/node-exporter/textfile}
DRY=0; [[ ${1:-} == --dry-run ]] && DRY=1
DOCKER=${DOCKER:-docker}
if ! $DOCKER info >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then DOCKER="sudo -n docker"; fi
mkdir -p "$STATE" "$TEXTFILE" 2>/dev/null
now=$(date +%s); keep=$((KEEP_DAYS * 86400)); seen="$STATE/first-seen"
REPO_DIR=${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}
corpus=$(mktemp)
find "$REPO_DIR" -type f \( -name '*.yaml' -o -name '*.yml' -o -name '*.sh' -o -name '*.py' -o -name 'Dockerfile*' -o -name '*.service' \) ! -path '*/.git/*' ! -path '*/www/*' ! -path '*/tests/*' ! -name 'test_*' -exec cat {} + > "$corpus" 2>/dev/null
protected() { local r tag; for r in ${1//,/ }; do [[ $r == *rollback* ]] && return 0; for k in ${IMAGE_CLEANUP_KEEP:-}; do [[ ${r%:*} == "$k" ]] && return 0; done; tag=${r##*:}; [[ $tag == "$r" || $r == */*:* && $tag == */* ]] && tag=latest
  grep -qF -- "$r" "$corpus" && return 0; grep -qF -- "${r%:*}:\${" "$corpus" && return 0; [[ $tag == latest ]] && grep -qF -- "${r%:*}" "$corpus" && return 0; done; return 1; }
bootstrap=0; [[ -f $seen ]] || bootstrap=1
log() { echo "$(date '+%F %T') $*"; }
bytes() { local v=${1%B}; [[ -z $v || $v == 0 ]] && { echo 0; return; }; numfmt --from=si "$v" 2>/dev/null || echo 0; }
images_size() { bytes "$($DOCKER system df --format '{{.Type}}|{{.Size}}' | awk -F'|' '$1=="Images"{print $2}')"; }

before=$(images_size)
declare -A used first
while read -r id; do [[ -n $id ]] && used[$id]=1; done < <($DOCKER container ls -a -q | xargs -r $DOCKER inspect -f '{{.Image}}')
[[ -f $seen ]] && while read -r id ts; do first[$id]=$ts; done < "$seen"

removed=0 failed=0 pending=0 newseen=$(mktemp)
mapfile -t ids < <($DOCKER image ls -a -q --no-trunc | sort -u)
if ((${#ids[@]})); then
  mapfile -t rows < <($DOCKER image inspect -f '{{.Id}}|{{.Created}}|{{.Metadata.LastTagTime}}|{{.Size}}|{{join .RepoTags ","}}' "${ids[@]}")
  declare -A usedrepo
  for row in "${rows[@]}"; do IFS='|' read -r id _ _ _ tags <<<"$row"; [[ -n ${used[$id]:-} && -n $tags ]] && for r in ${tags//,/ }; do usedrepo[${r%:*}]=1; done; done
  for row in "${rows[@]}"; do
    IFS='|' read -r id created tagged size tags <<<"$row"
    [[ -n ${used[$id]:-} ]] && continue
    c=$(date -d "$created" +%s 2>/dev/null || echo "$now"); t=$(date -d "$tagged" +%s 2>/dev/null || echo 0); ((t < 0)) && t=0
    if [[ -n $tags ]] && protected "$tags"; then log "kept $tags (referenced by the repo)"; echo "$id $now" >> "$newseen"; pending=$((pending + 1)); continue; fi
    if [[ -n ${first[$id]:-} ]]; then since=${first[$id]}; elif ((bootstrap)) && [[ -n $tags && -z ${usedrepo[${tags%%[:,]*}]:-} ]]; then since=$(( c > t ? c : t )); else since=$now; fi
    ((since > now)) && since=$now
    if ((now - since >= keep)); then
      ref=${tags:-$id}
      if ((DRY)); then log "would remove $ref $size bytes (unused $(( (now - since) / 86400 )) d)"; removed=$((removed + 1)); echo "$id $since" >> "$newseen"; continue; fi
      if [[ -n $tags ]]; then IFS=, read -ra rmargs <<<"$tags"; else rmargs=("$id"); fi
      if $DOCKER image rm "${rmargs[@]}" >/dev/null 2>&1; then
        log "removed $ref $size bytes (unused $(( (now - since) / 86400 )) d)"; removed=$((removed + 1))
      else log "could not remove $ref (kept)"; failed=$((failed + 1)); echo "$id $since" >> "$newseen"; fi
    else pending=$((pending + 1)); echo "$id $since" >> "$newseen"; fi
  done
fi
((DRY)) || mv "$newseen" "$seen"; rm -f "$newseen" "$corpus"

cache=0
if ((DRY)); then log "dry run: build cache not pruned"; else
  out=$($DOCKER builder prune -f --filter "until=$((KEEP_DAYS * 24))h" 2>&1)
  cache=$(bytes "$(awk -F': ' '/Total/{print $2}' <<<"$out" | tail -1)")
  log "build cache freed $cache bytes"
fi
after=$(images_size); img=$((before - after)); ((img < 0)) && img=0; freed=$((img + cache))
log "done: $removed images removed, $pending unused images still within $KEEP_DAYS d, $failed failed, $freed bytes freed (images $img, build cache $cache)"
((DRY)) && exit 0
{
  echo "# TYPE docker_image_cleanup_freed_bytes gauge"; echo "docker_image_cleanup_freed_bytes $freed"
  echo "# TYPE docker_image_cleanup_removed_images gauge"; echo "docker_image_cleanup_removed_images $removed"
  echo "# TYPE docker_image_cleanup_rollback_window_images gauge"; echo "docker_image_cleanup_rollback_window_images $pending"
  echo "# TYPE docker_image_cleanup_failed_images gauge"; echo "docker_image_cleanup_failed_images $failed"
  echo "# TYPE docker_image_cleanup_last_run_timestamp_seconds gauge"; echo "docker_image_cleanup_last_run_timestamp_seconds $now"
} > "$TEXTFILE/.image_cleanup.prom.tmp"
chmod 644 "$TEXTFILE/.image_cleanup.prom.tmp"; mv "$TEXTFILE/.image_cleanup.prom.tmp" "$TEXTFILE/image_cleanup.prom"
