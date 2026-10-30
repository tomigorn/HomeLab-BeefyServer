# Seerr customizations (Docker/CUSTOMIZATIONS.md "Seerr"). Sourced by post-update-check.sh.
SEERR_DIR=/srv/appdata/seerr
seerr_api() { curl -s -m 8 "http://127.0.0.1:5055/api/v1$1"; }
# data paths need a Seerr session: the admin API key stands in for one
seerr_data() { curl -s -m 8 -H "X-Api-Key: $(cat "$DIR/../../Seerr/secrets/api-key")" "http://127.0.0.1:5055$1"; }
# Download now as a plain requester: "<own request title> <title only others requested>" → HTTP codes
seerr_dn_requester() {
  local k reqs uid q
  k=$(cat "$1")
  reqs=$(curl -s -m 8 -H "X-Api-Key: $k" "http://127.0.0.1:5055/api/v1/request?take=100&filter=all")
  uid=$(jq -r '[.results[] | select((.requestedBy.permissions / 2 | floor) % 2 == 0 and (.requestedBy.permissions / 16 | floor) % 2 == 0 and .status != 3)][0].requestedBy.id' <<<"$reqs")
  for q in "$(jq -r --argjson u "$uid" '[.results[] | select(.requestedBy.id == $u and .status != 3) | "type=\(.type)&tmdb=\(.media.tmdbId)"][0]' <<<"$reqs")" \
           "$(jq -r --argjson u "$uid" '.results as $all | [$all[] | .media.tmdbId as $t | select([$all[] | select(.media.tmdbId == $t and .requestedBy.id == $u)] == []) | "type=\(.type)&tmdb=\($t)"][0]' <<<"$reqs")"; do
    curl -s -m 20 -o /dev/null -w '%{http_code} ' -H "X-Api-Key: $k" -H "X-Api-User: $uid" "http://127.0.0.1:5055${2:-/_download-now/api}?$q"
  done
}
if want seerr; then
  check seerr.image-pin '@sha256:28744b1ced522beb' "$(docker inspect -f '{{.Config.Image}}' seerr 2>&1)"
  check seerr.oidc-build '^develop-2d1a2ed' "$(seerr_api /status | jq -r .version 2>&1)"
  check seerr.login-public '^\[false,false,false,"holy-grail"\]$' "$(seerr_api /settings/public | jq -c '[.localLogin,.mediaServerLogin,.newPlexLogin,(.openIdProviders[]?.slug)]' 2>&1)"
  check seerr.login-settings '^\[true,false,false,true,"holy-grail"\]$' "$(jq -c '[.main.oidcLogin,.main.localLogin,.main.mediaServerLogin,.network.trustProxy,.oidc.providers[0].slug]' "$SEERR_DIR/settings.json" 2>&1)"
  check seerr.main '^\["https://requests.holy-grail.ch",24736\]$' "$(jq -c '[.main.applicationUrl,.main.defaultPermissions]' "$SEERR_DIR/settings.json" 2>&1)"
  # View Requests (16384) for every new and every human user; manage/admin (16, 2) only on the owner account 1; jellyfin-popular = service user
  check seerr.permissions '^16384 0 0$' "$(echo "$(( $(jq '.main.defaultPermissions' "$SEERR_DIR/settings.json" 2>&1) & 16384 ))" $(sqlite3 "file:$SEERR_DIR/db/db.sqlite3?mode=ro" "SELECT count(*) FROM user WHERE id != 1 AND email != 'jellyfin-popular' AND (permissions & 16384) = 0; SELECT count(*) FROM user WHERE id != 1 AND (permissions & 18) != 0;" 2>&1 | xargs))"
  check seerr.arr-profiles '^\["4K HDR - UHD Bluray \+ WEB \(1080p fallback\)","4K HDR - WEB-2160p \(1080p fallback\)","Anime - Bluray \+ WEB 1080p"\]$' "$(jq -c '[.radarr[0].activeProfileName,.sonarr[0].activeProfileName,.sonarr[0].activeAnimeProfileName]' "$SEERR_DIR/settings.json" 2>&1)"
  check seerr.night-jobs '^0 job\(s\) would change' "$(python3 "$DIR/../../Seerr/scripts/night-jobs.py" --dry-run 2>&1 | tail -1)"
  check seerr.webhook-floppy '^True 142 http://192\.168\.1\.102:8012/webhook/seerr/global/$' "$(jq -r '.notifications.agents.webhook | "\(if .enabled then "True" else "False" end) \(.types) \(.options.webhookUrl)"' "$SEERR_DIR/settings.json" 2>&1)"
  check seerr.avatar-db '^65109 2 [0-9]+$' "$(stat -c %u "$SEERR_DIR/db/db.sqlite3") $(sqlite3 "file:$SEERR_DIR/db/db.sqlite3?immutable=1" "SELECT count(*) FROM pragma_table_info('user') WHERE name IN ('avatar','avatarVersion'); SELECT count(*) FROM linked_accounts;" 2>&1 | xargs)"
  check seerr.trending-unticked '^0$' "$(jq '[.jellyfin.libraries[] | select((.name == "Trending items" or .name == "Trending shows") and .enabled)] | length' "$SEERR_DIR/settings.json" 2>&1)"
  check seerr.sso-reach '"issuer": ?"https://sso.holy-grail.ch/application/o/requests-login/"' "$(docker exec seerr wget -q -T 8 -O- https://sso.holy-grail.ch/application/o/requests-login/.well-known/openid-configuration 2>&1 | jq -c '{issuer}' 2>&1)"
  check seerr.extra-hosts '^192\.168\.1\.2$' "$(docker exec seerr getent ahostsv4 sso.holy-grail.ch 2>&1 | awk 'NR==1{print $1}')"
fi
if want seerr-proxy; then
  check seerr.proxy-inject '^_indexer-bar/bar.css _indexer-bar/bar.js _indexer-bar/can-wait.js _indexer-bar/download-now.js _indexer-bar/menu-links.js _indexer-bar/my-request.js _indexer-bar/night-label.js _indexer-bar/request-sizes.js$' "$(curl -sL -m 8 http://127.0.0.1:5055/ | grep -o '\(src\|href\)="/_indexer-bar/[a-z-]*\.\(js\|css\)"' | grep -o '_indexer-bar/[a-z-]*\.\(js\|css\)' | sort -u | xargs)"
  check seerr.data-needs-session '^401 401 401 401$' "$(for u in /_indexer-bar/usage.json /_indexer-bar/night-waiting.json /_indexer-bar/request-state.json '/_download-now/api?type=movie&tmdb=603'; do curl -s -m 8 -o /dev/null -w '%{http_code} ' "http://127.0.0.1:5055$u"; done | xargs 2>&1)"
  check seerr.jellyfin-auth-blocked '^403 403 403 [^4]' "$(for r in 'POST /api/v1/auth/jellyfin/quickconnect/initiate' 'POST /api/v1/auth/jellyfin' 'GET /api/v1/auth/JELLYFIN/quickconnect/check' 'GET /api/v1/auth/oidc/login/holy-grail'; do set -- $r; curl -s -m 8 -o /dev/null -w '%{http_code} ' -X $1 "http://127.0.0.1:5055$2"; done | xargs 2>&1)"
  check seerr.bar-config '^1$' "$(curl -sL -m 8 http://127.0.0.1:5055/ | grep -c 'bar.js" data-ib-data="/_indexer-bar/usage.json"')"
  check seerr.bar-texts '^hint note$' "$(seerr_data /_indexer-bar/usage.json | jq -r '"\(if .storage[0].hint.en then "hint" else "no-hint" end) \(if .note.en then "note" else "no-note" end)"' 2>&1)"
  check seerr.bar-storage '^SSD HDD$' "$(seerr_data /_indexer-bar/usage.json | jq -r '[.storage[].name] | join(" ")' 2>&1)"
  check seerr.indexer-bar-data '^true [0-9]+$' "$(seerr_data /_indexer-bar/usage.json | jq -r '"\(.ok) \(.indexers|length)"' 2>&1)"
  check seerr.night-label-data '^array$' "$(seerr_data /_indexer-bar/night-waiting.json | jq -r '.waiting|type' 2>&1)"
  check seerr.request-state-data '^array object object object object fresh$' "$(seerr_data /_indexer-bar/request-state.json | jq -r '"\(.canWait|type) \(.unreleased|type) \(.noRelease|type) \(.searching|type) \(.queue|type)"' 2>&1) $([ $(( $(date +%s) - $(stat -c %Y /srv/appdata/indexer-bar/request-state.json 2>/dev/null || echo 0) )) -lt 600 ] && echo fresh || echo stale)"
  check seerr.request-state-live '^active [1-9]' "$(systemctl is-active request-state.timer 2>&1) $(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/night-label.js | grep -c 'hg-pulse' 2>&1)"
  check seerr.status-icons-script '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/night-label.js | grep -c 'tonight.s scheduled search (04:17)' 2>&1)"
  check seerr.partly-half-circle '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/night-label.js | grep -c 'data-hg-half' 2>&1)"
  check seerr.request-sizes-data '^object [1-9]' "$(seerr_data /_indexer-bar/request-state.json | jq -r '"\(.sizes|type) \(.sizes|length)"' 2>&1)"
  check seerr.media-sizes-data '^object [1-9]' "$(seerr_data /_indexer-bar/request-state.json | jq -r '"\(.media|type) \(.media|length)"' 2>&1)"
  check seerr.request-sizes-script '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/request-sizes.js | grep -c 'data-hg-size-sort' 2>&1)"
  check seerr.title-size-script '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/request-sizes.js | grep -c 'data-hg-size-fact' 2>&1)"
  check seerr.status-badge-markup '^[1-9]' "$(docker exec seerr sh -c 'grep -l "M5.25 12a.75.75 0 0 1 .75-.75h12" /app/.next/static/chunks/*.js | wc -l' 2>&1)"
fi
if want download-now seerr-proxy; then
  check seerr.download-now-health '^\{"ok": true\}$' "$(curl -s -m 5 http://127.0.0.1:5055/_download-now/health 2>&1)"
  check seerr.download-now-auth '^401$' "$(curl -s -m 8 -o /dev/null -w '%{http_code}' 'http://127.0.0.1:5055/_download-now/api?type=movie&tmdb=603' 2>&1)"
  check seerr.download-now-backend '^(started|downloading|searching|available)$' "$(t=$(curl -s -m 8 -H "X-Api-Key: $(grep -oP '(?<=<ApiKey>)[^<]+' /srv/appdata/radarr/config.xml)" http://127.0.0.1:7878/api/v3/movie | jq -r '.[0].tmdbId' 2>&1); curl -s -m 20 -H "X-Api-Key: $(cat "$DIR/../../Seerr/secrets/api-key")" "http://127.0.0.1:5055/_download-now/api?type=movie&tmdb=$t" | jq -r .state 2>&1)"
  check seerr.download-now-requester '^200 403 $' "$(seerr_dn_requester "$DIR/../../Seerr/secrets/api-key" 2>&1)"
  check seerr.my-request-auth '^401$' "$(curl -s -m 8 -o /dev/null -w '%{http_code}' 'http://127.0.0.1:5055/_download-now/request?type=movie&tmdb=603' 2>&1)"
  check seerr.my-request-requester '^200 403 $' "$(seerr_dn_requester "$DIR/../../Seerr/secrets/api-key" /_download-now/request 2>&1)"
  check seerr.delete-keeps-files '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/my-request.js | grep -c 'data-delete-keep' 2>&1)"
  check seerr.title-pill-hints '^[1-9] [1-9]$' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/night-label.js | grep -c 'handed to Radarr/Sonarr' 2>&1) $(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/download-now.js | grep -c 'grabbedHint' 2>&1)"
  check seerr.download-now-script '^media-actions closest\("main"\)$' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/download-now.js | grep -o 'media-actions' | head -1) $(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/menu-links.js | grep -o 'closest("main")' | head -1)"
fi
