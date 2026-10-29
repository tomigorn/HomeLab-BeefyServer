#!/usr/bin/env bash
# Sets the built-in scheduled backup of Radarr, Sonarr, Prowlarr (every day, 14 days kept) and Bazarr (daily 04:00, 14 kept). Idempotent; the zips land in each app's config and go off-host with the nightly backup (Server/10-Backup.md). Run on beefy as buntu.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
key() { grep -oP '(?<=<ApiKey>)[^<]+' "/srv/appdata/$1/config.xml"; }
for a in "radarr 7878 v3" "sonarr 8989 v3" "prowlarr 9696 v1"; do
    set -- $a
    url="http://127.0.0.1:$2/api/$3/config/host"
    k=$(key "$1")
    cur=$(curl -fsS -m 10 -H "X-Api-Key: $k" "$url")
    new=$(jq '.backupInterval = 1 | .backupRetention = 14' <<<"$cur")
    curl -fsS -m 20 -X PUT -H "X-Api-Key: $k" -H 'Content-Type: application/json' --data-binary "$new" "$url/$(jq -r .id <<<"$cur")" \
        | jq -r --arg a "$1" '"\($a): every \(.backupInterval) day(s), \(.backupRetention) days kept"'
done
bk=$(cat "$HERE/../secrets/hook/bazarr.apikey")
curl -fsS -m 20 -X POST -H "X-API-KEY: $bk" --data 'settings-backup-frequency=Daily' --data 'settings-backup-retention=14' --data 'settings-backup-hour=4' http://127.0.0.1:6767/api/system/settings -o /dev/null
curl -fsS -m 10 -H "X-API-KEY: $bk" http://127.0.0.1:6767/api/system/settings | jq -r '.backup | "bazarr: \(.frequency) at \(.hour):00, \(.retention) kept"'
