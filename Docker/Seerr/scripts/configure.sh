#!/usr/bin/env bash
# Configure Seerr through its API once the owner account exists (README "First login").
# Idempotent: re-running updates the Radarr/Sonarr entries in place.
#   ~/Projects/Docker/Seerr/scripts/configure.sh
set -euo pipefail
A=http://127.0.0.1:5055/api/v1
D=$(cd "$(dirname "$0")/.." && pwd)
CFG=/srv/appdata/seerr/settings.json

# OIDC has no API in the preview image, only settings.json, which Seerr overwrites from
# memory: edit it with Seerr stopped, and only when something changed.
echo "== Holy Grail OIDC"
SEERR_OIDC_CLIENT_ID=$(grep -oP "(?<=^SEERR_OIDC_CLIENT_ID=).+" "$D/.env" || true)
SEERR_OIDC_CLIENT_SECRET=$(grep -oP "(?<=^SEERR_OIDC_CLIENT_SECRET=).+" "$D/.env" || true)
[ -n "$SEERR_OIDC_CLIENT_ID" ] && [ -n "$SEERR_OIDC_CLIENT_SECRET" ] || { echo "SEERR_OIDC_CLIENT_ID/SECRET missing in .env: run provision-media-sso.py on fastpi (README)"; exit 1; }
export SEERR_OIDC_CLIENT_ID SEERR_OIDC_CLIENT_SECRET CFG LOGO="$D/holy-grail-logo.png"
oidc_settings() {
  python3 - "$1" <<\PY
import base64, json, os, sys
cfg = os.environ["CFG"]
d = json.load(open(cfg))
want = {"slug": "holy-grail", "name": "Holy Grail",
        "issuerUrl": "https://sso.holy-grail.ch/application/o/requests-login/",
        "clientId": os.environ["SEERR_OIDC_CLIENT_ID"],
        "clientSecret": os.environ["SEERR_OIDC_CLIENT_SECRET"],
        "logo": "data:image/png;base64," + base64.b64encode(open(os.environ["LOGO"], "rb").read()).decode(),
        "newUserLogin": True}
others = [p for p in d.get("oidc", {}).get("providers", []) if p.get("slug") != want["slug"]]
new = dict(d, oidc=dict(d.get("oidc", {}), providers=others + [want]),
           main=dict(d["main"], oidcLogin=True), network=dict(d["network"], trustProxy=True))
if sys.argv[1] == "check":
    sys.exit(0 if new == d else 1)
tmp = cfg + ".tmp"
with open(tmp, "w") as f:
    json.dump(new, f, indent=1)
os.chmod(tmp, os.stat(cfg).st_mode & 0o777)
os.replace(tmp, cfg)
PY
}
if oidc_settings check; then
  echo "   unchanged"
else
  (cd "$D" && sudo docker compose stop seerr >/dev/null 2>&1)
  oidc_settings write
  sudo chown 65109 "$CFG"   # written as buntu; Seerr runs as 65109 and must keep writing it
  (cd "$D" && sudo docker compose up -d seerr >/dev/null 2>&1)
  echo "   written, Seerr restarted"
fi
for _ in $(seq 60); do curl -sf "$A/status" >/dev/null && break; sleep 2; done

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
RP=$(curl -sf -H "X-Api-Key: $RK" http://127.0.0.1:7878/api/v3/qualityprofile | python3 -c 'import json,sys; print([p["id"] for p in json.load(sys.stdin) if p["name"]=="4K HDR - UHD Bluray + WEB (1080p fallback)"][0])')
RADARR=$(cat <<JSON
{"name":"Radarr","hostname":"radarr","port":7878,"apiKey":"$RK","useSsl":false,"baseUrl":"","activeProfileId":$RP,"activeProfileName":"4K HDR - UHD Bluray + WEB (1080p fallback)","activeDirectory":"/data/media/movies","is4k":false,"minimumAvailability":"released","isDefault":true,"externalUrl":"https://radarr.holy-grail.ch","syncEnabled":true,"preventSearch":false,"tagRequests":false,"tags":[]}
JSON
)
j -X POST "$A/settings/radarr/test" -d "$RADARR" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   test ok: roots", [r["path"] for r in d["rootFolders"]])'
RID=$(j "$A/settings/radarr" | python3 -c 'import json,sys; r=[x["id"] for x in json.load(sys.stdin) if x["name"]=="Radarr"]; print(r[0] if r else "")')
if [ -n "$RID" ]; then j -X PUT "$A/settings/radarr/$RID" -d "$RADARR" >/dev/null; else j -X POST "$A/settings/radarr" -d "$RADARR" >/dev/null; fi

echo "== Sonarr"
SP=$(curl -sf -H "X-Api-Key: $SK" http://127.0.0.1:8989/api/v3/qualityprofile | python3 -c 'import json,sys; print([p["id"] for p in json.load(sys.stdin) if p["name"]=="4K HDR - WEB-2160p (1080p fallback)"][0])')
AP=$(curl -sf -H "X-Api-Key: $SK" http://127.0.0.1:8989/api/v3/qualityprofile | python3 -c 'import json,sys; print([p["id"] for p in json.load(sys.stdin) if p["name"].startswith("Anime")][0])')
SONARR=$(cat <<JSON
{"name":"Sonarr","hostname":"sonarr","port":8989,"apiKey":"$SK","useSsl":false,"baseUrl":"","activeProfileId":$SP,"activeProfileName":"4K HDR - WEB-2160p (1080p fallback)","activeDirectory":"/data/media/tv","activeLanguageProfileId":1,"activeAnimeProfileId":$AP,"activeAnimeProfileName":"Anime - Bluray + WEB 1080p","activeAnimeDirectory":"/data/media/tv","activeAnimeLanguageProfileId":1,"is4k":false,"enableSeasonFolders":true,"isDefault":true,"externalUrl":"https://sonarr.holy-grail.ch","syncEnabled":true,"preventSearch":false,"tagRequests":false,"tags":[],"animeTags":[],"seriesType":"standard","animeSeriesType":"anime"}
JSON
)
j -X POST "$A/settings/sonarr/test" -d "$SONARR" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   test ok: roots", [r["path"] for r in d["rootFolders"]])'
SID=$(j "$A/settings/sonarr" | python3 -c 'import json,sys; r=[x["id"] for x in json.load(sys.stdin) if x["name"]=="Sonarr"]; print(r[0] if r else "")')
if [ -n "$SID" ]; then j -X PUT "$A/settings/sonarr/$SID" -d "$SONARR" >/dev/null; else j -X POST "$A/settings/sonarr" -d "$SONARR" >/dev/null; fi

echo "== Main settings"
j -X POST "$A/settings/main" -d "$(j "$A/settings/main" | python3 -c 'import json,sys; d=json.load(sys.stdin); d.pop("apiKey", None); d.update(applicationUrl="https://requests.holy-grail.ch", applicationTitle="Requests", localLogin=False, mediaServerLogin=False, newPlexLogin=False); d["defaultPermissions"] |= 128 | 8192 | 16384; print(json.dumps(d))')" >/dev/null
j -X POST "$A/settings/initialize" | python3 -c 'import json,sys; print("   initialized:", json.load(sys.stdin)["initialized"])'

install -d -m 700 "$D/secrets"
printf '%s\n' "$KEY" > "$D/secrets/api-key"; chmod 600 "$D/secrets/api-key"
echo "done - API key saved to secrets/api-key"
