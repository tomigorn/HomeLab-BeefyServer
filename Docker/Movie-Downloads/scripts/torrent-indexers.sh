#!/usr/bin/env bash
# Adds or updates the FlareSolverr indexer proxy and the torrent indexers (1337x, The Pirate Bay, EZTV, Knaben) in Prowlarr; Prowlarr syncs them to Radarr and Sonarr. Idempotent.
#   ~/Projects/Docker/Movie-Downloads/scripts/torrent-indexers.sh
# Priority 40 (usenet 25-30, README "Indexers"), minimum seeders 1 per indexer (synced to Radarr/Sonarr as minimumSeeders). 1337x and EZTV (both Cloudflare-protected) go through the proxy via the tag "flaresolverr".
set -euo pipefail

H=${H:-127.0.0.1}
PK=$(grep -oP '(?<=<ApiKey>)[^<]+' /srv/appdata/prowlarr/config.xml)
TAG=flaresolverr
PRIORITY=40
MINSEEDERS=1

p() { local path=$1; shift; curl -sf -H "X-Api-Key: $PK" -H 'Content-Type: application/json' "http://$H:9696/api/v1$path" "$@"; }
j() { python3 -c "$1"; }

tag_id=$(p /tag | TAG=$TAG j 'import json,os,sys; print(next((t["id"] for t in json.load(sys.stdin) if t["label"]==os.environ["TAG"]), ""))')
[ -n "$tag_id" ] || tag_id=$(p /tag -X POST -d "{\"label\":\"$TAG\"}" | j 'import json,sys; print(json.load(sys.stdin)["id"])')

proxy=$( { p /indexerProxy/schema; echo; p /indexerProxy; } | TAG_ID=$tag_id j '
import json, os, sys
raw, dec = sys.stdin.read(), json.JSONDecoder()
schema, end = dec.raw_decode(raw)
existing, _ = dec.raw_decode(raw[end:].lstrip())
px = next((i for i in existing if i["implementation"] == "FlareSolverr"), None) or next(i for i in schema if i["implementation"] == "FlareSolverr")
for f in px["fields"]:
    if f["name"] == "host":
        f["value"] = "http://localhost:8191/"
px.update(name="FlareSolverr", tags=[int(os.environ["TAG_ID"])])
px.pop("presets", None)
print(json.dumps(px))
')
proxy_id=$(p /indexerProxy | j 'import json,sys; print(next((i["id"] for i in json.load(sys.stdin) if i["implementation"]=="FlareSolverr"), ""))')
if [ -n "$proxy_id" ]; then p "/indexerProxy/$proxy_id" -X PUT -d @- <<<"$proxy" >/dev/null; else p /indexerProxy -X POST -d @- <<<"$proxy" >/dev/null; fi
echo "FlareSolverr proxy ready, tag $TAG ($tag_id)"

profile_id=$(p /appprofile | j 'import json,sys; print(next(a["id"] for a in json.load(sys.stdin) if a["name"]=="Standard"))')

# name|tagged
for spec in "1337x|1" "The Pirate Bay|0" "EZTV|1" "Knaben|0"; do
  name=${spec%|*}; tagged=${spec#*|}
  body=$( { p /indexer/schema; echo; p /indexer; } | NAME=$name TAGGED=$tagged TAG_ID=$tag_id PROFILE_ID=$profile_id PRIORITY=$PRIORITY MINSEEDERS=$MINSEEDERS j '
import json, os, sys
raw, dec = sys.stdin.read(), json.JSONDecoder()
schema, end = dec.raw_decode(raw)
existing, _ = dec.raw_decode(raw[end:].lstrip())
n = os.environ["NAME"]
ix = next((i for i in existing if i["name"] == n), None) or next(i for i in schema if i["name"] == n and i["protocol"] == "torrent")
for f in ix["fields"]:
    if f["name"] == "torrentBaseSettings.appMinimumSeeders":
        f["value"] = int(os.environ["MINSEEDERS"])
ix.update(enable=True, priority=int(os.environ["PRIORITY"]), appProfileId=int(os.environ["PROFILE_ID"]), tags=[int(os.environ["TAG_ID"])] if os.environ["TAGGED"] == "1" else [])
ix.pop("presets", None)
print(json.dumps(ix))
')
  id=$(p /indexer | NAME=$name j 'import json,os,sys; print(next((i["id"] for i in json.load(sys.stdin) if i["name"]==os.environ["NAME"]), ""))')
  if [ -n "$id" ]; then
    p "/indexer/$id" -X PUT -d @- <<<"$body" >/dev/null || { echo "$name: Prowlarr rejected the indexer (connection test failed), nothing changed" >&2; continue; }
  else
    id=$(p /indexer -X POST -d @- <<<"$body" | j 'import json,sys; print(json.load(sys.stdin)["id"])') || { echo "$name: Prowlarr rejected the indexer (connection test failed), nothing added" >&2; continue; }
  fi
  echo "$name: Prowlarr indexer $id, priority $PRIORITY, min seeders $MINSEEDERS"
done
p /command -X POST -d '{"name":"ApplicationIndexerSync"}' >/dev/null
