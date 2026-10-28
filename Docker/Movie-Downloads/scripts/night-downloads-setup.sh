#!/usr/bin/env bash
# Wires "Can wait: downloads at night" (README) through the APIs. Idempotent.
# Radarr + Sonarr: tag download-now; the existing qBittorrent/SABnzbd clients get that tag (start at once); untagged "night" twins add paused in their own categories. qBittorrent + SABnzbd: the night categories, same folders as the normal ones.
#   ~/Projects/Docker/Movie-Downloads/scripts/night-downloads-setup.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SK=$(tr -d '[:space:]' < "$HERE/../secrets/hook/sabnzbd.apikey")
sab() { curl -sf -G "http://127.0.0.1:8081/api" --data-urlencode "apikey=$SK" -d output=json "$@"; }
QB=http://127.0.0.1:8080
QBIT_PASS_FILE=${QBIT_PASS_FILE:-$HERE/../secrets/qbit-webui.password}
# shellcheck source=qbit-api.sh
. "$HERE/qbit-api.sh"
trap qbit_logout EXIT

# qBittorrent categories: create, or fix the save path of an existing one.
cats=$(qbit_api "$QB" /torrents/categories)
for c in radarr-night:/data/torrents/movies sonarr-night:/data/torrents/tv; do
  name=${c%%:*}; path=${c#*:}
  if jq -e --arg n "$name" 'has($n)' <<<"$cats" >/dev/null; then ep=editCategory; else ep=createCategory; fi
  qbit_api "$QB" "/torrents/$ep" --data-urlencode "category=$name" --data-urlencode "savePath=$path" >/dev/null
done
qbit_api "$QB" /torrents/categories | jq -r 'to_entries[] | select(.key | endswith("-night")) | "qbit category \(.key) -> \(.value.savePath)"'

# SABnzbd categories: same folder as movies / tv, script and post-processing inherited from "*" (ClamAV hook).
for c in movies-night:movies tv-night:tv; do
  sab -d mode=set_config -d section=categories --data-urlencode "name=${c%%:*}" --data-urlencode "dir=${c#*:}" -d priority=-100 -d pp= -d script=Default >/dev/null
done
sab -d mode=get_config -d section=categories | jq -r '.config.categories[] | select(.name | endswith("-night")) | "sab category \(.name) -> \(.dir), priority \(.priority), script \(.script)"'

for app in radarr sonarr; do
  config=/srv/appdata/$app/config.xml
  key=$(sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' "$config"); port=$(sed -n 's:.*<Port>\(.*\)</Port>.*:\1:p' "$config")
  api() { local path=$1; shift; curl -sf -m 60 -H "X-Api-Key: $key" -H 'Content-Type: application/json' "http://localhost:$port/api/v3$path" "$@"; }
  case $app in radarr) catf=movieCategory; recent=recentMoviePriority; older=olderMoviePriority;; sonarr) catf=tvCategory; recent=recentTvPriority; older=olderTvPriority;; esac

  tag=$(api /tag | jq '[.[] | select(.label == "download-now") | .id][0] // empty')
  [ -n "$tag" ] || tag=$(api /tag -X POST -d '{"label":"download-now"}' | jq .id)

  clients=$(api /downloadclient)
  for impl in QBittorrent Sabnzbd; do
    now=$(jq -c --arg i "$impl" --arg f "$catf" '[.[] | select(.implementation == $i and ((.fields[] | select(.name == $f) | .value) // "" | endswith("-night") | not))][0]' <<<"$clients")
    [ "$now" != null ] || { echo "$app: no $impl client to copy" >&2; exit 1; }
    upd=$(jq -c --argjson t "$tag" '.tags = ((.tags // []) + [$t] | unique)' <<<"$now")
    [ "$upd" = "$now" ] || api "/downloadclient/$(jq .id <<<"$now")?forceSave=true" -X PUT -d "$upd" >/dev/null
    night=$(jq -c --arg i "$impl" --arg f "$catf" '[.[] | select(.implementation == $i and ((.fields[] | select(.name == $f) | .value) // "" | endswith("-night")))][0]' <<<"$clients")
    # GET masks the SABnzbd key; a new client needs the real one.
    want=$(jq -c --arg f "$catf" --arg r "$recent" --arg o "$older" --arg i "$impl" --arg sk "$SK" '
      del(.id) | .name = (.name + " night") | .tags = []
      | .fields |= map(if .name == $f then .value = (.value + "-night")
                       elif $i == "QBittorrent" and .name == "initialState" then .value = 2
                       elif $i == "Sabnzbd" and (.name == $r or .name == $o) then .value = -2
                       elif $i == "Sabnzbd" and .name == "apiKey" then .value = $sk
                       else . end)' <<<"$now")
    if [ "$night" = null ]; then
      api "/downloadclient?forceSave=true" -X POST -d "$want" >/dev/null
    else
      api "/downloadclient/$(jq .id <<<"$night")?forceSave=true" -X PUT -d "$(jq -c --argjson id "$(jq .id <<<"$night")" '.id = $id' <<<"$want")" >/dev/null
    fi
  done
  api /downloadclient | jq -r --arg f "$catf" --arg app "$app" '.[] | "\($app): \(.name) tags \(.tags) category \(.fields[] | select(.name == $f) | .value) \([.fields[] | select(.name | test("initialState|Priority$")) | "\(.name)=\(.value)"] | join(" "))"'
done
