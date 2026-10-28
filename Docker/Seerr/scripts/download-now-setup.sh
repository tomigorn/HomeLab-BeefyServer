#!/usr/bin/env bash
# Download now and My request (README "Download now", "My request"): API keys for the download-now container (secrets/download-now, group 65127; Seerr's own key for request changes) and its qBittorrent no-password entry 172.28.10.205. Idempotent, prints no key.
#   ~/Projects/Docker/Seerr/scripts/download-now-setup.sh
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
D="$HERE/../secrets/download-now"
GID=65127
IP=172.28.10.205/32
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

sudo install -d -o buntu -g "$GID" -m 2750 "$D"
for app in radarr sonarr; do
  sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' "/srv/appdata/$app/config.xml" > "$T/$app.apikey"
done
tr -d '[:space:]' < "$HERE/../../Movie-Downloads/secrets/hook/sabnzbd.apikey" > "$T/sabnzbd.apikey"
tr -d '[:space:]' < "$HERE/../secrets/api-key" > "$T/seerr.apikey"
for f in radarr sonarr sabnzbd seerr; do
  [ -s "$T/$f.apikey" ] || { echo "no $f key" >&2; exit 1; }
  sudo install -o buntu -g "$GID" -m 0640 "$T/$f.apikey" "$D/$f.apikey"
done
ls -l "$D" | awk 'NR>1{print "secret", $1, $3":"$4, $NF}'

# qBittorrent: the container moves and starts the night torrents (no password, like Radarr/Sonarr).
qbit() { sudo docker exec movie-gluetun wget -qO- "$@"; }
WL=$(qbit http://127.0.0.1:8080/api/v2/app/preferences | python3 -c 'import json,sys; w=json.load(sys.stdin)["bypass_auth_subnet_whitelist"].split("\n"); n=sys.argv[1]; print(json.dumps({"bypass_auth_subnet_whitelist": "\n".join(w if n in w else w + [n])}))' "$IP")
qbit --post-data "json=$WL" http://127.0.0.1:8080/api/v2/app/setPreferences
qbit http://127.0.0.1:8080/api/v2/app/preferences | python3 -c 'import json,sys; print("qbit whitelist:", json.load(sys.stdin)["bypass_auth_subnet_whitelist"].replace("\n", ", "))'
