#!/usr/bin/env bash
# Radarr + Sonarr Connect "Custom Script" (On Grab): an indexer that tags a search result with the searched title's IDs makes the app grab a same-titled release from another year (Treasure Maps: the 1990 remake for the 1968 Night of the Living Dead, or a same-named older/newer series). A grab more than 2 years off is removed, blocklisted and searched again (README "Wrong-film guard").
# Radarr compares the release year against the movie's year and secondary year. Sonarr compares the year parsed from the release title (the series' disambiguation year, always the series' start year) against the series' firstAired year, never the episode's air date, so a legitimately old episode of a long-running current series is kept.
# Runs inside the Radarr/Sonarr container (bash, curl, jq); registered by scripts/language-policy.sh.
set -euo pipefail

if [ "${radarr_eventtype:-}" = Grab ]; then
  app=radarr; id=$radarr_movie_id; title=$radarr_release_title; dlid=${radarr_download_id:-}
elif [ "${sonarr_eventtype:-}" = Grab ]; then
  app=sonarr; id=$sonarr_series_id; title=$sonarr_release_title; dlid=${sonarr_download_id:-}
else
  exit 0
fi
WAIT=${GG_WAIT:-90}

CONFIG=${LP_CONFIG:-/config/config.xml}
xml() { sed -n "s:.*<$1>\(.*\)</$1>.*:\1:p" "$CONFIG"; }
KEY=$(xml ApiKey)
URL="http://localhost:$(xml Port)$(xml UrlBase)/api/v3"
api() { local path=$1; shift; curl -sf -m 10 -H "X-Api-Key: $KEY" "$URL$path" "$@"; }

enc=$(jq -rn --arg t "$title" '$t | @uri')
case $app in
  radarr)
    noun=movie; qparam=movieIds
    got=$(api "/parse?title=$enc" | jq -r '.parsedMovieInfo.year // 0')
    read -r year second < <(api "/movie/$id" | jq -r '"\(.year // 0) \(.secondaryYear // 0)"')
    ;;
  sonarr)
    noun=series; qparam=seriesIds; second=0
    got=$(api "/parse?title=$enc" | jq -r '.parsedEpisodeInfo.seriesTitleInfo.year // 0')
    year=$(api "/series/$id" | jq -r '.year // 0')
    ;;
esac

near() { [ "$1" -gt 0 ] && [ $(( $2 > $1 ? $2 - $1 : $1 - $2 )) -le 2 ]; }
if [ "$got" -eq 0 ] || [ "$year" -eq 0 ] || near "$year" "$got" || near "$second" "$got"; then exit 0; fi

# The grab reaches the queue with the next download-client poll.
for _ in $(seq 0 5 "$WAIT"); do
  qid=$(api "/queue?page=1&pageSize=100&$qparam=$id" | jq -r --arg d "$dlid" '.records[] | select(.downloadId == $d) | .id' | head -1)
  [ -n "$qid" ] && break
  sleep 5
done
[ -n "$qid" ] || { echo "$noun $id: grabbed '$title' is from $got, not $year, but it is not in the queue: remove it by hand" >&2; exit 1; }
api "/queue/$qid?removeFromClient=true&blocklist=true&skipRedownload=false" -X DELETE >/dev/null
echo "$noun $id: grabbed '$title' is from $got, not $year: removed, blocklisted, searching again"
