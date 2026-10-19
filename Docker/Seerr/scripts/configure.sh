#!/usr/bin/env bash
# Configure Seerr through its API once the owner account exists (README "First login").
# Idempotent: re-running updates the Radarr/Sonarr entries in place.
#   ~/Projects/Docker/Seerr/scripts/configure.sh
set -euo pipefail
A=http://127.0.0.1:5055/api/v1
KEY=$(python3 -c 'import json; print(json.load(open("/srv/appdata/seerr/settings.json"))["main"]["apiKey"])')
RK=$(grep -oP '(?<=<ApiKey>)[^<]+' /srv/appdata/radarr/config.xml)
SK=$(grep -oP '(?<=<ApiKey>)[^<]+' /srv/appdata/sonarr/config.xml)
j() { curl -sf -H "X-Api-Key: $KEY" -H 'Content-Type: application/json' "$@"; }

j "$A/auth/me" >/dev/null || { echo "no owner yet: finish the first login (README) first"; exit 1; }

echo "== Jellyfin libraries"
j -X POST "$A/settings/jellyfin" -d '{"externalHostname":"https://jellyfin.holy-grail.ch"}' >/dev/null
j -X POST "$A/settings/jellyfin/library/sync" >/dev/null
for id in $(j "$A/settings/jellyfin/library" | python3 -c 'import json,sys; print(" ".join(l["id"] for l in json.load(sys.stdin)))'); do
  j -X PUT "$A/settings/jellyfin/library/$id" -d '{"enabled":true}' >/dev/null
done
j "$A/settings/jellyfin/library" | python3 -c 'import json,sys; [print("  ", l["name"], "enabled" if l.get("enabled") else "disabled") for l in json.load(sys.stdin)]'

echo "== Radarr"
RP=$(curl -sf -H "X-Api-Key: $RK" http://127.0.0.1:7878/api/v3/qualityprofile | python3 -c 'import json,sys; print([p["id"] for p in json.load(sys.stdin) if p["name"].startswith("4K HDR")][0])')
RADARR=$(cat <<JSON
{"name":"Radarr","hostname":"radarr","port":7878,"apiKey":"$RK","useSsl":false,"baseUrl":"","activeProfileId":$RP,"activeProfileName":"4K HDR - UHD Bluray + WEB (1080p fallback)","activeDirectory":"/data/media/movies","is4k":false,"minimumAvailability":"released","isDefault":true,"externalUrl":"https://radarr.holy-grail.ch","syncEnabled":true,"preventSearch":false,"tagRequests":false,"tags":[]}
JSON
)
j -X POST "$A/settings/radarr/test" -d "$RADARR" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   test ok: roots", [r["path"] for r in d["rootFolders"]])'
RID=$(j "$A/settings/radarr" | python3 -c 'import json,sys; r=[x["id"] for x in json.load(sys.stdin) if x["name"]=="Radarr"]; print(r[0] if r else "")')
if [ -n "$RID" ]; then j -X PUT "$A/settings/radarr/$RID" -d "$RADARR" >/dev/null; else j -X POST "$A/settings/radarr" -d "$RADARR" >/dev/null; fi

echo "== Sonarr"
SP=$(curl -sf -H "X-Api-Key: $SK" http://127.0.0.1:8989/api/v3/qualityprofile | python3 -c 'import json,sys; print([p["id"] for p in json.load(sys.stdin) if p["name"].startswith("4K HDR")][0])')
SONARR=$(cat <<JSON
{"name":"Sonarr","hostname":"sonarr","port":8989,"apiKey":"$SK","useSsl":false,"baseUrl":"","activeProfileId":$SP,"activeProfileName":"4K HDR - WEB-2160p (1080p fallback)","activeDirectory":"/data/media/tv","activeLanguageProfileId":1,"is4k":false,"enableSeasonFolders":true,"isDefault":true,"externalUrl":"https://sonarr.holy-grail.ch","syncEnabled":true,"preventSearch":false,"tagRequests":false,"tags":[],"animeTags":[],"seriesType":"standard","animeSeriesType":"anime"}
JSON
)
j -X POST "$A/settings/sonarr/test" -d "$SONARR" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   test ok: roots", [r["path"] for r in d["rootFolders"]])'
SID=$(j "$A/settings/sonarr" | python3 -c 'import json,sys; r=[x["id"] for x in json.load(sys.stdin) if x["name"]=="Sonarr"]; print(r[0] if r else "")')
if [ -n "$SID" ]; then j -X PUT "$A/settings/sonarr/$SID" -d "$SONARR" >/dev/null; else j -X POST "$A/settings/sonarr" -d "$SONARR" >/dev/null; fi

echo "== Main settings"
j -X POST "$A/settings/main" -d "$(j "$A/settings/main" | python3 -c 'import json,sys; d=json.load(sys.stdin); d.pop("apiKey", None); d.update(applicationUrl="https://requests.holy-grail.ch", applicationTitle="Requests"); d["defaultPermissions"] |= 128; print(json.dumps(d))')" >/dev/null
j -X POST "$A/settings/initialize" | python3 -c 'import json,sys; print("   initialized:", json.load(sys.stdin)["initialized"])'

install -d -m 700 "$(dirname "$0")/../secrets"
printf '%s\n' "$KEY" > "$(dirname "$0")/../secrets/api-key"; chmod 600 "$(dirname "$0")/../secrets/api-key"
echo "done - API key saved to secrets/api-key"
