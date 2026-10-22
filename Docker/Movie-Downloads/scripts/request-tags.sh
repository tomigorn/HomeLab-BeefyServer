#!/usr/bin/env bash
# Seerr sends a request's tags only when it adds a new title; for a title already in Radarr/Sonarr ("Movie is already monitored") they are dropped. This copies them over, runs the language hook (Swiss German profile, subtitles) and searches (README "Per-request audio and subtitles"). Run by systemd/request-tags.timer.
# Each approved request is handled once (RT_STATE), so a tag removed by hand later stays removed. RT_SEARCH=0: no search.
# Also keeps the Swiss German profile on language Any, which Recyclarr resets to Original whenever it updates that profile.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SEERR=${RT_SEERR_URL:-http://127.0.0.1:5055}
SEERR_KEY=${RT_SEERR_KEY:-$HERE/../../Seerr/secrets/api-key}
HOOK=${RT_HOOK:-$HERE/language-policy-hook.sh}
STATE=${RT_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/movie-downloads/request-tags.done}
SEARCH=${RT_SEARCH:-1}
mkdir -p "$(dirname "$STATE")" && touch "$STATE"

key=$(tr -d '[:space:]' < "$SEERR_KEY") || exit 1
requests=$(curl -sf -m 30 -H "X-Api-Key: $key" "$SEERR/api/v1/request?take=100&skip=0&filter=all&sort=added") \
  || { echo "Seerr: API error" >&2; exit 1; }
unset key
rc=0

# Approved (2), failed (4) or completed (5), with tags and a Radarr/Sonarr id: "<request id> <type> <title id> <tag ids,>".
while read -r rid type tid tags; do
  grep -qx "$rid" "$STATE" && continue
  case $type in
    movie) app=radarr; config=${RT_RADARR_CONFIG:-/srv/appdata/radarr/config.xml}; kind=movie; ids=movieIds; ev=(radarr_eventtype=MovieAdded "radarr_movie_id=$tid"); search="{\"name\":\"MoviesSearch\",\"movieIds\":[$tid]}";;
    tv) app=sonarr; config=${RT_SONARR_CONFIG:-/srv/appdata/sonarr/config.xml}; kind=series; ids=seriesIds; ev=(sonarr_eventtype=SeriesAdd "sonarr_series_id=$tid"); search="{\"name\":\"SeriesSearch\",\"seriesId\":$tid}";;
    *) continue;;
  esac
  xml() { sed -n "s:.*<$1>\(.*\)</$1>.*:\1:p" "$config"; }
  akey=$(xml ApiKey)
  url="http://localhost:$(xml Port)$(xml UrlBase)/api/v3"
  api() { local path=$1; shift; curl -sf -m 30 -H "X-Api-Key: $akey" -H 'Content-Type: application/json' "$url$path" "$@"; }
  code=$(curl -s -m 30 -o /dev/null -w '%{http_code}' -H "X-Api-Key: $akey" "$url/$kind/$tid")
  if [ "$code" = 404 ]; then echo "$rid" >> "$STATE"; continue; fi
  title=$(api "/$kind/$tid") || { echo "$app: request $rid: API error" >&2; rc=1; continue; }
  missing=$(jq -c --argjson want "[$tags]" '$want - (.tags // [])' <<<"$title")
  if [ "$missing" != "[]" ]; then
    api "/$kind/editor" -X PUT -d "{\"$ids\":[$tid],\"tags\":$missing,\"applyTags\":\"add\"}" >/dev/null \
      || { echo "$app: request $rid: adding tags failed" >&2; rc=1; continue; }
    env "${ev[@]}" LP_TAGS_ONLY=1 LP_CONFIG="$config" LP_LANGUAGES="$HERE/languages.tsv" LP_BAZARR_URL="${RT_BAZARR_URL:-http://127.0.0.1:6767}" \
      LP_BAZARR_KEY="${RT_BAZARR_KEY:-$HERE/../secrets/hook/bazarr.apikey}" bash "$HOOK" >&2 || rc=1
    if [ "$SEARCH" = 1 ]; then
      api /command -X POST -d "$search" >/dev/null || { echo "$app: request $rid: search failed" >&2; rc=1; }
      done_how=searched
    else
      done_how="not searched (RT_SEARCH=0)"
    fi
    echo "$app: request $rid, $(jq -r .title <<<"$title"): tags $(jq -r 'join(",")' <<<"$missing") added, $done_how"
  fi
  echo "$rid" >> "$STATE"
done < <(jq -r '.results[] | select((.status == 2 or .status == 4 or .status == 5) and ((.tags // []) | length > 0) and .media.externalServiceId != null)
  | "\(.id) \(.type) \(.media.externalServiceId) \(.tags | join(","))"' <<<"$requests")
config=${RT_RADARR_CONFIG:-/srv/appdata/radarr/config.xml}
akey=$(sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' "$config")
url="http://localhost:$(sed -n 's:.*<Port>\(.*\)</Port>.*:\1:p' "$config")$(sed -n 's:.*<UrlBase>\(.*\)</UrlBase>.*:\1:p' "$config")/api/v3"
profile=$(curl -sf -m 30 -H "X-Api-Key: $akey" "$url/qualityprofile" | jq -c '.[] | select(.name == "Swiss German (Mundart) only - any quality")') \
  || { echo "radarr: API error (profiles)" >&2; exit 1; }
if [ -n "$profile" ] && [ "$(jq -r .language.name <<<"$profile")" != Any ]; then
  jq -c '.language = {id: -1, name: "Any"}' <<<"$profile" \
    | curl -sf -m 30 -H "X-Api-Key: $akey" -H 'Content-Type: application/json' -X PUT "$url/qualityprofile/$(jq .id <<<"$profile")" -d @- >/dev/null \
    && echo "radarr: profile $(jq .id <<<"$profile") language $(jq -r .language.name <<<"$profile") -> Any" || rc=1
fi
exit $rc
