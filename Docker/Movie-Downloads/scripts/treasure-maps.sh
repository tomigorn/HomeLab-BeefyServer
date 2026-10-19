#!/usr/bin/env bash
# Adds or updates the Newznab indexer "Treasure Maps" in Prowlarr; Prowlarr syncs it to Radarr and Sonarr. Idempotent.
#   ~/Projects/Docker/Movie-Downloads/scripts/treasure-maps.sh
# API key: secrets/treasure-maps.apikey (gitignored, 600). Free tier: 100 API calls + 5 grabs per 24 h, so RSS is off (app profile "Search only").
set -euo pipefail

DIR=$(cd "$(dirname "$0")/.." && pwd)
H=${H:-127.0.0.1}
PK=$(grep -oP '(?<=<ApiKey>)[^<]+' /srv/appdata/prowlarr/config.xml)
TK=$(tr -d '\n' < "$DIR/secrets/treasure-maps.apikey")
NAME="Treasure Maps"
PROFILE="Search only"

p() { local path=$1; shift; curl -sf -H "X-Api-Key: $PK" -H 'Content-Type: application/json' "http://$H:9696/api/v1$path" "$@"; }

profile_id=$(p /appprofile | PROFILE=$PROFILE python3 -c 'import json,os,sys; print(next((a["id"] for a in json.load(sys.stdin) if a["name"]==os.environ["PROFILE"]), ""))')
if [ -z "$profile_id" ]; then
  profile_id=$(p /appprofile -X POST -d "{\"name\":\"$PROFILE\",\"enableRss\":false,\"enableAutomaticSearch\":true,\"enableInteractiveSearch\":true,\"minimumSeeders\":1}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
fi

body=$( { p /indexer/schema; echo; p /indexer; } | NAME=$NAME TK=$TK PROFILE_ID=$profile_id python3 -c '
import json, os, sys
raw, dec = sys.stdin.read(), json.JSONDecoder()
schema, end = dec.raw_decode(raw)
existing, _ = dec.raw_decode(raw[end:].lstrip())
cur = next((i for i in existing if i["name"] == os.environ["NAME"]), None)
ix = cur or next(i for i in schema if i["implementation"] == "Newznab" and i["name"] == "Generic Newznab")
values = {"baseUrl": "https://treasure-maps.com", "apiPath": "/api", "apiKey": os.environ["TK"],
          "baseSettings.queryLimit": 100, "baseSettings.grabLimit": 5, "baseSettings.limitsUnit": 0}
for f in ix["fields"]:
    if f["name"] in values:
        f["value"] = values[f["name"]]
ix.update(name=os.environ["NAME"], enable=True, priority=25, appProfileId=int(os.environ["PROFILE_ID"]), tags=[])
ix.pop("presets", None)
print(json.dumps(ix))
')

id=$(p /indexer | NAME=$NAME python3 -c 'import json,os,sys; print(next((i["id"] for i in json.load(sys.stdin) if i["name"]==os.environ["NAME"]), ""))')
if [ -n "$id" ]; then
  p "/indexer/$id" -X PUT -d @- <<<"$body" >/dev/null
else
  id=$(p /indexer -X POST -d @- <<<"$body" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
fi
echo "$NAME: Prowlarr indexer $id, app profile $PROFILE ($profile_id)"
p /command -X POST -d '{"name":"ApplicationIndexerSync"}' >/dev/null
