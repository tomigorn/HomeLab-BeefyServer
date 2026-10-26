#!/usr/bin/env bash
# Adds or updates the FlareSolverr indexer proxy and the torrent indexers (1337x, The Pirate Bay, EZTV, Knaben) in Prowlarr; Prowlarr syncs them to Radarr and Sonarr. Idempotent.
#   ~/Projects/Docker/Movie-Downloads/scripts/torrent-indexers.sh
# Priority 40 (usenet 25-30, README "Indexers"). Each indexer twice: "<name>" with minimum seeders 5 for every title, "<name> (old-rare)" with 1, tagged old-rare in Radarr/Sonarr so only titles with that tag (Any quality profiles) use it.
# Prowlarr's sync overwrites minimumSeeders in the apps but keeps their tags, so seeders live in Prowlarr (appMinimumSeeders) and the tag is set app-side after the sync. 1337x and EZTV (both Cloudflare-protected) go through the proxy via the Prowlarr tag "flaresolverr".
set -euo pipefail

H=${H:-127.0.0.1}
PK=$(grep -oP '(?<=<ApiKey>)[^<]+' /srv/appdata/prowlarr/config.xml)
TAG=flaresolverr
PRIORITY=40
MINSEEDERS=5
RARE_MINSEEDERS=1
RARE_TAG=old-rare
RARE_SUFFIX=" (old-rare)"

p() { local path=$1; shift; curl -sf -H "X-Api-Key: $PK" -H 'Content-Type: application/json' "http://$H:9696/api/v1$path" "$@"; }
j() { python3 -c "$@"; }

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

# definition|tagged|name|min seeders
for spec in "1337x|1" "The Pirate Bay|0" "EZTV|1" "Knaben|0"; do
  for variant in "$spec|${spec%|*}|$MINSEEDERS" "$spec|${spec%|*}$RARE_SUFFIX|$RARE_MINSEEDERS"; do
  IFS='|' read -r def tagged name minseeders <<<"$variant"
  body=$( { p /indexer/schema; echo; p /indexer; } | DEF=$def NAME=$name TAGGED=$tagged TAG_ID=$tag_id PROFILE_ID=$profile_id PRIORITY=$PRIORITY MINSEEDERS=$minseeders j '
import json, os, sys
raw, dec = sys.stdin.read(), json.JSONDecoder()
schema, end = dec.raw_decode(raw)
existing, _ = dec.raw_decode(raw[end:].lstrip())
n = os.environ["NAME"]
ix = next((i for i in existing if i["name"] == n), None) or next(i for i in schema if i["name"] == os.environ["DEF"] and i["protocol"] == "torrent")
for f in ix["fields"]:
    if f["name"] == "torrentBaseSettings.appMinimumSeeders":
        f["value"] = int(os.environ["MINSEEDERS"])
ix.update(name=n, enable=True, priority=int(os.environ["PRIORITY"]), appProfileId=int(os.environ["PROFILE_ID"]), tags=[int(os.environ["TAG_ID"])] if os.environ["TAGGED"] == "1" else [])
ix.pop("presets", None)
print(json.dumps(ix))
')
  id=$(p /indexer | NAME=$name j 'import json,os,sys; print(next((i["id"] for i in json.load(sys.stdin) if i["name"]==os.environ["NAME"]), ""))')
  if [ -n "$id" ]; then
    p "/indexer/$id" -X PUT -d @- <<<"$body" >/dev/null || { echo "$name: Prowlarr rejected the indexer (connection test failed), nothing changed" >&2; continue; }
  else
    id=$(p /indexer -X POST -d @- <<<"$body" | j 'import json,sys; print(json.load(sys.stdin)["id"])') || { echo "$name: Prowlarr rejected the indexer (connection test failed), nothing added" >&2; continue; }
  fi
  echo "$name: Prowlarr indexer $id, priority $PRIORITY, min seeders $minseeders"
  done
done

cid=$(p /command -X POST -d '{"name":"ApplicationIndexerSync","forceSync":true}' | j 'import json,sys; print(json.load(sys.stdin)["id"])')
for _ in $(seq 60); do
  st=$(p "/command/$cid" | j 'import json,sys; print(json.load(sys.stdin)["status"])')
  case $st in completed) break;; failed|aborted) echo "Prowlarr app sync $st" >&2; exit 1;; esac
  sleep 2
done
[ "$st" = completed ] || { echo "Prowlarr app sync still $st after 120 s, app tags not set" >&2; exit 1; }

# old-rare tag on the "(old-rare)" copies in Radarr and Sonarr, off every other indexer
for app in radarr:7878 sonarr:8989; do
  a=${app%%:*}; port=${app#*:}
  K=$(grep -oP '(?<=<ApiKey>)[^<]+' "/srv/appdata/$a/config.xml")
  q() { local path=$1; shift; curl -sf -H "X-Api-Key: $K" -H 'Content-Type: application/json' "http://$H:$port/api/v3$path" "$@"; }
  rtid=$(q /tag | TAG=$RARE_TAG j 'import json,os,sys; print(next((t["id"] for t in json.load(sys.stdin) if t["label"]==os.environ["TAG"]), ""))')
  [ -n "$rtid" ] || rtid=$(q /tag -X POST -d "{\"label\":\"$RARE_TAG\"}" | j 'import json,sys; print(json.load(sys.stdin)["id"])')
  while IFS= read -r ix; do
    q "/indexer/$(j 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$ix")?forceSave=true" -X PUT -d "$ix" >/dev/null
  done < <(q /indexer | TID=$rtid SUFFIX="$RARE_SUFFIX (Prowlarr)" j '
import json, os, sys
t, suffix = int(os.environ["TID"]), os.environ["SUFFIX"]
for i in json.load(sys.stdin):
    want = i["name"].endswith(suffix)
    if want != (t in i["tags"]):
        i["tags"] = sorted(set(i["tags"]) | {t}) if want else [x for x in i["tags"] if x != t]
        print(json.dumps(i))
')
  q /indexer | TID=$rtid j '
import json, os, sys
t = int(os.environ["TID"])
for i in json.load(sys.stdin):
    if i["protocol"] == "torrent":
        ms = next((f.get("value") for f in i["fields"] if f["name"] == "minimumSeeders"), None)
        print("  ", sys.argv[1] + ":", i["name"] + ", min seeders", ms, "| tag old-rare" if t in i["tags"] else "")' "$a"
done
