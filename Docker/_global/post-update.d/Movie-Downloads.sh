# Movie-Downloads customizations (Docker/CUSTOMIZATIONS.md "Movie-Downloads"). Sourced by post-update-check.sh.
MD_DIR="$DIR/../../Movie-Downloads"
md_arr() { curl -s -m 8 -H "X-Api-Key: $(grep -oP '(?<=<ApiKey>)[^<]+' "/srv/appdata/$1/config.xml")" "http://127.0.0.1:$2$3"; }
md_has() { jq -r --argjson want "$1" '[.[].name] as $n | ($want - $n) | if length == 0 then "ok" else "missing: " + join(", ") end' 2>&1; }
md_auth() { grep -oP '(?<=<AuthenticationMethod>)[^<]+' "/srv/appdata/$1/config.xml" 2>&1; }
md_sab() { curl -s -m 8 "http://127.0.0.1:8081/api?output=json&apikey=$(cat "$MD_DIR/secrets/hook/sabnzbd.apikey")&$1"; }
md_bz() { curl -s -m 8 -H "X-API-KEY: $(cat "$MD_DIR/secrets/hook/bazarr.apikey")" "http://127.0.0.1:6767/api$1"; }
md_qb() { curl -s -m 8 "http://127.0.0.1:8080/api/v2$1"; }
if want radarr; then
  check md.radarr-auth '^External$' "$(md_auth radarr)"
  check md.radarr-profiles '^ok$' "$(md_arr radarr 7878 /api/v3/qualityprofile | md_has '["4K HDR - UHD Bluray + WEB (1080p fallback)","4K HDR - UHD Bluray + WEB (1080p fallback) - English original","4K preferred · older versions allowed","4K preferred · older versions allowed - English original","Swiss German (Mundart) only - any quality","HD Bluray + WEB","Anime - Remux + WEB 1080p"]')"
  check md.radarr-cutoffs '^25000000 20000000 25000000 20000000 1200000$' "$(md_arr radarr 7878 /api/v3/qualityprofile | jq -r '[.[] | {(.name): .cutoffFormatScore}] | add | [."4K HDR - UHD Bluray + WEB (1080p fallback)", ."4K HDR - UHD Bluray + WEB (1080p fallback) - English original", ."4K preferred · older versions allowed", ."4K preferred · older versions allowed - English original", ."Swiss German (Mundart) only - any quality"] | join(" ")' 2>&1)"
  check md.radarr-own-cfs '^ok$' "$(md_arr radarr 7878 /api/v3/customformat | md_has '["Bad Group: BlackBit","Bad Group: Vyndros","Audio: Swiss German","Language: Original + English","Language: Not Original","Resolution: 2160p","Resolution: 1080p","Resolution: 720p","Resolution: SD","Resolution: DVD"]')"
  check md.radarr-extra-audio-cfs '^([4-9][0-9]|[1-9][0-9]{2}) Extra audio$' "$(md_arr radarr 7878 /api/v3/customformat | jq -r '[.[] | select(.name | startswith("Extra audio"))] | "\(length) Extra audio"' 2>&1)"
  check md.radarr-connect '^ok$' "$(md_arr radarr 7878 /api/v3/notification | md_has '["Jellyfin","Language policy","Wrong-film guard","Title wipe","Original audio","Download now: first download only"]')"
  check md.radarr-hook-deps '^ok$' "$(docker exec movie-radarr sh -c 'command -v bash >/dev/null && command -v curl >/dev/null && command -v jq >/dev/null && command -v flock >/dev/null && test -r /scripts/grab-guard.sh && test -r /secrets/bazarr.apikey && echo ok' 2>&1)"
  check md.radarr-download-clients '^qBittorrent:download-now:radarr qBittorrent night::radarr-night SABnzbd:download-now:movies SABnzbd night::movies-night$' "$(t=$(md_arr radarr 7878 /api/v3/tag | jq -c 'map({(.id|tostring): .label}) | add'); md_arr radarr 7878 /api/v3/downloadclient | jq -r --argjson t "$t" '[.[] | select(.enable) | "\(.name):\([.tags[] | $t[tostring]] | join(",")):\(.fields[] | select(.name | test("^(movie|tv)Category$")) | .value)"] | join(" ")' 2>&1)"
  check md.radarr-night-paused '^2 -2$' "$(md_arr radarr 7878 /api/v3/downloadclient | jq -r '[(.[] | select(.name == "qBittorrent night") | .fields[] | select(.name == "initialState") | .value), (.[] | select(.name == "SABnzbd night") | .fields[] | select(.name == "recentMoviePriority") | .value)] | join(" ")' 2>&1)"
  check md.radarr-tags '^ok$' "$(md_arr radarr 7878 /api/v3/tag | jq '[.[] | {name: .label}]' | md_has '["download-now","old-rare","audio-swiss-german"]')"
  check md.radarr-autotagging '^(2[0-9]|[3-9][0-9]) lang-$' "$(md_arr radarr 7878 /api/v3/autotagging | jq -r '[.[] | select(.name | startswith("lang-"))] | "\(length) lang-"' 2>&1)"
  check md.radarr-old-rare-indexers '^old-rare min1=[3-9] min5=[3-9]$' "$(t=$(md_arr radarr 7878 /api/v3/tag | jq '[.[] | select(.label == "old-rare") | .id][0]'); md_arr radarr 7878 /api/v3/indexer | jq -r --argjson t "$t" '[.[] | {r: (.tags | index($t) != null), s: ([.fields[] | select(.name | endswith("minimumSeeders")) | .value][0])} | select(.s != null)] | "old-rare min1=\([.[] | select(.r and .s == 1)] | length) min5=\([.[] | select((.r | not) and .s == 5)] | length)"' 2>&1)"
  check md.radarr-media-management '^true /data/.recyclebin 7 afterManual true srt,ass,ssa,sub,idx$' "$(md_arr radarr 7878 /api/v3/config/mediamanagement | jq -r '"\(.copyUsingHardlinks) \(.recycleBin) \(.recycleBinCleanupDays) \(.rescanAfterRefresh) \(.importExtraFiles) \(.extraFileExtensions)"' 2>&1)"
  check md.radarr-metadata-off '^0 enabled$' "$(md_arr radarr 7878 /api/v3/metadata | jq -r '[.[] | select(.enable)] | "\(length) enabled"' 2>&1)"
fi
if want sonarr; then
  check md.sonarr-auth '^External$' "$(md_auth sonarr)"
  check md.sonarr-profiles '^ok$' "$(md_arr sonarr 8989 /api/v3/qualityprofile | md_has '["4K HDR - WEB-2160p (1080p fallback)","4K HDR - WEB-2160p (1080p fallback) - English original","4K preferred · older versions allowed","4K preferred · older versions allowed - English original","Swiss German (Mundart) only - any quality","WEB-1080p","Anime - Bluray + WEB 1080p"]')"
  check md.sonarr-cutoffs '^25000000 20000000 25000000 20000000 1200000$' "$(md_arr sonarr 8989 /api/v3/qualityprofile | jq -r '[.[] | {(.name): .cutoffFormatScore}] | add | [."4K HDR - WEB-2160p (1080p fallback)", ."4K HDR - WEB-2160p (1080p fallback) - English original", ."4K preferred · older versions allowed", ."4K preferred · older versions allowed - English original", ."Swiss German (Mundart) only - any quality"] | join(" ")' 2>&1)"
  check md.sonarr-own-cfs '^ok$' "$(md_arr sonarr 8989 /api/v3/customformat | md_has '["Bad Group: BlackBit","Bad Group: Vyndros","Audio: Swiss German","Language: Original + English","Language: Not Original","Resolution: 2160p","Resolution: 1080p","Resolution: 720p","Resolution: SD","Season Pack"]')"
  check md.sonarr-extra-audio-cfs '^([4-9][0-9]|[1-9][0-9]{2}) Extra audio$' "$(md_arr sonarr 8989 /api/v3/customformat | jq -r '[.[] | select(.name | startswith("Extra audio"))] | "\(length) Extra audio"' 2>&1)"
  check md.sonarr-connect '^ok$' "$(md_arr sonarr 8989 /api/v3/notification | md_has '["Jellyfin","Language policy","Wrong-film guard","Title wipe","Original audio","Download now: first download only"]')"
  check md.sonarr-hook-deps '^ok$' "$(docker exec movie-sonarr sh -c 'command -v bash >/dev/null && command -v curl >/dev/null && command -v jq >/dev/null && command -v flock >/dev/null && test -r /scripts/grab-guard.sh && test -r /secrets/bazarr.apikey && echo ok' 2>&1)"
  check md.sonarr-download-clients '^qBittorrent:download-now:sonarr qBittorrent night::sonarr-night SABnzbd:download-now:tv SABnzbd night::tv-night$' "$(t=$(md_arr sonarr 8989 /api/v3/tag | jq -c 'map({(.id|tostring): .label}) | add'); md_arr sonarr 8989 /api/v3/downloadclient | jq -r --argjson t "$t" '[.[] | select(.enable) | "\(.name):\([.tags[] | $t[tostring]] | join(",")):\(.fields[] | select(.name | test("^(movie|tv)Category$")) | .value)"] | join(" ")' 2>&1)"
  check md.sonarr-night-paused '^2 -2$' "$(md_arr sonarr 8989 /api/v3/downloadclient | jq -r '[(.[] | select(.name == "qBittorrent night") | .fields[] | select(.name == "initialState") | .value), (.[] | select(.name == "SABnzbd night") | .fields[] | select(.name == "recentTvPriority") | .value)] | join(" ")' 2>&1)"
  check md.sonarr-tags '^ok$' "$(md_arr sonarr 8989 /api/v3/tag | jq '[.[] | {name: .label}]' | md_has '["download-now","old-rare","audio-swiss-german"]')"
  check md.sonarr-autotagging '^(2[0-9]|[3-9][0-9]) lang-$' "$(md_arr sonarr 8989 /api/v3/autotagging | jq -r '[.[] | select(.name | startswith("lang-"))] | "\(length) lang-"' 2>&1)"
  check md.sonarr-old-rare-indexers '^old-rare min1=[3-9] min5=[3-9]$' "$(t=$(md_arr sonarr 8989 /api/v3/tag | jq '[.[] | select(.label == "old-rare") | .id][0]'); md_arr sonarr 8989 /api/v3/indexer | jq -r --argjson t "$t" '[.[] | {r: (.tags | index($t) != null), s: ([.fields[] | select(.name | endswith("minimumSeeders")) | .value][0])} | select(.s != null)] | "old-rare min1=\([.[] | select(.r and .s == 1)] | length) min5=\([.[] | select((.r | not) and .s == 5)] | length)"' 2>&1)"
  check md.sonarr-media-management '^true /data/.recyclebin 7 afterManual true srt,ass,ssa,sub,idx$' "$(md_arr sonarr 8989 /api/v3/config/mediamanagement | jq -r '"\(.copyUsingHardlinks) \(.recycleBin) \(.recycleBinCleanupDays) \(.rescanAfterRefresh) \(.importExtraFiles) \(.extraFileExtensions)"' 2>&1)"
  check md.sonarr-metadata-off '^0 enabled$' "$(md_arr sonarr 8989 /api/v3/metadata | jq -r '[.[] | select(.enable)] | "\(length) enabled"' 2>&1)"
fi
if want radarr sonarr recyclarr; then
  check md.scoring 'all checks passed' "$(python3 "$MD_DIR/scripts/scoring-check.py" 2>&1 | tail -1)"
  check md.swiss-german 'all checks passed' "$(python3 "$MD_DIR/scripts/swiss-german-check.py" 2>&1 | tail -1)"
  check md.host-timers '^ok$' "$(m=''; for u in request-tags.timer night-downloads.timer night-search.timer clear-data-wipe.timer queue-watchdog.timer description-scout.timer nfo-scout.timer language-missing.timer original-audio.timer subtitle-check.timer original-audio-import.path; do [ "$(systemctl is-active "$u")" = active ] || m="$m $u"; done; echo "${m:-ok}")"
fi
if want recyclarr; then
  check md.recyclarr-config '^15 4 \* \* \* ok$' "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' movie-recyclarr | sed -n 's/^CRON_SCHEDULE=//p') $(docker exec movie-recyclarr sh -c 'test -r /config/recyclarr.yml && test -d /config/custom-formats && test -r /config/secrets.yml && echo ok' 2>&1)"
fi
if want prowlarr; then
  check md.prowlarr-auth '^External$' "$(md_auth prowlarr)"
  check md.prowlarr-torrent-indexers '^min5=4 min1=4$' "$(md_arr prowlarr 9696 /api/v1/indexer | jq -r '[.[] | select(.enable) | [.fields[] | select(.name | endswith("inimumSeeders")) | .value][0] | select(. != null)] | "min5=\([.[] | select(. == 5)] | length) min1=\([.[] | select(. == 1)] | length)"' 2>&1)"
  check md.prowlarr-flaresolverr '^FlareSolverr 4$' "$(t=$(md_arr prowlarr 9696 /api/v1/tag | jq '[.[] | select(.label == "flaresolverr") | .id][0]'); echo "$(md_arr prowlarr 9696 /api/v1/indexerProxy | jq -r --argjson t "$t" '[.[] | select(.tags | index($t)) | .name] | join(",")' 2>&1) $(md_arr prowlarr 9696 /api/v1/indexer | jq --argjson t "$t" '[.[] | select(.tags | index($t))] | length' 2>&1)")"
  check md.prowlarr-priorities '^NZBgeek=25 NzbPlanet=26 Treasure Maps=30 NZBIndex=50$' "$(md_arr prowlarr 9696 /api/v1/indexer | jq -r '[.[] | {(.name): .priority}] | add | "NZBgeek=\(.NZBgeek) NzbPlanet=\(.NzbPlanet) Treasure Maps=\(."Treasure Maps") NZBIndex=\(.NZBIndex)"' 2>&1)"
  check md.prowlarr-treasure-limits '^query=null grab=400 unit=0$' "$(md_arr prowlarr 9696 /api/v1/indexer | jq -r '.[] | select(.name == "Treasure Maps") | [.fields[] | select(.name | startswith("baseSettings")) | {(.name | ltrimstr("baseSettings.")): .value}] | add | "query=\(.queryLimit) grab=\(.grabLimit) unit=\(.limitsUnit)"' 2>&1)"
  check md.prowlarr-apps '^Radarr:fullSync Sonarr:fullSync$' "$(md_arr prowlarr 9696 /api/v1/applications | jq -r '[.[] | "\(.name):\(.syncLevel)"] | sort | join(" ")' 2>&1)"
fi
if want qbittorrent; then
  check md.qbit-auth-bypass '^true 7 192\.168\.1\.2/32 172\.28\.10\.204/32 172\.28\.10\.205/32$' "$(md_qb /app/preferences | jq -r '(.bypass_auth_subnet_whitelist | split("\n")) as $w | "\(.bypass_auth_subnet_whitelist_enabled) \($w | length) \($w | map(select(. == "192.168.1.2/32" or . == "172.28.10.204/32" or . == "172.28.10.205/32")) | join(" "))"' 2>&1)"
  check md.qbit-categories '^radarr=/data/torrents/movies radarr-night=/data/torrents/movies sonarr=/data/torrents/tv sonarr-night=/data/torrents/tv$' "$(md_qb /torrents/categories | jq -r '[to_entries | sort_by(.key)[] | "\(.key)=\(.value.savePath)"] | join(" ")' 2>&1)"
  check md.qbit-clamav-autorun '^true /clamav-hook/clamav-hook\.py --qbit "%I" "%F" ok$' "$(md_qb /app/preferences | jq -r '"\(.autorun_enabled) \(.autorun_program)"' 2>&1) $(docker exec movie-qbittorrent sh -c 'test -x /clamav-hook/clamav-hook.py && echo ok' 2>&1)"
  check md.qbit-tun0-seeding '^tun0 2 43200$' "$(md_qb /app/preferences | jq -r '"\(.current_network_interface) \(.max_ratio) \(.max_seeding_time)"' 2>&1)"
fi
if want sabnzbd; then
  check md.sab-host-whitelist '^sabnzbd,gluetun,sabnzbd\.holy-grail\.ch,192\.168\.1\.102$' "$(md_sab mode=get_config | jq -r '.config.misc.host_whitelist | join(",")' 2>&1)"
  check md.sab-unwanted-ext '^22 2 775$' "$(md_sab mode=get_config | jq -r '.config.misc | "\(.unwanted_extensions | length) \(.action_on_unwanted_extensions) \(.permissions)"' 2>&1)"
  check md.sab-clamav-script '^/clamav-hook \*=clamav-hook\.py ok$' "$(md_sab mode=get_config | jq -r '"\(.config.misc.script_dir) *=\(.config.categories[] | select(.name == "*") | .script)"' 2>&1) $(docker exec movie-sabnzbd sh -c 'test -x /clamav-hook/clamav-hook.py && echo ok' 2>&1)"
  check md.sab-categories '^movies=movies movies-night=movies tv=tv tv-night=tv$' "$(md_sab mode=get_config | jq -r '[.config.categories | sort_by(.name)[] | select(.name | test("^(movies|tv)(-night)?$")) | "\(.name)=\(.dir)"] | join(" ")' 2>&1)"
  check md.sab-clamd-reach 'PONG' "$(docker exec movie-sabnzbd python3 -c 'import socket; s = socket.create_connection(("clamav", 3310), 3); s.sendall(b"zPING\0"); print(s.recv(8).decode().strip("\0"))' 2>&1)"
fi
if want bazarr; then
  check md.bazarr-postprocessing '^true /scripts/subtitle-check\.py ok$' "$(md_bz /system/settings | jq -r '.general | "\(.use_postprocessing) \(.postprocessing_cmd | split(" ")[0])"' 2>&1) $(docker exec movie-bazarr sh -c 'test -x /scripts/subtitle-check.py && command -v python3 >/dev/null && echo ok' 2>&1)"
  check md.bazarr-mod-common '^common$' "$(md_bz /system/settings | jq -r '.general.subzero_mods | if type == "array" then join(",") else . end' 2>&1)"
  check md.bazarr-utf8 '^true$' "$(md_bz /system/settings | jq -r .general.utf8_encode 2>&1)"
  check md.bazarr-subsync '^true true false$' "$(md_bz /system/settings | jq -r '.subsync | "\(.use_subsync) \(.use_original_language) \(.no_fix_framerate)"' 2>&1)"
  check md.bazarr-profiles '^(2[2-9]|[3-9][0-9]) profiles$' "$(md_bz /system/languages/profiles | jq -r '"\(length) profiles"' 2>&1)"
fi
if want gluetun vpn-status qbittorrent; then
  check md.vpn-port-forward '^true tun0 connected$' "$(curl -s -m 8 http://127.0.0.1:8087/api/status | jq -r '"\(.match) \(.qbit_interface) \(.qbit_connection)"' 2>&1)"
fi
if want gluetun; then
  check md.idle-vpn-probe '^0$' "$(sed -n 's/^beefy_idle_busy{reason="probe_error"} //p' /run/beefy-idle.prom 2>&1)"
fi
if want autoheal; then
  check md.autoheal-label '^[8-9] labelled$' "$(echo "$(docker ps -q --filter label=autoheal-movie=true | wc -l) labelled")"
fi
