#!/usr/bin/env bash
# After Radarr/Sonarr imported files: makes Seerr show them as Available within minutes instead of at the 04:22 full scan (README "Seerr availability after an import").
# seerr-sync-hook.sh (Connect script) appends the imported file paths to <appdata>/<app>/seerr-sync.queue; systemd/seerr-sync.path starts this script, which debounces (waits until QUIET s after the last import, at most CAP s after the first, at least MIN_GAP s after the previous run), asks Jellyfin to scan files it does not have yet, then runs Seerr's "Jellyfin Full Library Scan" (Media Availability Sync alone does not flip titles with an old DateCreated).
# Claimed queue files are deleted only after the Seerr scan request succeeded (back in the queue otherwise, exit 1); imports that land during a round get another round (systemd ignores PathChanged while this runs), up to BUDGET s.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
QUEUES=${SS_QUEUES:-/srv/appdata/radarr/seerr-sync.queue /srv/appdata/sonarr/seerr-sync.queue}
DIR=${XDG_STATE_HOME:-$HOME/.local/state}/movie-downloads
LASTRUN=${SS_LASTRUN:-$DIR/seerr-sync.last}
LOG=${SS_LOG:-$DIR/seerr-sync.log}
SEERR=${SS_SEERR_URL:-http://127.0.0.1:5055}
SEERR_KEY=${SS_SEERR_KEY:-$HERE/../../Seerr/secrets/api-key}
JF=${SS_JF_URL:-http://127.0.0.1:8096}
JF_KEY=${SS_JF_KEY:-$HERE/../../Jellyfin/secrets/api-key}
QUIET=${SS_QUIET:-120}
CAP=${SS_CAP:-600}
MIN_GAP=${SS_MIN_GAP:-600}
JF_WAIT=${SS_JF_WAIT:-90}
SCAN_WAIT=${SS_SCAN_WAIT:-300}
POLL=${SS_POLL:-5}
BUDGET=${SS_BUDGET:-12600}
FROM=${SS_MAP_FROM:-/data/media/}
TO=${SS_MAP_TO:-/media/}
mkdir -p "$DIR"

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }
exec 9> "$LASTRUN.lock"
flock -n 9 || { echo "another run is waiting or running, it takes the queue"; exit 0; }

newest() { local f m=0 t; for f in $QUEUES; do t=$(stat -c %Y "$f" 2>/dev/null) && [ "$t" -gt "$m" ] && m=$t; done; echo "$m"; }
debounce() {
  local now last prev quiet_at cap_at gap_at ready first=$(date +%s)
  while :; do
    now=$(date +%s); last=$(newest); prev=$(cat "$LASTRUN" 2>/dev/null || echo 0)
    quiet_at=$((last + QUIET)); cap_at=$((first + CAP)); gap_at=$((prev + MIN_GAP))
    ready=$quiet_at; [ "$cap_at" -lt "$ready" ] && ready=$cap_at; [ "$gap_at" -gt "$ready" ] && ready=$gap_at
    [ "$now" -ge "$ready" ] && return
    sleep "$POLL"
  done
}
# claim: moves each queue aside (the apps keep appending to a new one) and also takes claimed files a killed run left; they are removed only after a successful round
claim() {
  local q c
  claimed=(); paths=""
  for q in $QUEUES; do
    [ -s "$q" ] && mv "$q" "$q.claimed.$$" 2>/dev/null
    for c in "$q".claimed.*; do [ -f "$c" ] && claimed+=("$c") && paths+=$(cat "$c")$'\n'; done
  done
  paths=$(grep -v '^$' <<<"$paths" | sort -u)
}
unclaim() { local c; for c in "${claimed[@]}"; do cat "$c" >> "${c%.claimed.*}" && rm -f "$c"; done; }
pending() { local q; for q in $QUEUES; do [ -s "$q" ] && return 0; done; return 1; }

jf() { curl -sf -m 30 -H "Authorization: MediaBrowser Token=\"$jk\"" "$JF$1" "${@:2}" </dev/null; }
missing() { # paths (Jellyfin side) not known to Jellyfin
  local have; have=$(jf "/Items?Recursive=true&IncludeItemTypes=Movie,Episode&Fields=Path&EnableImages=false&EnableUserData=false" | jq -r '.Items[].Path // empty') || return 1
  grep -vxFf <(echo "$have") <<<"$1" || true
}
seerr() { curl -sf -m 30 -H "X-Api-Key: $sk" "$SEERR/api/v1$1" "${@:2}" </dev/null; }
running() { seerr /settings/jobs | jq -r '.[] | select(.id == "jellyfin-full-scan") | .running'; }

round() {
  local want todo upd waited t0
  log "start: $(grep -c . <<<"$paths") imported file(s)"
  jk=$(tr -d '[:space:]' < "$JF_KEY") || { log "no Jellyfin API key at $JF_KEY"; return 1; }
  want=$(sed "s#^$FROM#$TO#" <<<"$paths")
  todo=$(missing "$want")
  if [ -z "${todo:-}" ] && ! jf "/System/Info/Public" >/dev/null; then log "Jellyfin not reachable, Seerr scan only"; fi
  if [ -n "${todo:-}" ]; then
    upd=$(jq -Rn '{Updates: [inputs | select(length > 0) | {Path: ., UpdateType: "Created"}]}' <<<"$todo")
    jf /Library/Media/Updated -X POST -H 'Content-Type: application/json' -d "$upd" >/dev/null && log "Jellyfin asked to scan $(grep -c . <<<"$todo") file(s)" || log "Jellyfin scan request failed"
    waited=0
    while [ -n "${todo:-}" ] && [ "$waited" -lt "$JF_WAIT" ]; do sleep "$POLL"; waited=$((waited + POLL)); todo=$(missing "$want"); done
    [ -z "${todo:-}" ] && log "Jellyfin has the files" || log "Jellyfin still lacks $(grep -c . <<<"$todo") file(s) after $JF_WAIT s, scanning Seerr anyway"
  fi
  sk=$(tr -d '[:space:]' < "$SEERR_KEY") || { log "no Seerr API key at $SEERR_KEY"; return 1; }
  t0=$(date +%s)
  for _ in $(seq 60); do [ "$(running)" = false ] && break; sleep "$POLL"; done
  seerr /settings/jobs/jellyfin-full-scan/run -X POST >/dev/null || { log "Seerr scan request failed"; return 1; }
  date +%s > "$LASTRUN"
  sleep "$POLL"
  while [ "$(running)" = true ] && [ $(( $(date +%s) - t0 )) -lt "$SCAN_WAIT" ]; do sleep "$POLL"; done
  log "Seerr full scan done in $(( $(date +%s) - t0 )) s"
}

begin=$(date +%s)
while :; do
  debounce
  claim
  if [ -z "$paths" ]; then rm -f "${claimed[@]}"; echo "queue empty, nothing to do"; exit 0; fi
  round || { unclaim; log "claimed files back in the queue for the next run"; exit 1; }
  rm -f "${claimed[@]}"
  pending || exit 0
  if [ $(( $(date +%s) - begin )) -ge "$BUDGET" ]; then log "imports still queued after $BUDGET s, the next import or the 04:22 scan takes them"; exit 0; fi
  log "imports arrived during this round, another round"
done
