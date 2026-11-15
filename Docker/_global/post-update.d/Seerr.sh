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
  check seerr.image-local '^seerr-local:current [0-9a-f]{40}$' "$(docker inspect -f '{{.Config.Image}} {{index .Config.Labels "holy-grail.seerr.tree"}}' seerr 2>&1)"
  check seerr.oidc-build '^v[0-9]+\.[0-9]+\.[0-9]+(-oidc[0-9a-f]{7})? https://sso\.holy-grail\.ch/application/o/authorize/' "$(seerr_api /status | jq -r .commitTag 2>&1) $(seerr_api /auth/oidc/login/holy-grail | jq -r .redirectUrl 2>&1)"
  check seerr.rebuild-timer '^enabled$' "$(systemctl is-enabled seerr-rebuild.timer 2>&1)"
  check seerr.no-inline-script '^0$' "$(p=$(curl -s -m 8 http://127.0.0.1:5055/login); if grep -qi '<html' <<<"$p"; then grep -o '<script[^>]*>' <<<"$p" | grep -v -c -E 'src=|type="application/json"'; else echo unreadable; fi)"
  check seerr.turbopack-bootstrap '^1 200 1$' "$(echo "$(curl -s -m 8 http://127.0.0.1:5055/login | grep -c 'id="__HG_TURBOPACK_BOOTSTRAP__" type="application/json"') $(curl -s -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:5055/hg-turbopack-bootstrap.js) $(curl -s -m 8 http://127.0.0.1:5055/hg-turbopack-bootstrap.js | grep -c __HG_TURBOPACK_BOOTSTRAP__)")"
  # image/patches/20-request-cards-no-self-polling.patch: request cards no longer re-fetch on mount / tab focus, poll 60 s only while downloading
  # since v3.5.2 the image ships without /app/src, so only the compiled chunks prove the patch
  check seerr.request-cards-no-poll '^[1-9][0-9]*$' "$(docker exec seerr sh -c 'grep -l "revalidateOnMount:!1,revalidateOnFocus:!1,revalidateIfStale:!1,refreshInterval:" /app/.next/static/chunks/*.js | wc -l' 2>&1)"
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
  # tip.js finds Seerr's tooltip by these classes (README "Hint boxes"); a renamed class leaves Seerr's boxes following the mouse again
  check seerr.tooltip-classes '^[1-9][0-9]*$' "$(docker exec seerr sh -c 'grep -lF "z-50 text-sm absolute font-normal bg-gray-800" /app/.next/static/chunks/*.js | wc -l' 2>&1)"
  check seerr.sso-reach '"issuer": ?"https://sso.holy-grail.ch/application/o/requests-login/"' "$(docker exec seerr wget -q -T 8 -O- https://sso.holy-grail.ch/application/o/requests-login/.well-known/openid-configuration 2>&1 | jq -c '{issuer}' 2>&1)"
  check seerr.extra-hosts '^192\.168\.1\.2$' "$(docker exec seerr getent ahostsv4 sso.holy-grail.ch 2>&1 | awk 'NR==1{print $1}')"
fi
if want seerr-proxy; then
  # nginx.conf still valid on the image and sub_filter (the bar injection) still compiled in
  check seerr.proxy-config '^ok ok http_sub_module$' "$(echo "$(docker exec seerr-proxy nginx -t 2>&1 | grep -c -e 'syntax is ok' -e 'test is successful' | sed 's/^2$/ok ok/') $(docker exec seerr-proxy nginx -V 2>&1 | grep -o http_sub_module | head -1)")"
  check seerr.proxy-inject '^_indexer-bar/bar.css _indexer-bar/bar.js _indexer-bar/can-wait.js _indexer-bar/download-now.js _indexer-bar/manual.js _indexer-bar/menu-links.css _indexer-bar/menu-links.js _indexer-bar/my-request.js _indexer-bar/night-label.js _indexer-bar/request-filter.js _indexer-bar/request-origin.js _indexer-bar/request-sizes.js _indexer-bar/tip.js$' "$(curl -sL -m 8 http://127.0.0.1:5055/ | grep -o '\(src\|href\)="/_indexer-bar/[a-z-]*\.\(js\|css\)\(?v=[0-9a-f]*\)\?"' | grep -o '_indexer-bar/[a-z-]*\.\(js\|css\)' | sort -u | xargs)"
  check seerr.hint-box '^1$' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/tip.js | grep -c 'setAttribute("data-hg-tip-merged"' 2>&1)"
  check seerr.bar-hints '^[1-9][0-9]* 0$' "$(b=$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/bar.js); echo $(grep -c 'window.hgTip.set' <<<"$b") $(grep -cE '[A-Za-z]\.title = ' <<<"$b"))"
  check seerr.hint-box-pinned '^2$' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/tip.js | grep -cE 'setAttribute\("data-hg-tip-pin"|addEventListener\("pointerover"' 2>&1)"
  check seerr.data-needs-session '^401 401 401 401$' "$(for u in /_indexer-bar/usage.json /_indexer-bar/night-waiting.json /_indexer-bar/request-state.json '/_download-now/api?type=movie&tmdb=603'; do curl -s -m 8 -o /dev/null -w '%{http_code} ' "http://127.0.0.1:5055$u"; done | xargs 2>&1)"
  check seerr.jellyfin-auth-blocked '^403 403 403 [^4]' "$(for r in 'POST /api/v1/auth/jellyfin/quickconnect/initiate' 'POST /api/v1/auth/jellyfin' 'GET /api/v1/auth/JELLYFIN/quickconnect/check' 'GET /api/v1/auth/oidc/login/holy-grail'; do set -- $r; curl -s -m 8 -o /dev/null -w '%{http_code} ' -X $1 "http://127.0.0.1:5055$2"; done | xargs 2>&1)"
  check seerr.bar-config '^1$' "$(curl -sL -m 8 http://127.0.0.1:5055/ | grep -c 'bar.js?v=[0-9a-f]*" data-ib-data="/_indexer-bar/usage.json"')"
  check seerr.bar-texts '^hint note$' "$(seerr_data /_indexer-bar/usage.json | jq -r '"\(if .storage[0].hint.en then "hint" else "no-hint" end) \(if .note.en then "note" else "no-note" end)"' 2>&1)"
  check seerr.bar-storage '^SSD HDD$' "$(seerr_data /_indexer-bar/usage.json | jq -r '[.storage[].name] | join(" ")' 2>&1)"
  check seerr.indexer-bar-data '^true [0-9]+$' "$(seerr_data /_indexer-bar/usage.json | jq -r '"\(.ok) \(.indexers|length)"' 2>&1)"
  check seerr.bar-subtitles '^true SubDL OpenSubtitles$' "$(seerr_data /_indexer-bar/usage.json | jq -r '"\(.subtitlesOk) \([.subtitles[]?.name] | join(" "))"' 2>&1)"
  # exact numbers from the SubDL / OpenSubtitles accounts (keys: Seerr/scripts/subtitle-keys.py), not the ≈ fallback
  check seerr.bar-subtitle-accounts '^exact exact$' "$(seerr_data /_indexer-bar/usage.json | jq -r '"\(.subtitles[] | select(.id == "subdl") | if .searches.approx or .searches.limit == null then "estimate" else "exact" end) \(.subtitles[] | select(.id == "opensubtitlescom") | if .downloads.limit == null then "no-limit" else "exact" end)"' 2>&1)"
  check seerr.bar-premium '^true true [1-9]' "$(seerr_data /_indexer-bar/usage.json | jq -r '"\(.subscriptions | all(.premium != null)) \([.indexers[] | select(.premium)] | length > 0)"' 2>&1) $(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/bar.js | grep -c 'ib-star' 2>&1)"
  check seerr.night-label-data '^array$' "$(seerr_data /_indexer-bar/night-waiting.json | jq -r '.waiting|type' 2>&1)"
  check seerr.request-state-data '^array object object object object fresh$' "$(seerr_data /_indexer-bar/request-state.json | jq -r '"\(.canWait|type) \(.unreleased|type) \(.noRelease|type) \(.searching|type) \(.queue|type)"' 2>&1) $([ $(( $(date +%s) - $(stat -c %Y /srv/appdata/indexer-bar/request-state.json 2>/dev/null || echo 0) )) -lt 600 ] && echo fresh || echo stale)"
  check seerr.request-state-live '^active [1-9]' "$(systemctl is-active request-state.timer 2>&1) $(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/night-label.js | grep -c 'hg-pulse' 2>&1)"
  check seerr.status-icons-script '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/night-label.js | grep -c 'tonight.s scheduled search (04:17)' 2>&1)"
  check seerr.partly-half-circle '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/night-label.js | grep -c 'data-hg-half' 2>&1)"
  check seerr.partly-hints '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/night-label.js | grep -c 'Everything you requested is in Jellyfin' 2>&1)"
  check seerr.request-sizes-data '^object [1-9]' "$(seerr_data /_indexer-bar/request-state.json | jq -r '"\(.sizes|type) \(.sizes|length)"' 2>&1)"
  check seerr.media-sizes-data '^object [1-9]' "$(seerr_data /_indexer-bar/request-state.json | jq -r '"\(.media|type) \(.media|length)"' 2>&1)"
  check seerr.request-sizes-script '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/request-sizes.js | grep -c 'data-hg-size-sort' 2>&1)"
  # GB sort: Seerr's list call with hg_size goes to the download-now backend (one sorted page, Seerr's page size untouched)
  check seerr.size-sort-backend '^401 3 [1-9][0-9]* largest [1-9]$' "$(curl -s -m 8 -o /dev/null -w '%{http_code}' 'http://127.0.0.1:5055/api/v1/request?take=3&skip=0&filter=all&mediaType=all&hg_size=desc') $(seerr_data '/api/v1/request?take=3&skip=0&filter=all&mediaType=all&sort=added&sortDirection=desc&hg_size=desc' | jq -r --slurpfile st /srv/appdata/indexer-bar/request-state.json '"\(.results|length) \(.pageInfo.results) \(if (.results[0].id|tostring) as $i | ($st[0].sizes[$i].size // 0) >= ([$st[0].sizes[] | .size // 0] | max) then "largest" else "not-largest" end)"' 2>&1) $(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/request-sizes.js | grep -c 'hg_size=' 2>&1)"
  # injected scripts: every URL carries the current version of www/ (scripts/bar-assets.sh), cached a year only under that URL; seerr-bar-assets.path writes a new version on every change
  check seerr.asset-cache '^1 current immutable no-cache active$' "$(v=$("$DIR/../../Seerr/scripts/bar-assets.sh" version 2>&1); seen=$(curl -sL -m 8 http://127.0.0.1:5055/ | grep -o '/_indexer-bar/[a-z-]*\.\(js\|css\)?v=[0-9a-f]*' | sed 's/.*?v=//' | sort -u); echo "$(grep -c . <<<"$seen") $([ "$seen" = "$v" ] && echo current || echo "stale:$seen/$v") $(curl -sI -m 5 "http://127.0.0.1:5055/_indexer-bar/bar.js?v=$v" | grep -io 'immutable' | head -1) $(curl -sI -m 5 http://127.0.0.1:5055/_indexer-bar/bar.js | grep -i '^cache-control' | grep -o 'no-cache') $(systemctl is-active seerr-bar-assets.path 2>&1)")"
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
  check seerr.origins-auth '^401$' "$(curl -s -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:5055/_download-now/origins 2>&1)"
  check seerr.origins-data '^object readable$' "$(seerr_data /_download-now/origins | jq -r '.requests|type' 2>&1) $(docker exec seerr-download-now test -r /run/origins/origins.json && echo readable || echo missing)"
  # Finished = Seerr's filter available: completed requests whose media is fully available (not partially)
  check seerr.request-filter '^[1-9][0-9]* 1$' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/request-filter.js | grep -c 'data-hg-filter' 2>&1) $(docker exec seerr grep -A1 "case 'available':" /app/dist/routes/request.js | grep -c 'mediaStatusFilter = \[media_1.MediaStatus.AVAILABLE\]' 2>&1)"
  check seerr.origin-script '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/request-origin.js | grep -c 'data-hg-origin-fact' 2>&1)"
  check seerr.title-pill-hints '^[1-9] [1-9]$' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/night-label.js | grep -c 'handed to Radarr/Sonarr' 2>&1) $(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/download-now.js | grep -c 'grabbedHint' 2>&1)"
  check seerr.manual-auth '^401 302$' "$(for u in /_download-now/manual/problems /_manual/; do curl -s -m 8 -o /dev/null -w '%{http_code} ' "http://127.0.0.1:5055$u"; done | xargs 2>&1)"
  check seerr.manual-backend '^200 array$' "$(r=$(curl -s -m 90 -w '\n%{http_code}' -H "X-Api-Key: $(cat "$DIR/../../Seerr/secrets/api-key")" http://127.0.0.1:5055/_download-now/manual/problems); echo "$(tail -1 <<<"$r") $(head -n -1 <<<"$r" | jq -r '.open|type' 2>&1)")"
  check seerr.manual-page '^302 /manual [1-9]$' "$(echo "$(curl -s -m 8 -o /dev/null -w '%{http_code} %header{location}' http://127.0.0.1:5055/_manual/) $(curl -s -m 8 http://127.0.0.1:5055/_indexer-bar/manual.js | grep -c 'data-hg-manual')")"
  check seerr.manual-menu '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/menu-links.js | grep -c '"/manual"' 2>&1)"
  check seerr.manual-ffprobe '^ffprobe version' "$(docker exec seerr-download-now ffprobe -version 2>&1 | head -1)"
  check seerr.manual-mounts '^/data/torrents:false /data/usenet/complete:false /run/clamav:false /run/scouts:false$' "$(docker inspect -f '{{range .Mounts}}{{.Destination}}:{{.RW}} {{end}}' seerr-download-now 2>&1 | tr ' ' '\n' | grep -E '^/(data|run/clamav|run/scouts)' | sort | xargs)"
  check seerr.manual-scout '^readable$' "$(docker exec seerr-download-now sh -c 'test -r /run/scouts/nfo-scout.json && echo readable || echo missing' 2>&1)"
  check seerr.manual-scout-requests '^writable$' "$(docker exec seerr-download-now sh -c 'touch /run/scout-requests/.check && rm /run/scout-requests/.check && echo writable || echo read-only' 2>&1)"
  check seerr.manual-virus-log '^(readable|no-file-yet)$' "$(docker exec seerr-download-now sh -c 'if test -r /run/clamav/clamav-results.jsonl; then echo readable; elif test -d /run/clamav; then echo no-file-yet; else echo missing; fi' 2>&1)"
  check seerr.manual-vt '^[1-9]' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/manual.js | grep -c 'data-vt' 2>&1)"
  check seerr.manual-override '^[1-9][0-9]* [1-9][0-9]*$' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/manual.js | grep -c 'data-override') $(docker exec seerr-download-now grep -c 'nightly(dn, deps)' /app/manual.py 2>&1)"
  check seerr.manual-state '^writable 65127 700$' "$(docker exec seerr-download-now sh -c 'test -w /state && echo writable || echo read-only' 2>&1) $(stat -c '%u %a' /srv/appdata/seerr-download-now 2>&1)"
  check seerr.download-now-script '^media-actions closest\("main"\)$' "$(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/download-now.js | grep -o 'media-actions' | head -1) $(curl -s -m 5 http://127.0.0.1:5055/_indexer-bar/menu-links.js | grep -o 'closest("main")' | head -1)"
fi
