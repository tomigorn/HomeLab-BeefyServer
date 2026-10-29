#!/usr/bin/env bash
# Radarr + Sonarr Connect "Custom Script" (On Grab): an indexer that tags a search result with the searched title's IDs makes the app grab a same-titled release from another year (Treasure Maps: the 1990 remake for the 1968 Night of the Living Dead, or a same-named older/newer series). A grab more than 2 years off is removed, blocklisted and searched again; one that never reaches the queue is marked failed through the grab history instead. A grab whose title is on the dub-only list (scripts/nfo-scout.py: the NFO lists two or more languages but not the title's original one) gets the same removal. Each case leaves an event file for scripts/queue-watchdog.py to send as a Telegram (README "Wrong-film guard").
# Radarr compares the release year against the movie's year and secondary year. Sonarr compares the year parsed from the release title against the series' start year (±2), the series' run (start year to last-aired year, or the current year while it continues) and the air years of the grabbed episodes (±1): groups name a season pack with the season's air year (The.Boys.2024.S04), a legitimately old episode of a long-running series is kept.
# Sonarr writes the grab history and lists the queue item only after this script returns, so its removal runs detached (GG_DETACH=0 runs it inline).
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
LOG=${GG_LOG:-/config/grab-guard.log}
EVENTS=${GG_EVENTS:-/config/guard-events}
DUBONLY=${GG_DUBONLY:-/config/dub-only.json}

CONFIG=${LP_CONFIG:-/config/config.xml}
xml() { sed -n "s:.*<$1>\(.*\)</$1>.*:\1:p" "$CONFIG"; }
KEY=$(xml ApiKey)
URL="http://localhost:$(xml Port)$(xml UrlBase)/api/v3"
api() { local path=$1; shift; curl -sf -m 10 -H @<(printf 'X-Api-Key: %s\n' "$KEY") "$URL$path" "$@"; }

enc=$(jq -rn --arg t "$title" '$t | @uri')
case $app in
  radarr)
    noun=movie; qparam=includeUnknownMovieItems; hparam=movieId
    got=$(api "/parse?title=$enc" | jq -r '.parsedMovieInfo.year // 0')
    meta=$(api "/movie/$id")
    second=$(jq -r '.secondaryYear // 0' <<<"$meta")
    ;;
  sonarr)
    noun=series; qparam=includeUnknownSeriesItems; hparam=seriesId; second=0
    got=$(api "/parse?title=$enc" | jq -r '.parsedEpisodeInfo.seriesTitleInfo.year // 0')
    meta=$(api "/series/$id")
    first=$(jq -r '(.firstAired // "")[0:4] | tonumber? // 0' <<<"$meta")
    last=$(jq -r '(.lastAired // "")[0:4] | tonumber? // 0' <<<"$meta")
    [ "$(jq -r '.status // ""' <<<"$meta")" != ended ] && [ "$last" -gt 0 ] && [ "$last" -lt "$(date +%Y)" ] && last=$(date +%Y)
    airyears=$(printf '%s' "${sonarr_release_episodeairdates:-}" | tr ',' '\n' | sed -n 's/^\([0-9]\{4\}\)-.*/\1/p')
    if [ -z "$airyears" ] && [ -n "${sonarr_release_seasonnumber:-}" ]; then
      airyears=$(api "/episode?seriesId=$id&seasonNumber=$sonarr_release_seasonnumber" | jq -r '.[].airDate // empty | .[0:4]') || airyears=
    fi
    ;;
esac

tid=$(jq -r '(if .tmdbId then .tmdbId else .tvdbId end) // 0' <<<"$meta")
year=$(jq -r '.year // 0' <<<"$meta")
mtitle=$(jq -r '.title // ""' <<<"$meta")

inrun() { [ "${first:-0}" -gt 0 ] && [ "${last:-0}" -gt 0 ] && [ "$1" -ge "$first" ] && [ "$1" -le "$last" ]; }
nearair() { local y; for y in $airyears; do [ $(( $1 > y ? $1 - y : y - $1 )) -le 1 ] && return 0; done; return 1; }
near() { [ "$1" -gt 0 ] && [ $(( $2 > $1 ? $2 - $1 : $1 - $2 )) -le 2 ]; }
# The exact release title, the same title id and at least two listed languages, younger than 30 days; no list or an unreadable one = no match.
dub=$(jq -c --arg t "$title" --argjson id "$tid" --argjson min "$(( $(date +%s) - 30 * 86400 ))" \
  '[.[] | select(.title == $t and (.langs | length) >= 2 and .at > $min and (.titleId == $id or .titleId == null or $id == 0))][0] // empty' "$DUBONLY" 2>/dev/null) || dub=
if [ -n "$dub" ]; then
  langs=$(jq -r '.langs | join(", ")' <<<"$dub"); orig=$(jq -r '.original' <<<"$dub")
  why="lists only dubbed audio ($langs), not the original $orig"
elif [ "$got" -eq 0 ] || [ "$year" -eq 0 ] || near "$year" "$got" || near "$second" "$got" || { [ "$app" = sonarr ] && { inrun "$got" || nearair "$got" || near "$first" "$got"; }; }; then
  exit 0
else
  why="is from $got, not $year"
fi

# queue-watchdog.py turns the event file into the Telegram: the container holds no bot token.
event() {
  ( umask 002; mkdir -p "$EVENTS"; f=$EVENTS/$(date +%s)-$$.json
    jq -n --arg app "$app" --arg action "$1" --arg title "$mtitle" --argjson year "$year" --arg release "$title" --argjson releaseYear "$got" --argjson dub "${dub:-null}" \
      '{app: $app, action: $action, title: $title, year: $year, release: $release, releaseYear: $releaseYear}
       + (if $dub then {reason: "dub-only", languages: $dub.langs, original: $dub.original} else {} end)' > "$f.tmp" && mv "$f.tmp" "$f" ) || true
}

if [ "$app" = sonarr ] && [ "${GG_DETACH:-1}" != 0 ] && [ -z "${GG_DETACHED:-}" ]; then
  GG_DETACHED=1 setsid nohup bash "$0" </dev/null >>"$LOG" 2>&1 &
  echo "$noun $id: grabbed '$title' $why: removal scheduled"
  exit 0
fi

# The grab reaches the queue with the next download-client poll. A release the app cannot map to the title (the wrong year) is queued with no movie/series id: it is neither returned by the movieIds/seriesIds filter nor without includeUnknown*Items, so match on the download id over the whole queue.
qid=
for _ in $(seq 0 5 "$WAIT"); do
  qid=$(api "/queue?page=1&pageSize=1000&$qparam=true" | jq -r --arg d "$dlid" '.records[] | select(.downloadId == $d) | .id' | head -1) || qid=
  [ -n "$qid" ] && break
  sleep 5
done
if [ -n "$qid" ]; then
  if api "/queue/$qid?removeFromClient=true&blocklist=true&skipRedownload=false" -X DELETE >/dev/null; then
    event removed
    echo "$noun $id: grabbed '$title' $why: removed, blocklisted, searching again"
    exit 0
  fi
fi

# Not in the queue (a client slow to report): mark the grab failed through the history, which blocklists it and searches again.
hid=
[ -n "$dlid" ] && hid=$(api "/history/$noun?$hparam=$id" | jq -r --arg d "$dlid" '[.[] | select(.eventType == "grabbed" and .downloadId == $d)][0].id // empty') || hid=
if [ -n "$hid" ] && api "/history/failed/$hid" -X POST >/dev/null; then
  event marked-failed
  echo "$noun $id: grabbed '$title' $why: not in the queue, marked failed in history, blocklisted, searching again"
  exit 0
fi
event manual
echo "$noun $id: grabbed '$title' $why, but it is not in the queue or in history: remove it by hand" >&2
exit 1
