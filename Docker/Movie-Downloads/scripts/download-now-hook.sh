#!/usr/bin/env bash
# Radarr/Sonarr Connect "Custom Script" (On Import, On Upgrade): download-now (Seerr "Can wait" unticked) counts for the first download only. After an import the tag comes off the title, so upgrades and later episodes go to the night clients again (README "Can wait: downloads at night").
# Runs inside the app container (bash, curl, jq); registered by scripts/language-policy.sh. Idempotent.
set -euo pipefail

if [ -n "${radarr_eventtype:-}" ]; then
  event=$radarr_eventtype; kind=movie; ids=movieIds; id=${radarr_movie_id:-}
elif [ -n "${sonarr_eventtype:-}" ]; then
  event=$sonarr_eventtype; kind=series; ids=seriesIds; id=${sonarr_series_id:-}
else
  echo "no radarr_eventtype/sonarr_eventtype: not called by Radarr or Sonarr" >&2; exit 1
fi
[ "$event" = Download ] && [ -n "$id" ] || exit 0

CONFIG=${DN_CONFIG:-/config/config.xml}
xml() { sed -n "s:.*<$1>\(.*\)</$1>.*:\1:p" "$CONFIG"; }
KEY=$(xml ApiKey)
URL="http://localhost:$(xml Port)$(xml UrlBase)/api/v3"
api() { local path=$1; shift; curl -sf -m 10 -H "X-Api-Key: $KEY" -H 'Content-Type: application/json' "$URL$path" "$@"; }

tag=$(api /tag | jq '[.[] | select(.label == "download-now") | .id][0] // empty')
[ -n "$tag" ] || exit 0
api "/$kind/$id" | jq -e --argjson t "$tag" '(.tags // []) | index($t)' >/dev/null || exit 0
api "/$kind/editor" -X PUT -d "{\"$ids\":[$id],\"tags\":[$tag],\"applyTags\":\"remove\"}" >/dev/null
echo "$kind $id: download-now removed after the import, later downloads wait for the night"
