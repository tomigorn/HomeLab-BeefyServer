# Seerr customizations (Docker/CUSTOMIZATIONS.md "Seerr"). Sourced by post-update-check.sh.
SEERR_DIR=/srv/appdata/seerr
seerr_api() { curl -s -m 8 "http://127.0.0.1:5055/api/v1$1"; }
# Download now as a plain requester: "<own request title> <title only others requested>" → HTTP codes
seerr_dn_requester() {
  local k reqs uid q
  k=$(cat "$1")
  reqs=$(curl -s -m 8 -H "X-Api-Key: $k" "http://127.0.0.1:5055/api/v1/request?take=100&filter=all")
  uid=$(jq -r '[.results[] | select((.requestedBy.permissions / 2 | floor) % 2 == 0 and (.requestedBy.permissions / 16 | floor) % 2 == 0 and .status != 3)][0].requestedBy.id' <<<"$reqs")
  for q in "$(jq -r --argjson u "$uid" '[.results[] | select(.requestedBy.id == $u and .status != 3) | "type=\(.type)&tmdb=\(.media.tmdbId)"][0]' <<<"$reqs")" \
           "$(jq -r --argjson u "$uid" '.results as $all | [$all[] | .media.tmdbId as $t | select([$all[] | select(.media.tmdbId == $t and .requestedBy.id == $u)] == []) | "type=\(.type)&tmdb=\($t)"][0]' <<<"$reqs")"; do
    curl -s -m 20 -o /dev/null -w '%{http_code} ' -H "X-Api-Key: $k" -H "X-Api-User: $uid" "http://127.0.0.1:5055/_download-now/api?$q"
  done
}
if want seerr; then
  check seerr.image-pin '@sha256:28744b1ced522beb' "$(docker inspect -f '{{.Config.Image}}' seerr 2>&1)"
  check seerr.oidc-build '^develop-2d1a2ed' "$(seerr_api /status | jq -r .version 2>&1)"
  check seerr.login-public '^\[false,false,"holy-grail"\]$' "$(seerr_api /settings/public | jq -c '[.localLogin,.mediaServerLogin,(.openIdProviders[]?.slug)]' 2>&1)"
  check seerr.login-settings '^\[true,false,false,true,"holy-grail"\]$' "$(jq -c '[.main.oidcLogin,.main.localLogin,.main.mediaServerLogin,.network.trustProxy,.oidc.providers[0].slug]' "$SEERR_DIR/settings.json" 2>&1)"
  check seerr.main '^\["https://requests.holy-grail.ch",8352\]$' "$(jq -c '[.main.applicationUrl,.main.defaultPermissions]' "$SEERR_DIR/settings.json" 2>&1)"
  check seerr.arr-profiles '^\["4K HDR - UHD Bluray \+ WEB \(1080p fallback\)","4K HDR - WEB-2160p \(1080p fallback\)","Anime - Bluray \+ WEB 1080p"\]$' "$(jq -c '[.radarr[0].activeProfileName,.sonarr[0].activeProfileName,.sonarr[0].activeAnimeProfileName]' "$SEERR_DIR/settings.json" 2>&1)"
  check seerr.night-jobs '^0 job\(s\) would change' "$(python3 "$DIR/../../Seerr/scripts/night-jobs.py" --dry-run 2>&1 | tail -1)"
  check seerr.webhook-floppy '^True 142 http://192\.168\.1\.102:8012/webhook/seerr/global/$' "$(jq -r '.notifications.agents.webhook | "\(if .enabled then "True" else "False" end) \(.types) \(.options.webhookUrl)"' "$SEERR_DIR/settings.json" 2>&1)"
  check seerr.avatar-db '^65109 2 [0-9]+$' "$(stat -c %u "$SEERR_DIR/db/db.sqlite3") $(sqlite3 "file:$SEERR_DIR/db/db.sqlite3?immutable=1" "SELECT count(*) FROM pragma_table_info('user') WHERE name IN ('avatar','avatarVersion'); SELECT count(*) FROM linked_accounts;" 2>&1 | xargs)"
  check seerr.sso-reach '"issuer": ?"https://sso.holy-grail.ch/application/o/requests-login/"' "$(docker exec seerr wget -q -T 8 -O- https://sso.holy-grail.ch/application/o/requests-login/.well-known/openid-configuration 2>&1 | jq -c '{issuer}' 2>&1)"
  check seerr.extra-hosts '^192\.168\.1\.2$' "$(docker exec seerr getent ahostsv4 sso.holy-grail.ch 2>&1 | awk 'NR==1{print $1}')"
fi
if want seerr-proxy; then
  check seerr.proxy-inject '^_indexer-bar/bar.css _indexer-bar/bar.js _indexer-bar/can-wait.js _indexer-bar/download-now.js _indexer-bar/menu-links.js _indexer-bar/night-label.js$' "$(curl -sL -m 8 http://127.0.0.1:5055/ | grep -o '_indexer-bar/[a-z-]*\.\(js\|css\)' | sort -u | xargs)"
  check seerr.indexer-bar-data '^true [0-9]+$' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/usage.json | jq -r '"\(.ok) \(.indexers|length)"' 2>&1)"
  check seerr.night-label-data '^array$' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/night-waiting.json | jq -r '.waiting|type' 2>&1)"
fi
if want download-now seerr-proxy; then
  check seerr.download-now-health '^\{"ok": true\}$' "$(curl -s -m 5 http://127.0.0.1:5055/_download-now/health 2>&1)"
  check seerr.download-now-auth '^403$' "$(curl -s -m 8 -o /dev/null -w '%{http_code}' 'http://127.0.0.1:5055/_download-now/api?type=movie&tmdb=603' 2>&1)"
  check seerr.download-now-backend '^(started|downloading|searching|available)$' "$(t=$(curl -s -m 8 -H "X-Api-Key: $(grep -oP '(?<=<ApiKey>)[^<]+' /srv/appdata/radarr/config.xml)" http://127.0.0.1:7878/api/v3/movie | jq -r '.[0].tmdbId' 2>&1); curl -s -m 20 -H "X-Api-Key: $(cat "$DIR/../../Seerr/secrets/api-key")" "http://127.0.0.1:5055/_download-now/api?type=movie&tmdb=$t" | jq -r .state 2>&1)"
  check seerr.download-now-requester '^200 403 $' "$(seerr_dn_requester "$DIR/../../Seerr/secrets/api-key" 2>&1)"
  check seerr.download-now-script '^media-actions closest\("main"\)$' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/download-now.js | grep -o 'media-actions' | head -1) $(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/menu-links.js | grep -o 'closest("main")' | head -1)"
fi
