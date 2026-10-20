#!/usr/bin/env bash
# Radarr Connect "Custom Script" (On Grab): an indexer that tags a search result with the searched movie's IDs makes Radarr grab a same-titled film from another year (Treasure Maps: the 1990 remake for the 1968 Night of the Living Dead). A grab more than 2 years off is removed, blocklisted and searched again (README "Wrong-film guard").
# Runs inside the Radarr container (bash, curl, jq); registered by scripts/language-policy.sh.
set -euo pipefail

[ "${radarr_eventtype:-}" = Grab ] || exit 0
WAIT=${GG_WAIT:-90}

CONFIG=${LP_CONFIG:-/config/config.xml}
xml() { sed -n "s:.*<$1>\(.*\)</$1>.*:\1:p" "$CONFIG"; }
KEY=$(xml ApiKey)
URL="http://localhost:$(xml Port)$(xml UrlBase)/api/v3"
api() { local path=$1; shift; curl -sf -m 10 -H "X-Api-Key: $KEY" "$URL$path" "$@"; }

id=$radarr_movie_id
title=$radarr_release_title
got=$(api "/parse?title=$(jq -rn --arg t "$title" '$t | @uri')" | jq -r '.parsedMovieInfo.year // 0')
read -r year second < <(api "/movie/$id" | jq -r '"\(.year // 0) \(.secondaryYear // 0)"')

near() { [ "$1" -gt 0 ] && [ $(( $2 > $1 ? $2 - $1 : $1 - $2 )) -le 2 ]; }
if [ "$got" -eq 0 ] || [ "$year" -eq 0 ] || near "$year" "$got" || near "$second" "$got"; then exit 0; fi

# The grab reaches the queue with the next download-client poll.
for _ in $(seq 0 5 "$WAIT"); do
  qid=$(api "/queue?page=1&pageSize=100&movieIds=$id" | jq -r --arg d "${radarr_download_id:-}" '.records[] | select(.downloadId == $d) | .id' | head -1)
  [ -n "$qid" ] && break
  sleep 5
done
[ -n "$qid" ] || { echo "movie $id: grabbed '$title' is from $got, not $year, but it is not in the queue: remove it by hand" >&2; exit 1; }
api "/queue/$qid?removeFromClient=true&blocklist=true&skipRedownload=false" -X DELETE >/dev/null
echo "movie $id: grabbed '$title' is from $got, not $year: removed, blocklisted, searching again"
