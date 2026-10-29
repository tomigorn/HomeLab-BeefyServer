#!/usr/bin/env bash
# Radarr/Sonarr Connect "Custom Script" (On Grab, On Import, On Upgrade): remembers each title's downloads (README "Delete a title"). Deleting a title in Radarr/Sonarr wipes nothing here.
# `title-wipe-hook.sh wipe <radarr|sonarr> <tmdbId|tvdbId> [title] [year]` (run by clear-data-wipe.sh after Seerr "Clear Data"): deletes the title in Radarr/Sonarr with its files, its torrents + files in qBittorrent, its SABnzbd jobs and its Jellyfin item.
# `title-wipe-hook.sh seed <radarr|sonarr>` fills the state from the app's grab history (titles grabbed before the hook existed). Needs bash, curl, jq, flock.
set -euo pipefail

CONFIG=${TW_CONFIG:-/config/config.xml}
STATE=${TW_STATE:-/config/title-wipe.tsv}
QBIT=${TW_QBIT_URL:-http://gluetun:8080}
QBIT_PASS=${TW_QBIT_PASS:-}
SAB=${TW_SAB_URL:-http://gluetun:8081}
SAB_KEY=${TW_SAB_KEY:-/secrets/sabnzbd.apikey}
JF=${TW_JF_URL:-http://127.0.0.1:8096}
JF_KEY=${TW_JF_KEY:-/secrets/jellyfin.apikey}
JF_WAIT=${TW_JF_WAIT:-30}

if [ "${1:-}" = seed ] || [ "${1:-}" = wipe ]; then
  event=$1; app=${2:-}; key=${3:-}; title=${4:-}; year=${5:-0}
elif [ -n "${radarr_eventtype:-}" ]; then
  app=radarr; event=$radarr_eventtype; key=${radarr_movie_tmdbid:-}; dlid=${radarr_download_id:-}; client=${radarr_download_client_type:-${radarr_download_client:-}}
elif [ -n "${sonarr_eventtype:-}" ]; then
  app=sonarr; event=$sonarr_eventtype; key=${sonarr_series_tvdbid:-}; dlid=${sonarr_download_id:-}; client=${sonarr_download_client_type:-${sonarr_download_client:-}}
else
  echo "no radarr_eventtype/sonarr_eventtype: not called by Radarr or Sonarr" >&2; exit 1
fi
case $app in
  radarr) kind=movie; catfield=movieCategory; idpath=.movie.tmdbId; include=includeMovie; idf=tmdbId; excl=addImportExclusion; jtype=Movie; jid=Tmdb; jroot=${TW_JF_ROOT:-/media/movies/};;
  sonarr) kind=series; catfield=tvCategory; idpath=.series.tvdbId; include=includeSeries; idf=tvdbId; excl=addImportListExclusion; jtype=Series; jid=Tvdb; jroot=${TW_JF_ROOT:-/media/tv/};;
  *) echo "$event: app must be radarr or sonarr" >&2; exit 1;;
esac

xml() { sed -n "s:.*<$1>\(.*\)</$1>.*:\1:p" "$CONFIG"; }
api() { local path=$1; shift; curl -sf -m 30 -H @<(printf 'X-Api-Key: %s\n' "$(xml ApiKey)") "http://localhost:$(xml Port)$(xml UrlBase)/api/v3$path" "$@"; }

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

if [ "$event" != wipe ]; then
  case $event in
    Grab|Download) [ -n "$dlid" ] && [ -n "$key" ] && record "$key" "$(client_of "$client" "$dlid")" "$dlid";;
  esac
  exit 0
fi

[[ $key =~ ^[1-9][0-9]*$ ]] || { echo "wipe: $app needs a numeric $idf" >&2; exit 1; }
failed=0

# Radarr/Sonarr: the title (exact id) with its files; Seerr "Remove from Radarr/Sonarr" may have done this already.
found=$(api "/$kind?$idf=$key") || { echo "$kind $key: $app not reachable, nothing wiped" >&2; exit 1; }
found=$(jq -c --arg f "$idf" --argjson k "$key" '[.[] | select(.[$f] == $k)]' <<<"$found")
if [ "$(jq length <<<"$found")" -gt 0 ]; then
  title=$(jq -r '.[0].title' <<<"$found"); year=$(jq -r '.[0].year // 0' <<<"$found")
  for id in $(jq -r '.[].id' <<<"$found"); do
    if api "/$kind/$id?deleteFiles=true&$excl=false" -X DELETE >/dev/null; then echo "$kind $key: $title removed from $app with its files"; else echo "$kind $key: $app delete of $id failed" >&2; failed=1; fi
  done
else
  echo "$kind $key: not in $app (any more)"
fi

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
  same_title_left=$(jq --arg w "$want" --arg y "$year" --arg k "$kind" --arg f "$idf" --argjson key "$key" "[ .[] | select(.[\$f] != \$key and (.title | $norm) == \$w and (\$k == \"series\" or (.year | tostring) == \$y)) ] | length" <<<"$library")
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
QBIT_PASS_FILE=$QBIT_PASS QBIT_JAR=
# shellcheck source=qbit-api.sh
. "$(dirname "$0")/qbit-api.sh"
if torrents=$(qbit_api "$QBIT" /torrents/info); then
  torrents=$(jq -c --argjson c "$qcats" '[.[] | select(.category as $x | $c | index($x))]' <<<"$torrents")
  if [ "$recorded" -gt 0 ]; then
    hashes=$(mine qbit | jq -R . | jq -sc .)
    hit=$(jq -c --argjson h "$hashes" '[.[] | select(.hash | ascii_downcase | IN($h[]))]' <<<"$torrents")
  else
    hit=$(name_match name <<<"$torrents")
    fallback_ok torrents "$hit" || hit='[]'
  fi
  if [ "$(jq length <<<"$hit")" -gt 0 ]; then
    if qbit_api "$QBIT" /torrents/delete -d "hashes=$(jq -r 'map(.hash) | join("|")' <<<"$hit")&deleteFiles=true" >/dev/null; then
      jq -r --arg p "$kind $key" '.[] | "\($p): torrent removed with files: \(.name)"' <<<"$hit"
    else
      echo "$kind $key: qBittorrent delete failed" >&2; failed=1
    fi
  fi
else
  echo "$kind $key: qBittorrent not reachable at $QBIT" >&2; failed=1
fi
qbit_logout

# SABnzbd: the title's jobs from the queue and the history (del_files=1 also drops leftovers of failed jobs).
scats=$(cats Sabnzbd)
if [ -r "$SAB_KEY" ]; then
  sk=$(tr -d '[:space:]' < "$SAB_KEY")
  sab() { curl -sf -m 10 -G "$SAB/api" --data-urlencode apikey@<(printf %s "$sk") "$@"; }
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
  unset sk
else
  echo "$kind $key: no SABnzbd API key at $SAB_KEY" >&2; failed=1
fi

# Jellyfin: the item with exactly this provider id in the app's library folder; Radarr/Sonarr's own Jellyfin notification usually drops it first.
if [ -r "$JF_KEY" ]; then
  jk=$(tr -d '[:space:]' < "$JF_KEY")
  jitems() { curl -sf -m 30 -H @<(printf 'Authorization: MediaBrowser Token="%s"\n' "$jk") "$JF/Items?Recursive=true&IncludeItemTypes=$jtype&Fields=ProviderIds,Path" \
    | jq -c --arg p "$jid" --arg k "$key" --arg r "$jroot" '[.Items[] | select(.ProviderIds[$p] == $k and ((.Path // "") | startswith($r)))]'; }
  waited=0
  while hit=$(jitems) && [ "$(jq length <<<"$hit")" -gt 0 ] && [ "$waited" -lt "$JF_WAIT" ]; do sleep 5; waited=$((waited + 5)); done
  if [ -z "${hit:-}" ]; then
    echo "$kind $key: Jellyfin not reachable at $JF" >&2; failed=1
  else
    for id in $(jq -r '.[].Id' <<<"$hit"); do
      if curl -sf -m 30 -X DELETE -H @<(printf 'Authorization: MediaBrowser Token="%s"\n' "$jk") "$JF/Items/$id" >/dev/null; then echo "$kind $key: Jellyfin item $id removed"; else echo "$kind $key: Jellyfin delete of $id failed" >&2; failed=1; fi
    done
  fi
  unset jk
else
  echo "$kind $key: no Jellyfin API key at $JF_KEY" >&2; failed=1
fi

if [ "$failed" = 0 ] && [ "$recorded" -gt 0 ]; then
  if [ -w "$STATE" ] && [ -w "$(dirname "$STATE")" ]; then
    ( flock 9; awk -F'\t' -v k="$key" '$1 != k' "$STATE" > "$STATE.new"; cat "$STATE.new" > "$STATE"; rm -f "$STATE.new" ) 9>>"$STATE.lock"
  else
    echo "$kind $key: state not pruned, $STATE or its directory not writable as uid $(id -u) (lines stay; harmless)" >&2
  fi
fi
exit $failed
