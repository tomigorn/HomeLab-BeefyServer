#!/usr/bin/env bash
# Radarr/Sonarr Connect "Custom Script" (On Movie Added / On Series Add): puts a new title on the right 4K profile before its first search (README "Languages").
# Runs inside the app container (bash, curl, jq); registered by scripts/language-policy.sh.
set -euo pipefail

ENG_SUFFIX=" - English original"
if [ -n "${radarr_eventtype:-}" ]; then
  event=$radarr_eventtype; want=MovieAdded; id=${radarr_movie_id:-}; kind=movie; ids=movieIds
  MAIN="4K HDR - UHD Bluray + WEB (1080p fallback)"
elif [ -n "${sonarr_eventtype:-}" ]; then
  event=$sonarr_eventtype; want=SeriesAdd; id=${sonarr_series_id:-}; kind=series; ids=seriesIds
  MAIN="4K HDR - WEB-2160p (1080p fallback)"
else
  echo "no radarr_eventtype/sonarr_eventtype: not called by Radarr or Sonarr" >&2; exit 1
fi
[ "$event" = "$want" ] || exit 0

CONFIG=${LP_CONFIG:-/config/config.xml}
xml() { sed -n "s:.*<$1>\(.*\)</$1>.*:\1:p" "$CONFIG"; }
KEY=$(xml ApiKey)
URL="http://localhost:$(xml Port)$(xml UrlBase)/api/v3"
api() { local path=$1; shift; curl -sf -m 10 -H "X-Api-Key: $KEY" -H 'Content-Type: application/json' "$URL$path" "$@"; }

title=$(api "/$kind/$id")
read -r MID EID < <(api /qualityprofile | jq -r --arg m "$MAIN" --arg s "$ENG_SUFFIX" \
  'map({(.name): .id}) | add | "\(.[$m]) \(.[$m + $s])"')
read -r current english < <(jq -r '"\(.qualityProfileId) \(.originalLanguage.name == "English")"' <<<"$title")

target=
[ "$current" = "$MID" ] && [ "$english" = true ] && target=$EID
[ "$current" = "$EID" ] && [ "$english" = false ] && target=$MID
if [ -n "$target" ]; then
  api "/$kind/editor" -X PUT -d "{\"$ids\":[$id],\"qualityProfileId\":$target}" >/dev/null
  echo "$kind $id: profile $current -> $target"
else
  echo "$kind $id: profile $current kept"
fi
