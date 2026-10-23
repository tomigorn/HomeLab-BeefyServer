#!/usr/bin/env bash
# Radarr/Sonarr Connect "Custom Script" (On Grab, On Import, On Upgrade, On Movie/Series Delete): remembers each title's downloads, and when the title is deleted WITH its files (Seerr "Remove from Radarr/Sonarr") removes its torrents + files from qBittorrent, its SABnzbd jobs and its Seerr record, so a new request starts fresh (README "Delete a title").
# Runs inside the app container (bash, curl, jq, flock); registered by scripts/language-policy.sh. `title-wipe-hook.sh seed <radarr|sonarr>` fills the state from the app's grab history (titles grabbed before the hook existed).
set -euo pipefail

CONFIG=${TW_CONFIG:-/config/config.xml}
STATE=${TW_STATE:-/config/title-wipe.tsv}
QBIT=${TW_QBIT_URL:-http://gluetun:8080}
SAB=${TW_SAB_URL:-http://gluetun:8081}
SAB_KEY=${TW_SAB_KEY:-/secrets/sabnzbd.apikey}
SEERR=${TW_SEERR_URL:-http://seerr:5055}
SEERR_KEY=${TW_SEERR_KEY:-/secrets/seerr.apikey}
SEERR_WAIT=${TW_SEERR_WAIT:-60}
LOG=${TW_LOG:-/config/logs/title-wipe.txt}

if [ "${1:-}" = seed ]; then
  app=$2; event=seed
elif [ -n "${radarr_eventtype:-}" ]; then
  app=radarr; event=$radarr_eventtype; key=${radarr_movie_tmdbid:-}; dlid=${radarr_download_id:-}; client=${radarr_download_client_type:-${radarr_download_client:-}}
  title=${radarr_movie_title:-}; year=${radarr_movie_year:-0}; deleted=${radarr_movie_deletedfiles:-}
elif [ -n "${sonarr_eventtype:-}" ]; then
  app=sonarr; event=$sonarr_eventtype; key=${sonarr_series_tvdbid:-}; dlid=${sonarr_download_id:-}; client=${sonarr_download_client_type:-${sonarr_download_client:-}}
  title=${sonarr_series_title:-}; year=${sonarr_series_year:-0}; deleted=${sonarr_series_deletedfiles:-}
else
  echo "no radarr_eventtype/sonarr_eventtype: not called by Radarr or Sonarr" >&2; exit 1
fi
case $app in
  radarr) kind=movie; DELETE=MovieDelete; catfield=movieCategory; idpath=.movie.tmdbId; include=includeMovie; mtype=movie; mid=tmdbId;;
  sonarr) kind=series; DELETE=SeriesDelete; catfield=tvCategory; idpath=.series.tvdbId; include=includeSeries; mtype=tv; mid=tvdbId;;
  *) echo "seed: app must be radarr or sonarr" >&2; exit 1;;
esac

xml() { sed -n "s:.*<$1>\(.*\)</$1>.*:\1:p" "$CONFIG"; }
api() { local path=$1; shift; curl -sf -m 10 -H "X-Api-Key: $(xml ApiKey)" "http://localhost:$(xml Port)$(xml UrlBase)/api/v3$path" "$@"; }

client_of() { # client_of <client name or type> <download id>: qbit | sab
  case ${1,,} in
    *qbit*|*torrent*) echo qbit;;
    *sab*) echo sab;;
    *) if [[ $2 =~ ^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$ ]]; then echo qbit; else echo sab; fi;;
  esac
}
record() { # record <id> <client> <download id>
  local line
  [ "$2" = qbit ] && line="$1	qbit	${3,,}" || line="$1	sab	$3"
  ( flock 9; grep -qxF "$line" "$STATE" 2>/dev/null || echo "$line" >> "$STATE" ) 9>>"$STATE.lock"
}

if [ "$event" = seed ]; then
  page=1; n=0
  while :; do
    recs=$(api "/history?eventType=1&$include=true&page=$page&pageSize=250")
    while IFS=$'\t' read -r id c d; do
      record "$id" "$(client_of "$c" "$d")" "$d"; n=$((n + 1))
    done < <(jq -r --arg p "$idpath" '.records[] | select((.downloadId // "") != "" and getpath($p | ltrimstr(".") | split(".")) != null) | [getpath($p | ltrimstr(".") | split(".")), (.data.downloadClient // .data.downloadClientName // ""), .downloadId] | @tsv' <<<"$recs")
    [ $((page * 250)) -lt "$(jq -r .totalRecords <<<"$recs")" ] || break
    page=$((page + 1))
  done
  echo "$app: $n grabs seeded into $STATE"
  exit 0
fi

case $event in
  Grab|Download)
    [ -n "$dlid" ] && [ -n "$key" ] || exit 0
    record "$key" "$(client_of "$client" "$dlid")" "$dlid"
    exit 0;;
  "$DELETE") ;;
  *) exit 0;;
esac

if [ "${deleted,,}" != true ]; then
  echo "$kind $key: deleted without files, nothing wiped"; exit 0
fi
[ -n "$key" ] && [ "$key" != 0 ] || { echo "$kind: no id, nothing wiped" >&2; exit 1; }
failed=0

norm='ascii_downcase | explode | map(select(. != 39 and . != 8217)) | implode | gsub("[^a-z0-9]+"; " ") | ltrimstr(" ") | rtrimstr(" ")'
want=$(jq -rn --arg t "$title" "\$t | $norm")
# Name match for titles without recorded downloads: movie = "<title> <year> …" exactly once; series = "<title> S01…/<year>/Season/Complete …".
name_match() { # name_match <jq path of the name>: reads [{…}], prints the matching items
  jq -c --arg w "$want" --arg y "$year" --arg k "$kind" "
    def n: $norm;
    [ .[] | . as \$i | (getpath(\$ARGS.positional[0] | split(\".\")) | n + \" \") as \$x
      | select(if \$k == \"movie\" then (\$x | startswith(\$w + \" \" + \$y + \" \"))
               else (\$x | startswith(\$w + \" \")) and (\$x | ltrimstr(\$w + \" \") | test(\"^(s[0-9]{1,2}(e[0-9]+)*|season [0-9]+|[0-9]{4}|complete) \"))
               end) ]" --args "$1"
}
same_title_left=1
if [ -n "$want" ] && library=$(api "/$kind"); then
  same_title_left=$(jq --arg w "$want" --arg y "$year" --arg k "$kind" "[ .[] | select((.title | $norm) == \$w and (\$k == \"series\" or (.year | tostring) == \$y)) ] | length" <<<"$library")
fi
fallback_ok() { # fallback_ok <what> <matches json>
  local count; count=$(jq length <<<"$2")
  [ -n "$want" ] || { echo "$kind $key: no title, $1 by name skipped"; return 1; }
  [ "$count" -gt 0 ] || { echo "$kind $key: no $1 recorded or named like '$title ($year)'"; return 1; }
  if [ "$same_title_left" -gt 0 ]; then echo "$kind $key: another title is named '$title', $1 by name skipped (ambiguous)"; return 1; fi
  if [ "$kind" = movie ] && [ "$count" -gt 1 ]; then echo "$kind $key: $count $1 named like '$title ($year)', skipped (ambiguous)"; return 1; fi
}

clients=$(api /downloadclient) || { echo "$kind $key: download clients not readable" >&2; clients='[]'; failed=1; }
cats() { jq -c --arg i "$1" --arg f "$catfield" '[.[] | select(.implementation == $i) | .fields[] | select(.name == $f) | .value | select(. != null)]' <<<"$clients"; }
mine() { awk -F'\t' -v k="$key" -v c="$1" '$1 == k && $2 == c {print $3}' "$STATE"; }
recorded=$(cut -f1 "$STATE" 2>/dev/null | grep -cx "$key" || true)

# qBittorrent: recorded hashes (or the name match) in the app's own category only, torrent + its files.
qcats=$(cats QBittorrent)
if torrents=$(curl -sf -m 10 "$QBIT/api/v2/torrents/info"); then
  torrents=$(jq -c --argjson c "$qcats" '[.[] | select(.category as $x | $c | index($x))]' <<<"$torrents")
  if [ "$recorded" -gt 0 ]; then
    hashes=$(mine qbit | jq -R . | jq -sc .)
    hit=$(jq -c --argjson h "$hashes" '[.[] | select(.hash | ascii_downcase | IN($h[]))]' <<<"$torrents")
  else
    hit=$(name_match name <<<"$torrents")
    fallback_ok torrents "$hit" || hit='[]'
  fi
  if [ "$(jq length <<<"$hit")" -gt 0 ]; then
    if curl -sf -m 10 -d "hashes=$(jq -r 'map(.hash) | join("|")' <<<"$hit")&deleteFiles=true" "$QBIT/api/v2/torrents/delete" >/dev/null; then
      jq -r --arg p "$kind $key" '.[] | "\($p): torrent removed with files: \(.name)"' <<<"$hit"
    else
      echo "$kind $key: qBittorrent delete failed" >&2; failed=1
    fi
  fi
else
  echo "$kind $key: qBittorrent not reachable at $QBIT" >&2; failed=1
fi

# SABnzbd: the title's jobs from the queue and the history (del_files=1 also drops leftovers of failed jobs).
scats=$(cats Sabnzbd)
if [ -r "$SAB_KEY" ]; then
  sk=$(tr -d '[:space:]' < "$SAB_KEY")
  sab() { curl -sf -m 10 -G "$SAB/api" --data-urlencode "apikey=$sk" "$@"; }
  for mode in queue history; do
    if ! slots=$(sab -d mode=$mode -d output=json -d limit=1000); then echo "$kind $key: SABnzbd $mode not readable" >&2; failed=1; continue; fi
    slots=$(jq -c --argjson c "$scats" --arg m "$mode" '[.[$m].slots // [] | .[] | select((.cat // .category) as $x | $c | index($x)) | . + {title: (.filename // .name)}]' <<<"$slots")
    if [ "$recorded" -gt 0 ]; then
      hit=$(jq -c --argjson n "$(mine sab | jq -R . | jq -sc .)" '[.[] | select(.nzo_id | IN($n[]))]' <<<"$slots")
    else
      hit=$(name_match title <<<"$slots")
      fallback_ok "SABnzbd $mode entries" "$hit" || hit='[]'
    fi
    for nzo in $(jq -r '.[].nzo_id' <<<"$hit"); do
      if sab -d mode=$mode -d name=delete -d "value=$nzo" -d del_files=1 >/dev/null; then echo "$kind $key: SABnzbd $mode entry $nzo removed"; else echo "$kind $key: SABnzbd delete of $nzo failed" >&2; failed=1; fi
    done
  done
else
  echo "$kind $key: no SABnzbd API key at $SAB_KEY" >&2; failed=1
fi

if [ "$recorded" -gt 0 ]; then
  ( flock 9; awk -F'\t' -v k="$key" '$1 != k' "$STATE" > "$STATE.new"; cat "$STATE.new" > "$STATE"; rm -f "$STATE.new" ) 9>>"$STATE.lock"
fi

# Seerr: what its "Clear Data" does. Its own "Remove from Radarr" saves the record (status deleted, 7) after Radarr answers, so wait for that and run detached, or the save would bring the record back.
seerr_wipe() {
  local sk media found id status waited=0
  [ -r "$SEERR_KEY" ] || { echo "$kind $key: no Seerr API key at $SEERR_KEY" >&2; return 1; }
  sk=$(tr -d '[:space:]' < "$SEERR_KEY")
  find_media() {
    local skip=0 page
    while :; do
      page=$(curl -sf -m 10 -H "X-Api-Key: $sk" "$SEERR/api/v1/media?take=100&skip=$skip") || return 1
      found=$(jq -c --arg t "$mtype" --arg f "$mid" --argjson k "$key" '[.results[] | select(.mediaType == $t and .[$f] == $k)][0] // empty' <<<"$page")
      [ -z "$found" ] || return 0
      skip=$((skip + 100))
      [ "$skip" -lt "$(jq -r '.pageInfo.results // 0' <<<"$page")" ] || return 0
    done
  }
  find_media || { echo "$kind $key: Seerr not reachable at $SEERR" >&2; return 1; }
  [ -n "$found" ] || { echo "$kind $key: not in Seerr"; return 0; }
  while status=$(jq -r .status <<<"$found"); [ "$status" != 7 ] && [ "$waited" -lt "$SEERR_WAIT" ]; do
    sleep 2; waited=$((waited + 2)); find_media || true
    [ -n "$found" ] || { echo "$kind $key: gone from Seerr"; return 0; }
  done
  id=$(jq -r .id <<<"$found")
  if curl -sf -m 10 -X DELETE -H "X-Api-Key: $sk" "$SEERR/api/v1/media/$id" >/dev/null; then
    echo "$kind $key: Seerr media $id removed"
  else
    echo "$kind $key: Seerr delete of media $id failed" >&2; return 1
  fi
}
if [ "${TW_FOREGROUND:-}" = 1 ]; then
  seerr_wipe || failed=1
else
  mkdir -p "$(dirname "$LOG")"
  ( seerr_wipe 2>&1 | while IFS= read -r l; do echo "$(date '+%F %T') $l"; done >> "$LOG" ) </dev/null >/dev/null 2>&1 &
  echo "$kind $key: Seerr record is cleared in the background (log $LOG)"
fi
exit $failed
