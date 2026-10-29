#!/usr/bin/env bash
# Wires "Can wait: downloads at night" (README) through the APIs. Idempotent.
# Radarr + Sonarr: tag download-now; the existing qBittorrent/SABnzbd clients get that tag (start at once); untagged "night" twins add paused in their own categories. qBittorrent + SABnzbd: the night categories, same folders as the normal ones.
#   ~/Projects/Docker/Movie-Downloads/scripts/night-downloads-setup.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SK=$(tr -d '[:space:]' < "$HERE/../secrets/hook/sabnzbd.apikey")
sab() { curl -sf -G "http://127.0.0.1:8081/api" --data-urlencode apikey@<(printf %s "$SK") -d output=json "$@"; }
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
  api() { local path=$1; shift; curl -sf -m 60 -H @<(printf 'X-Api-Key: %s\n' "$key") -H 'Content-Type: application/json' "http://localhost:$port/api/v3$path" "$@"; }
  # Writes that carry the qBittorrent password read the body from stdin, never argv.
  apin() { local path=$1; shift; curl -sf -m 60 -H @<(printf 'X-Api-Key: %s\n' "$key") -H 'Content-Type: application/json' "http://localhost:$port/api/v3$path" --data-binary @- "$@"; }
  case $app in radarr) catf=movieCategory; recent=recentMoviePriority; older=olderMoviePriority;; sonarr) catf=tvCategory; recent=recentTvPriority; older=olderTvPriority;; esac

  tag=$(api /tag | jq '[.[] | select(.label == "download-now") | .id][0] // empty')
  [ -n "$tag" ] || tag=$(api /tag -X POST -d '{"label":"download-now"}' | jq .id)

  clients=$(api /downloadclient)
  for impl in QBittorrent Sabnzbd; do
    now=$(jq -c --arg i "$impl" --arg f "$catf" '[.[] | select(.implementation == $i and ((.fields[] | select(.name == $f) | .value) // "" | endswith("-night") | not))][0]' <<<"$clients")
    [ "$now" != null ] || { echo "$app: no $impl client to copy" >&2; exit 1; }
    # qBittorrent has a WebUI login: username + password from the secret file (GET masks the password, so always written).
    cred='.'; [ "$impl" != QBittorrent ] || cred='.fields |= map(if .name == "username" then .value = "admin" elif .name == "password" then .value = ($qp | rtrimstr("\n")) else . end)'
    upd=$(jq -c --rawfile qp "$QBIT_PASS_FILE" --argjson t "$tag" '.tags = ((.tags // []) + [$t] | unique)' <<<"$now" | jq -c --rawfile qp "$QBIT_PASS_FILE" "$cred")
    if [ "$impl" = QBittorrent ]; then apin "/downloadclient/$(jq .id <<<"$now")?forceSave=true" -X PUT <<<"$upd" >/dev/null
    else [ "$upd" = "$now" ] || api "/downloadclient/$(jq .id <<<"$now")?forceSave=true" -X PUT -d "$upd" >/dev/null; fi
    night=$(jq -c --arg i "$impl" --arg f "$catf" '[.[] | select(.implementation == $i and ((.fields[] | select(.name == $f) | .value) // "" | endswith("-night")))][0]' <<<"$clients")
    # GET masks the SABnzbd key; a new client needs the real one.
    want=$(jq -c --rawfile qp "$QBIT_PASS_FILE" --arg f "$catf" --arg r "$recent" --arg o "$older" --arg i "$impl" --arg sk "$SK" '
      del(.id) | .name = (.name + " night") | .tags = []
      | .fields |= map(if .name == $f then .value = (.value + "-night")
                       elif $i == "QBittorrent" and .name == "initialState" then .value = 2
                       elif $i == "Sabnzbd" and (.name == $r or .name == $o) then .value = -2
                       elif $i == "Sabnzbd" and .name == "apiKey" then .value = $sk
                       elif $i == "QBittorrent" and .name == "username" then .value = "admin"
                       elif $i == "QBittorrent" and .name == "password" then .value = ($qp | rtrimstr("\n"))
                       else . end)' <<<"$now")
    if [ "$night" = null ]; then
      apin "/downloadclient?forceSave=true" -X POST <<<"$want" >/dev/null
    else
      jq -c --argjson id "$(jq .id <<<"$night")" '.id = $id' <<<"$want" | apin "/downloadclient/$(jq .id <<<"$night")?forceSave=true" -X PUT >/dev/null
    fi
  done
  api /downloadclient | jq -r --arg f "$catf" --arg app "$app" '.[] | "\($app): \(.name) tags \(.tags) category \(.fields[] | select(.name == $f) | .value) \([.fields[] | select(.name | test("initialState|Priority$")) | "\(.name)=\(.value)"] | join(" "))"'
done
