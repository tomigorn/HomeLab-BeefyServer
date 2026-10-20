#!/usr/bin/env bash
# Weekly "search cutoff unmet", capped so the indexers' API limits hold (README "Upgrades"). Run by systemd/cutoff-search.timer.
# CS_MOVIES / CS_EPISODES: titles per run (default 10 each, 0 = skip); the least recently searched go first.
set -uo pipefail

MOVIES=${CS_MOVIES:-10}
EPISODES=${CS_EPISODES:-10}
rc=0

search() { # search <app> <config.xml> <count> <sortKey> <command> <ids field> <label jq> [extra query]
  local app=$1 config=$2 count=$3 sort=$4 command=$5 field=$6 label=$7 extra=${8:-}
  [ "$count" -gt 0 ] || return 0
  local key url page ids
  key=$(sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' "$config")
  url="http://localhost:$(sed -n 's:.*<Port>\(.*\)</Port>.*:\1:p' "$config")$(sed -n 's:.*<UrlBase>\(.*\)</UrlBase>.*:\1:p' "$config")/api/v3"
  page=$(curl -sf -m 30 -H "X-Api-Key: $key" "$url/wanted/cutoff?page=1&pageSize=$count&monitored=true&sortKey=$sort&sortDirection=ascending$extra") \
    || { echo "$app: API error" >&2; return 1; }
  ids=$(jq -c '[.records[].id]' <<<"$page")
  if [ "$ids" = "[]" ]; then echo "$app: nothing below cutoff"; return 0; fi
  curl -sf -m 30 -H "X-Api-Key: $key" -H 'Content-Type: application/json' -X POST "$url/command" \
    -d "{\"name\":\"$command\",\"$field\":$ids}" >/dev/null || { echo "$app: search command failed" >&2; return 1; }
  echo "$app: $(jq '.records | length' <<<"$page") of $(jq .totalRecords <<<"$page") cutoff-unmet searched: $(jq -r "[.records[] | $label] | join(\", \")" <<<"$page")"
}

search radarr "${CS_RADARR_CONFIG:-/srv/appdata/radarr/config.xml}" "$MOVIES" movies.lastSearchTime MoviesSearch movieIds '.title' || rc=1
search sonarr "${CS_SONARR_CONFIG:-/srv/appdata/sonarr/config.xml}" "$EPISODES" episodes.lastSearchTime EpisodeSearch episodeIds \
  'if .series then "\(.series.title) S\(.seasonNumber)E\(.episodeNumber)" else .title end' '&includeSeries=true' || rc=1
exit $rc
