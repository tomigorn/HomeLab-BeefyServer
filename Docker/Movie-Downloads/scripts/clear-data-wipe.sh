#!/usr/bin/env bash
# Seerr "Clear Data" (DELETE /api/v1/media/<id>) has no event: this compares Seerr's media list with the last run and wipes every title whose row is gone, via title-wipe-hook.sh wipe (Radarr/Sonarr + files, qBittorrent, SABnzbd, Jellyfin; README "Delete a title"). Run by systemd/clear-data-wipe.timer.
# A row that is back with a new id and no request was re-created by Seerr's own Jellyfin/Radarr/Sonarr sync: also cleared, and that row is removed after the wipe. A new row with a request (requested again) is left alone.
# Wipes nothing on the first run (no snapshot), when Seerr's list is unreadable, empty or incomplete, when a row is back in a second listing, or when more than CW_MAX rows are gone at once (run once by hand with CW_MAX=<n> if that was intended).
# Skips blocklisted rows (status 6: un-blocklisting deletes the row) and series without a tvdb id. A failed wipe is retried on the next runs (CW_TRIES).
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SEERR=${CW_SEERR_URL:-http://127.0.0.1:5055}
SEERR_KEY=${CW_SEERR_KEY:-$HERE/../../Seerr/secrets/api-key}
HOOK=${CW_HOOK:-$HERE/title-wipe-hook.sh}
DIR=${XDG_STATE_HOME:-$HOME/.local/state}/movie-downloads
SNAP=${CW_SNAPSHOT:-$DIR/seerr-media.tsv}
PENDING=$SNAP.pending
LOG=${CW_LOG:-$DIR/clear-data-wipe.log}
MAX=${CW_MAX:-5}
TRIES=${CW_TRIES:-5}
mkdir -p "$(dirname "$SNAP")" "$(dirname "$LOG")"

log() { local l; while IFS= read -r l; do echo "$l"; echo "$(date '+%F %T') $l" >> "$LOG"; done; }
key=$(tr -d '[:space:]' < "$SEERR_KEY") || { echo "no Seerr API key at $SEERR_KEY" | log; exit 1; }
seerr() { curl -sf -m 30 -H "X-Api-Key: $key" "$SEERR/api/v1$1" "${@:2}" </dev/null; }
details() { seerr "/${1%%:*}/${1#*:}"; } # details <movie|tv>:<tmdbId>: Seerr's page of the title, with .mediaInfo while a row exists
requests() { jq '.mediaInfo.requests // [] | length' <<<"${1:-null}" 2>/dev/null || echo 0; }

# listing: every media row as "<movie|tv>:<tmdbId>	<tvdbId or ->	<status>	<row id>", sorted; fails unless the pages add up to Seerr's own total.
listing() {
  local skip=0 page total rows=""
  while :; do
    page=$(seerr "/media?take=1000&skip=$skip") || return 1
    total=$(jq -er '.pageInfo.results | numbers' <<<"$page" 2>/dev/null) || return 1
    rows+=$(jq -er '.results | arrays | .[] | "\(.mediaType):\(.tmdbId)\t\(.tvdbId // "-")\t\(.status)\t\(.id)"' <<<"$page" 2>/dev/null)$'\n' || [ "$total" -eq 0 ] || return 1
    skip=$((skip + 1000))
    [ "$skip" -lt "$total" ] || break
  done
  rows=$(grep -v '^$' <<<"$rows" | sort -u)
  [ "$(grep -c . <<<"$rows")" -eq "$total" ] || return 1
  printf '%s\n' "$rows" | grep -v '^$'
  return 0
}
# vanished <listing>: snapshot keys whose row is gone or back with another id (snapshot lines without an id: gone only)
vanished() {
  awk -F'\t' 'NR == FNR { id[$1] = $4; next } !($1 in id) || ($4 != "" && id[$1] != $4) { print $1 }' <(printf '%s\n' "$1") "$SNAP" | sort -u
}

now=$(listing) || { echo "Seerr media list unreadable or incomplete, nothing wiped" | log; exit 1; }
if [ ! -s "$SNAP" ]; then
  printf '%s\n' "$now" | grep -v '^$' > "$SNAP.new"; mv "$SNAP.new" "$SNAP"
  echo "first run: snapshot of $(grep -c . "$SNAP") Seerr titles, nothing wiped" | log; exit 0
fi
if [ -z "$now" ]; then
  echo "Seerr lists no titles but $(grep -c . "$SNAP") were there, nothing wiped" | log; exit 1
fi

gone=$(vanished "$now")
if [ -n "$gone" ]; then
  again=$(listing) || { echo "Seerr media list unreadable on the second look, nothing wiped" | log; exit 1; }
  gone=$(comm -12 <(echo "$gone") <(vanished "$again"))
  now=$again
fi
if [ -n "$gone" ] && [ "$(grep -c . <<<"$gone")" -gt "$MAX" ]; then
  echo "$(grep -c . <<<"$gone") Seerr titles gone at once (more than CW_MAX=$MAX), nothing wiped; if intended run once: CW_MAX=<n> $0" | log; exit 1
fi

touch "$PENDING"
for k in $gone; do
  IFS=$'\t' read -r _ tvdb status _ < <(grep -m1 "^$k	" "$SNAP")
  [ "$tvdb" != - ] || tvdb=
  if [ "$status" = 6 ]; then echo "$k: blocklisted row gone (un-blocklisted), nothing wiped" | log; continue; fi
  if [ "${k%%:*}" = tv ] && [ -z "$tvdb" ]; then echo "$k: Seerr row gone but no tvdb id, nothing wiped" | log; continue; fi
  grep -q "^$k	" "$PENDING" || printf '%s\t%s\t0\n' "$k" "${tvdb:--}" >> "$PENDING"
  echo "$k: Seerr row gone (Clear Data), wiping" | log
done

rc=0; removed=""; : > "$PENDING.new"
while IFS=$'\t' read -r k tvdb tries; do
  [ -n "$k" ] || continue
  info=$(details "$k")
  if [ "$(requests "$info")" -gt 0 ]; then echo "$k: requested again in Seerr, nothing wiped" | log; continue; fi
  case ${k%%:*} in
    movie) app=radarr; id=${k#*:}; ty=$(jq -r '"\(.title // "")\u001f\(.releaseDate // "" | .[0:4])"' <<<"${info:-null}" 2>/dev/null);;
    tv) app=sonarr; id=$tvdb; ty=$(jq -r '"\(.name // "")\u001f\(.firstAirDate // "" | .[0:4])"' <<<"${info:-null}" 2>/dev/null);;
    *) continue;;
  esac
  IFS=$'\x1f' read -r title year <<<"${ty:-}"
  if [ "$app" = radarr ]; then config=${CW_RADARR_CONFIG:-/srv/appdata/radarr/config.xml}; else config=${CW_SONARR_CONFIG:-/srv/appdata/sonarr/config.xml}; fi
  if env TW_CONFIG="$config" TW_STATE="$(dirname "$config")/title-wipe.tsv" TW_QBIT_URL="${TW_QBIT_URL:-http://127.0.0.1:8080}" \
      TW_QBIT_PASS="${TW_QBIT_PASS:-$HERE/../secrets/qbit-webui.password}" \
      TW_SAB_URL="${TW_SAB_URL:-http://127.0.0.1:8081}" TW_SAB_KEY="${TW_SAB_KEY:-$HERE/../secrets/hook/sabnzbd.apikey}" \
      TW_JF_URL="${TW_JF_URL:-http://127.0.0.1:8096}" TW_JF_KEY="${TW_JF_KEY:-$HERE/../../Jellyfin/secrets/api-key}" \
      bash "$HOOK" wipe "$app" "$id" "${title:-}" "${year:-0}" </dev/null 2>&1 | log; then
    echo "$k: wiped" | log
    # Seerr's Jellyfin/Radarr/Sonarr sync may have re-created the row (no request) while the title still existed.
    info=$(details "$k")
    orphan=$(jq -r 'select((.mediaInfo.requests // [] | length) == 0) | .mediaInfo.id // empty' <<<"${info:-null}" 2>/dev/null)
    if [ -n "$orphan" ]; then
      if seerr "/media/$orphan" -X DELETE -o /dev/null; then echo "$k: Seerr row $orphan re-created by its sync, removed" | log; removed+=" $orphan"
      else echo "$k: Seerr row $orphan re-created by its sync, removing it failed" | log; rc=1; fi
    fi
  else
    rc=1; tries=$((tries + 1))
    if [ "$tries" -ge "$TRIES" ]; then echo "$k: wipe failed $tries times, gave up (see above)" | log
    else echo "$k: wipe failed (try $tries of $TRIES), retried next run" | log; printf '%s\t%s\t%s\n' "$k" "$tvdb" "$tries" >> "$PENDING.new"; fi
  fi
done < "$PENDING"
mv "$PENDING.new" "$PENDING"

final=$(listing) || final=$now
awk -F'\t' -v r="$removed" 'BEGIN { n = split(r, a, " "); for (i = 1; i <= n; i++) x[a[i]] = 1 } !($4 in x)' <<<"$final" > "$SNAP.new"; mv "$SNAP.new" "$SNAP"
unset key
exit $rc
