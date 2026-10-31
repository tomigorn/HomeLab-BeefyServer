# Movie-Downloads customizations (Docker/CUSTOMIZATIONS.md "Movie-Downloads"). Sourced by post-update-check.sh.
MD_DIR="$DIR/../../Movie-Downloads"
md_arr() { curl -s -m 8 -H "X-Api-Key: $(grep -oP '(?<=<ApiKey>)[^<]+' "/srv/appdata/$1/config.xml")" "http://127.0.0.1:$2$3"; }
md_arrtest() { curl -s -m 30 -X POST -H "X-Api-Key: $(grep -oP '(?<=<ApiKey>)[^<]+' "/srv/appdata/$1/config.xml")" "http://127.0.0.1:$2/api/v3/downloadclient/testall" | jq -r '"\(map(.isValid) | all) \(length)"' 2>&1; }
md_has() { jq -r --argjson want "$1" '[.[].name] as $n | ($want - $n) | if length == 0 then "ok" else "missing: " + join(", ") end' 2>&1; }
md_auth() { grep -oP '(?<=<AuthenticationMethod>)[^<]+' "/srv/appdata/$1/config.xml" 2>&1; }
md_sab() { curl -s -m 8 "http://127.0.0.1:8081/api?output=json&apikey=$(cat "$MD_DIR/secrets/hook/sabnzbd.apikey")&$1"; }
md_bz() { curl -s -m 8 -H "X-API-KEY: $(cat "$MD_DIR/secrets/hook/bazarr.apikey")" "http://127.0.0.1:6767/api$1"; }
md_qb() { QBIT_TIMEOUT=8 qbit_api http://127.0.0.1:8080 "$1"; }
# ffprobe (bundled, file protocol only) on a library file from an SSD branch, so no HDD spin-up.
md_ffprobe() { local f; f=$(find /srv/.disks/ssd-hot/media/$2 /srv/.disks/ssd-hot2/media/$2 -name '*.mkv' -size +10M -print -quit 2>/dev/null); [ -n "$f" ] || { echo "no SSD sample"; return; }; docker exec "movie-$1" "/app/$1/bin/ffprobe" -v fatal -show_entries format=duration -of csv=p=0 "/data/${f#/srv/.disks/*/}" 2>&1 | tail -1; }
# Upgrade policy (README "Which version gets downloaded"): audio ladder in the 4K profile, minimum step, extras / season pack scores everywhere.
MD_LADDER='["TrueHD ATMOS","DTS X","TrueHD","DTS-HD MA","FLAC","PCM","DD+ ATMOS","ATMOS (undefined)","DTS-HD HRA","DD+","DTS-ES","DTS","DD","AAC"]'
md_ladder() { jq -r --arg p "$1" --argjson n "$MD_LADDER" '.[] | select(.name == $p) | (.formatItems | map({(.name): .score}) | add) as $s | [$n[] | $s[.] // "none"] | join(" ")' 2>&1; }
md_policy() { jq -r '"step=\([.[].minUpgradeFormatScore] | unique | join(",")) extras=\([.[].formatItems[] | select(.name | startswith("Extra audio: ")) | .score] | unique | join(",")) pack=\([.[].formatItems[] | select(.name == "Season Pack") | .score] | unique | join(","))"' 2>&1; }
md_dirs() { docker exec "movie-$1" sh -c 'b=; for d in "$@"; do [ -d "$d" ] && [ -w "$d" ] || b="$b $d"; done; echo "${b:-ok}"' sh "${@:2}" 2>&1; }
if want radarr; then
  check md.radarr-auth '^External$' "$(md_auth radarr)"
  check md.radarr-qbit-login '^true 4$' "$(md_arrtest radarr 7878)"
  check md.radarr-profiles '^ok$' "$(md_arr radarr 7878 /api/v3/qualityprofile | md_has '["4K HDR - UHD Bluray + WEB (1080p fallback)","4K HDR - UHD Bluray + WEB (1080p fallback) - English original","4K preferred · older versions allowed","4K preferred · older versions allowed - English original","Swiss German (Mundart) only - any quality","HD Bluray + WEB","Anime - Remux + WEB 1080p"]')"
  check md.radarr-cutoffs '^26000000 21000000 26000000 21000000 1200000 6000000 6000000$' "$(md_arr radarr 7878 /api/v3/qualityprofile | jq -r '[.[] | {(.name): .cutoffFormatScore}] | add | [."4K HDR - UHD Bluray + WEB (1080p fallback)", ."4K HDR - UHD Bluray + WEB (1080p fallback) - English original", ."4K preferred · older versions allowed", ."4K preferred · older versions allowed - English original", ."Swiss German (Mundart) only - any quality", ."HD Bluray + WEB", ."Anime - Remux + WEB 1080p"] | join(" ")' 2>&1)"
  check md.radarr-own-cfs '^ok$' "$(md_arr radarr 7878 /api/v3/customformat | md_has '["Bad Group: BlackBit","Bad Group: Vyndros","Audio: Swiss German","Language: Original + English","Language: Not Original","Resolution: 2160p","Resolution: 1080p","Resolution: 720p","Resolution: SD","Resolution: DVD"]')"
  check md.radarr-edition-cfs '^ok$' "$(md_arr radarr 7878 /api/v3/customformat | md_has '["Edition: longer cut","Theatrical Cut","IMAX","IMAX Enhanced","Remaster","4K Remaster","Criterion Collection"]')"
  check md.radarr-edition-scores '^1000000/2000/1$' "$(md_arr radarr 7878 /api/v3/qualityprofile | jq -r 'def se: [.formatItems[] | select(.name == "Edition: longer cut") | .score][0]; (map(select(.name | startswith("Swiss German") | not))) as $o | "\([$o[] | se] | unique | join(","))/\([.[] | select(.name | startswith("Swiss German")) | se] | join(","))/\([$o[] | .minUpgradeFormatScore] | unique | join(","))"' 2>&1)"
  check md.radarr-audio-ladder '^3000 3000 2750 2750 2750 2750 2250 2250 2000 1750 1000 750 750 500$' "$(md_arr radarr 7878 /api/v3/qualityprofile | md_ladder '4K HDR - UHD Bluray + WEB (1080p fallback)')"
  check md.radarr-upgrade-policy '^step=1 extras=0 pack=$' "$(md_arr radarr 7878 /api/v3/qualityprofile | md_policy)"
  check md.radarr-remux '^(Remux-2160p,WEBDL-2160p,WEBRip-2160p below Bluray-2160p -6000 -10000 ?){4}$' "$(md_arr radarr 7878 /api/v3/qualityprofile | jq -r '[.[] | select(.name | test("^4K (HDR - UHD Bluray|preferred)")) | [.items[] | select(.allowed)] as $i | ([$i | to_entries[] | select(.value.items | length > 0) | select([.value.items[].quality.name] | index("Remux-2160p")) | .key][0]) as $r | ([$i | to_entries[] | select(.value.quality.name? == "Bluray-2160p") | .key][0]) as $b | "\([$i[$r].items[].quality.name] | sort | join(",")) \(if $r < $b then "below Bluray-2160p" else "ABOVE" end) \([.formatItems[] | select(.name == "Remux: smaller encode wins") | .score] | join(",")) \(.minFormatScore)"] | join(" ")' 2>&1)"
  check md.radarr-extra-audio-cfs '^([4-9][0-9]|[1-9][0-9]{2}) Extra audio$' "$(md_arr radarr 7878 /api/v3/customformat | jq -r '[.[] | select(.name | startswith("Extra audio"))] | "\(length) Extra audio"' 2>&1)"
  check md.radarr-connect '^ok$' "$(md_arr radarr 7878 /api/v3/notification | md_has '["Jellyfin","Language policy","Wrong-film guard","Title wipe","Original audio","Download now: first download only","Seerr sync"]')"
  check md.radarr-hook-deps '^ok$' "$(docker exec movie-radarr sh -c 'command -v bash >/dev/null && command -v curl >/dev/null && command -v jq >/dev/null && command -v flock >/dev/null && test -r /scripts/grab-guard.sh && test -r /secrets/bazarr.apikey && echo ok' 2>&1)"
  check md.radarr-ffprobe '^[0-9]+\.[0-9]+$' "$(md_ffprobe radarr movies)"
  check md.radarr-download-dirs '^ok$' "$(md_dirs radarr /data/usenet/complete/movies /data/torrents/movies /data/media/movies)"
  check md.radarr-download-clients '^qBittorrent:download-now:radarr qBittorrent night::radarr-night SABnzbd:download-now:movies SABnzbd night::movies-night$' "$(t=$(md_arr radarr 7878 /api/v3/tag | jq -c 'map({(.id|tostring): .label}) | add'); md_arr radarr 7878 /api/v3/downloadclient | jq -r --argjson t "$t" '[.[] | select(.enable) | "\(.name):\([.tags[] | $t[tostring]] | join(",")):\(.fields[] | select(.name | test("^(movie|tv)Category$")) | .value)"] | join(" ")' 2>&1)"
  check md.radarr-night-paused '^2 -2$' "$(md_arr radarr 7878 /api/v3/downloadclient | jq -r '[(.[] | select(.name == "qBittorrent night") | .fields[] | select(.name == "initialState") | .value), (.[] | select(.name == "SABnzbd night") | .fields[] | select(.name == "recentMoviePriority") | .value)] | join(" ")' 2>&1)"
  check md.radarr-tags '^ok$' "$(md_arr radarr 7878 /api/v3/tag | jq '[.[] | {name: .label}]' | md_has '["download-now","old-rare","audio-swiss-german"]')"
  check md.radarr-autotagging '^(2[0-9]|[3-9][0-9]) lang-$' "$(md_arr radarr 7878 /api/v3/autotagging | jq -r '[.[] | select(.name | startswith("lang-"))] | "\(length) lang-"' 2>&1)"
  check md.radarr-old-rare-indexers '^old-rare min1=[3-9] min5=[3-9] tpb=ok$' "$(t=$(md_arr radarr 7878 /api/v3/tag | jq '[.[] | select(.label == "old-rare") | .id][0]'); md_arr radarr 7878 /api/v3/indexer | jq -r --argjson t "$t" '[.[] | {n: .name, e: (.enableAutomaticSearch and .enableInteractiveSearch), r: (.tags | index($t) != null), s: ([.fields[] | select(.name | endswith("minimumSeeders")) | .value][0])} | select(.s != null)] | "old-rare min1=\([.[] | select(.r and .s == 1)] | length) min5=\([.[] | select((.r | not) and .s == 5)] | length) tpb=\(if ([.[] | select(.e and .n == "The Pirate Bay (Prowlarr)" and (.r | not) and .s == 5)] | length) == 1 and ([.[] | select(.e and .n == "The Pirate Bay (old-rare) (Prowlarr)" and .r and .s == 1)] | length) == 1 then "ok" else "BAD" end)"' 2>&1)"
  check md.radarr-media-management '^true /data/.recyclebin 7 afterManual true srt,ass,ssa,sub,idx$' "$(md_arr radarr 7878 /api/v3/config/mediamanagement | jq -r '"\(.copyUsingHardlinks) \(.recycleBin) \(.recycleBinCleanupDays) \(.rescanAfterRefresh) \(.importExtraFiles) \(.extraFileExtensions)"' 2>&1)"
  check md.radarr-min-free '^5896192$' "$(md_arr radarr 7878 /api/v3/config/mediamanagement | jq -r .minimumFreeSpaceWhenImporting 2>&1)"
  check md.radarr-metadata-off '^0 enabled$' "$(md_arr radarr 7878 /api/v3/metadata | jq -r '[.[] | select(.enable)] | "\(length) enabled"' 2>&1)"
  check md.radarr-backup-daily '^1 14$' "$(md_arr radarr 7878 /api/v3/config/host | jq -r '"\(.backupInterval) \(.backupRetention)"' 2>&1)"
fi
if want sonarr; then
  check md.sonarr-auth '^External$' "$(md_auth sonarr)"
  check md.sonarr-qbit-login '^true 4$' "$(md_arrtest sonarr 8989)"
  check md.sonarr-profiles '^ok$' "$(md_arr sonarr 8989 /api/v3/qualityprofile | md_has '["4K HDR - WEB-2160p (1080p fallback)","4K HDR - WEB-2160p (1080p fallback) - English original","4K preferred · older versions allowed","4K preferred · older versions allowed - English original","Swiss German (Mundart) only - any quality","WEB-1080p","Anime - Bluray + WEB 1080p"]')"
  check md.sonarr-cutoffs '^25000000 20000000 25000000 20000000 1200000$' "$(md_arr sonarr 8989 /api/v3/qualityprofile | jq -r '[.[] | {(.name): .cutoffFormatScore}] | add | [."4K HDR - WEB-2160p (1080p fallback)", ."4K HDR - WEB-2160p (1080p fallback) - English original", ."4K preferred · older versions allowed", ."4K preferred · older versions allowed - English original", ."Swiss German (Mundart) only - any quality"] | join(" ")' 2>&1)"
  check md.sonarr-own-cfs '^ok$' "$(md_arr sonarr 8989 /api/v3/customformat | md_has '["Bad Group: BlackBit","Bad Group: Vyndros","Audio: Swiss German","Language: Original + English","Language: Not Original","Resolution: 2160p","Resolution: 1080p","Resolution: 720p","Resolution: SD","Season Pack"]')"
  check md.sonarr-audio-ladder '^3000 3000 2750 2750 2750 2750 2250 2250 2000 1750 1000 750 750 500$' "$(md_arr sonarr 8989 /api/v3/qualityprofile | md_ladder '4K HDR - WEB-2160p (1080p fallback)')"
  check md.sonarr-upgrade-policy '^step=1 extras=0 pack=0$' "$(md_arr sonarr 8989 /api/v3/qualityprofile | md_policy)"
  check md.sonarr-extra-audio-cfs '^([4-9][0-9]|[1-9][0-9]{2}) Extra audio$' "$(md_arr sonarr 8989 /api/v3/customformat | jq -r '[.[] | select(.name | startswith("Extra audio"))] | "\(length) Extra audio"' 2>&1)"
  check md.sonarr-connect '^ok$' "$(md_arr sonarr 8989 /api/v3/notification | md_has '["Jellyfin","Language policy","Wrong-film guard","Title wipe","Original audio","Download now: first download only","Seerr sync"]')"
  check md.sonarr-hook-deps '^ok$' "$(docker exec movie-sonarr sh -c 'command -v bash >/dev/null && command -v curl >/dev/null && command -v jq >/dev/null && command -v flock >/dev/null && test -r /scripts/grab-guard.sh && test -r /secrets/bazarr.apikey && echo ok' 2>&1)"
  check md.sonarr-ffprobe '^[0-9]+\.[0-9]+$' "$(md_ffprobe sonarr tv)"
  check md.sonarr-download-dirs '^ok$' "$(md_dirs sonarr /data/usenet/complete/tv /data/torrents/tv /data/media/tv)"
  check md.sonarr-download-clients '^qBittorrent:download-now:sonarr qBittorrent night::sonarr-night SABnzbd:download-now:tv SABnzbd night::tv-night$' "$(t=$(md_arr sonarr 8989 /api/v3/tag | jq -c 'map({(.id|tostring): .label}) | add'); md_arr sonarr 8989 /api/v3/downloadclient | jq -r --argjson t "$t" '[.[] | select(.enable) | "\(.name):\([.tags[] | $t[tostring]] | join(",")):\(.fields[] | select(.name | test("^(movie|tv)Category$")) | .value)"] | join(" ")' 2>&1)"
  check md.sonarr-night-paused '^2 -2$' "$(md_arr sonarr 8989 /api/v3/downloadclient | jq -r '[(.[] | select(.name == "qBittorrent night") | .fields[] | select(.name == "initialState") | .value), (.[] | select(.name == "SABnzbd night") | .fields[] | select(.name == "recentTvPriority") | .value)] | join(" ")' 2>&1)"
  check md.sonarr-tags '^ok$' "$(md_arr sonarr 8989 /api/v3/tag | jq '[.[] | {name: .label}]' | md_has '["download-now","old-rare","audio-swiss-german"]')"
  check md.sonarr-autotagging '^(2[0-9]|[3-9][0-9]) lang-$' "$(md_arr sonarr 8989 /api/v3/autotagging | jq -r '[.[] | select(.name | startswith("lang-"))] | "\(length) lang-"' 2>&1)"
  check md.sonarr-old-rare-indexers '^old-rare min1=[3-9] min5=[3-9] tpb=ok$' "$(t=$(md_arr sonarr 8989 /api/v3/tag | jq '[.[] | select(.label == "old-rare") | .id][0]'); md_arr sonarr 8989 /api/v3/indexer | jq -r --argjson t "$t" '[.[] | {n: .name, e: (.enableAutomaticSearch and .enableInteractiveSearch), r: (.tags | index($t) != null), s: ([.fields[] | select(.name | endswith("minimumSeeders")) | .value][0])} | select(.s != null)] | "old-rare min1=\([.[] | select(.r and .s == 1)] | length) min5=\([.[] | select((.r | not) and .s == 5)] | length) tpb=\(if ([.[] | select(.e and .n == "The Pirate Bay (Prowlarr)" and (.r | not) and .s == 5)] | length) == 1 and ([.[] | select(.e and .n == "The Pirate Bay (old-rare) (Prowlarr)" and .r and .s == 1)] | length) == 1 then "ok" else "BAD" end)"' 2>&1)"
  check md.sonarr-media-management '^true /data/.recyclebin 7 afterManual true srt,ass,ssa,sub,idx$' "$(md_arr sonarr 8989 /api/v3/config/mediamanagement | jq -r '"\(.copyUsingHardlinks) \(.recycleBin) \(.recycleBinCleanupDays) \(.rescanAfterRefresh) \(.importExtraFiles) \(.extraFileExtensions)"' 2>&1)"
  check md.sonarr-min-free '^5896192$' "$(md_arr sonarr 8989 /api/v3/config/mediamanagement | jq -r .minimumFreeSpaceWhenImporting 2>&1)"
  check md.sonarr-metadata-off '^0 enabled$' "$(md_arr sonarr 8989 /api/v3/metadata | jq -r '[.[] | select(.enable)] | "\(length) enabled"' 2>&1)"
  check md.sonarr-backup-daily '^1 14$' "$(md_arr sonarr 8989 /api/v3/config/host | jq -r '"\(.backupInterval) \(.backupRetention)"' 2>&1)"
  check md.sonarr-id-parse '^The Boys 2024 4$' "$(md_arr sonarr 8989 '/api/v3/parse?title=The.Boys.2024.S04.2160p.AMZN.WEB-DL-GRP' | jq -r '.parsedEpisodeInfo | "\(.seriesTitleInfo.titleWithoutYear) \(.seriesTitleInfo.year) \(.seasonNumber)"' 2>&1)"
  check md.sonarr-id-import '^id-match auto-import: [0-9]+ candidate\(s\), [0-9]+ importable$' "$(python3 "$MD_DIR/scripts/queue-watchdog.py" --dry-run 2>&1 | tail -1)"
fi
if want radarr sonarr recyclarr; then
  check md.scoring 'all checks passed' "$(python3 "$MD_DIR/scripts/scoring-check.py" 2>&1 | tail -1)"
  check md.swiss-german 'all checks passed' "$(python3 "$MD_DIR/scripts/swiss-german-check.py" 2>&1 | tail -1)"
  check md.host-timers '^ok$' "$(m=''; for u in media-metrics.timer request-tags.timer night-downloads.timer request-state.timer night-search.timer clear-data-wipe.timer queue-watchdog.timer description-scout.timer nfo-scout.timer language-missing.timer original-audio.timer subtitle-check.timer original-audio-import.path clear-data-wipe-added.path seerr-sync.path; do [ "$(systemctl is-active "$u")" = active ] || m="$m $u"; done; echo "${m:-ok}")"
  check md.original-audio-lock '^2$' "$(systemctl show original-audio.service original-audio-import.service -p ExecStart 2>&1 | grep -c '/run/lock/beefy-media.lock')"
  check md.parser-sandbox '^nfo-scout=strict,read-only,same subtitle-check=strict,read-only,yes,same$' "$(for u in nfo-scout subtitle-check; do printf '%s=%s,%s%s,%s ' "$u" "$(systemctl show -p ProtectSystem --value "$u")" "$(systemctl show -p ProtectHome --value "$u")" "$([ "$u" = subtitle-check ] && echo ",$(systemctl show -p PrivateNetwork --value "$u")")" "$(cmp -s "$MD_DIR/systemd/$u.service" "/etc/systemd/system/$u.service" && echo same || echo differs)"; done | xargs)"
  check md.subtitle-scan-branches '^2$' "$(grep -o -e '/srv/\.disks/ssd-hot2\?/media=/srv/video/media' /etc/systemd/system/subtitle-check.service | sort -u | wc -l)"
  check md.original-audio-units '^path=active,success import=success,0,cap_fowner sweep=success,cap_fowner same$' "$(v() { systemctl show -p "$2" --value "$1"; }; i=original-audio-import; echo "path=$(systemctl is-active $i.path),$(v $i.path Result) import=$(v $i.service Result),$(v $i.service StartLimitIntervalUSec),$(v $i.service AmbientCapabilities) sweep=$(v original-audio.service Result),$(v original-audio.service AmbientCapabilities) $(for u in original-audio.service $i.service $i.path; do cmp -s "$MD_DIR/systemd/$u" "/etc/systemd/system/$u" || echo "$u"; done | xargs | sed -e 's/^$/same/;t' -e 's/^/differs: /')")"
  check md.seerr-sync '^path=active units=same job=jellyfin-full-scan$' "$(echo "path=$(systemctl is-active seerr-sync.path) units=$(for u in seerr-sync.path seerr-sync.service; do cmp -s "$MD_DIR/systemd/$u" "/etc/systemd/system/$u" && echo same || echo differ; done | sort -u | xargs) job=$(curl -s -m 8 -H "X-Api-Key: $(tr -d '[:space:]' < "$MD_DIR/../Seerr/secrets/api-key")" http://127.0.0.1:5055/api/v1/settings/jobs | jq -r '.[] | select(.id == "jellyfin-full-scan") | .id')")"
  check md.night-downloads '^low tariff: (True|False), waiting for the night: [0-9]+$' "$(python3 "$MD_DIR/scripts/night-downloads.py" --dry-run 2>&1 | grep '^low tariff')"
fi
if want recyclarr; then
  check md.recyclarr-config '^15 4 \* \* \* ok$' "$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' movie-recyclarr | sed -n 's/^CRON_SCHEDULE=//p') $(docker exec movie-recyclarr sh -c 'test -r /config/recyclarr.yml && test -d /config/custom-formats && test -r /config/secrets.yml && echo ok' 2>&1)"
fi
if want prowlarr; then
  check md.prowlarr-auth '^External$' "$(md_auth prowlarr)"
  check md.prowlarr-torrent-indexers '^min5=4 min1=4$' "$(md_arr prowlarr 9696 /api/v1/indexer | jq -r '[.[] | select(.enable) | [.fields[] | select(.name | endswith("inimumSeeders")) | .value][0] | select(. != null)] | "min5=\([.[] | select(. == 5)] | length) min1=\([.[] | select(. == 1)] | length)"' 2>&1)"
  check md.prowlarr-flaresolverr '^FlareSolverr 4$' "$(t=$(md_arr prowlarr 9696 /api/v1/tag | jq '[.[] | select(.label == "flaresolverr") | .id][0]'); echo "$(md_arr prowlarr 9696 /api/v1/indexerProxy | jq -r --argjson t "$t" '[.[] | select(.tags | index($t)) | .name] | join(",")' 2>&1) $(md_arr prowlarr 9696 /api/v1/indexer | jq --argjson t "$t" '[.[] | select(.tags | index($t))] | length' 2>&1)")"
  check md.prowlarr-priorities '^NZBgeek=25 NzbPlanet=26 Treasure Maps=27 NZBIndex=50$' "$(md_arr prowlarr 9696 /api/v1/indexer | jq -r '[.[] | {(.name): .priority}] | add | "NZBgeek=\(.NZBgeek) NzbPlanet=\(.NzbPlanet) Treasure Maps=\(."Treasure Maps") NZBIndex=\(.NZBIndex)"' 2>&1)"
  check md.prowlarr-treasure-limits '^query=null grab=400 unit=0$' "$(md_arr prowlarr 9696 /api/v1/indexer | jq -r '.[] | select(.name == "Treasure Maps") | [.fields[] | select(.name | startswith("baseSettings")) | {(.name | ltrimstr("baseSettings.")): .value}] | add | "query=\(.queryLimit) grab=\(.grabLimit) unit=\(.limitsUnit)"' 2>&1)"
  check md.treasure-rss '^rss=true radarr=true sonarr=true$' "rss=$(md_arr prowlarr 9696 /api/v1/appprofile | jq -r '[.[] | select(.name == "Standard")][0].enableRss' 2>&1) radarr=$(md_arr radarr 7878 /api/v3/indexer | jq -r '[.[] | select(.name == "Treasure Maps (Prowlarr)")][0].enableRss' 2>&1) sonarr=$(md_arr sonarr 8989 /api/v3/indexer | jq -r '[.[] | select(.name == "Treasure Maps (Prowlarr)")][0].enableRss' 2>&1)"
  check md.prowlarr-apps '^Radarr:fullSync Sonarr:fullSync$' "$(md_arr prowlarr 9696 /api/v1/applications | jq -r '[.[] | "\(.name):\(.syncLevel)"] | sort | join(" ")' 2>&1)"
  check md.prowlarr-backup-daily '^1 14$' "$(md_arr prowlarr 9696 /api/v1/config/host | jq -r '"\(.backupInterval) \(.backupRetention)"' 2>&1)"
fi
if want qbittorrent; then
  QBIT_PASS_FILE=$MD_DIR/secrets/qbit-webui.password QBIT_JAR=
  # shellcheck source=../../Movie-Downloads/scripts/qbit-api.sh
  . "$MD_DIR/scripts/qbit-api.sh"
  check md.qbit-auth-bypass '^true 4 no-gateway 192\.168\.1\.2/32 172\.28\.10\.204/32 172\.28\.10\.205/32$' "$(md_qb /app/preferences | jq -r '(.bypass_auth_subnet_whitelist | split("\n")) as $w | "\(.bypass_auth_subnet_whitelist_enabled) \($w | length) \(if $w | index("172.28.10.1/32") then "gateway" else "no-gateway" end) \($w | map(select(. == "192.168.1.2/32" or . == "172.28.10.204/32" or . == "172.28.10.205/32")) | join(" "))"' 2>&1)"
  check md.qbit-webui-login '^403 ok$' "$(curl -s -o /dev/null -m 8 -w '%{http_code}' http://127.0.0.1:8080/api/v2/app/version) $(md_qb /app/version >/dev/null && echo ok || echo 'login failed')"
  check md.qbit-categories '^radarr=/data/torrents/movies radarr-night=/data/torrents/movies sonarr=/data/torrents/tv sonarr-night=/data/torrents/tv$' "$(md_qb /torrents/categories | jq -r '[to_entries | sort_by(.key)[] | "\(.key)=\(.value.savePath)"] | join(" ")' 2>&1)"
  check md.qbit-integrity-autorun '^true /clamav-hook/download-integrity\.py --qbit "%I" "%F" true ok$' "$(md_qb /app/preferences | jq -r '"\(.autorun_enabled) \(.autorun_program) \(.recheck_completed_torrents)"' 2>&1) $(docker exec movie-qbittorrent sh -c 'test -x /clamav-hook/download-integrity.py && test -x /clamav-hook/clamav-hook.py && echo ok' 2>&1)"
  check md.qbit-tun0-seeding '^tun0 2 43200$' "$(md_qb /app/preferences | jq -r '"\(.current_network_interface) \(.max_ratio) \(.max_seeding_time)"' 2>&1)"
  check md.qbit-queueing '^true [1-9][0-9]*$' "$(md_qb /app/preferences | jq -r '"\(.queueing_enabled) \(.max_active_downloads)"' 2>&1)"
  check md.qbit-disk-hold '^5758 ok$' "$(grep -oP '(?<="ND_MIN_FREE_GIB", ")[0-9]+' "$MD_DIR/scripts/night-downloads.py") $(jq -e 'has("disk_held")' "$(getent passwd "${SUDO_USER:-$(id -un)}" | cut -d: -f6)/.local/state/movie-downloads/night-downloads.json" >/dev/null 2>&1 && echo ok || echo 'no disk_held in the state')"
  qbit_logout
fi
if want sabnzbd; then
  check md.sab-host-whitelist '^sabnzbd,gluetun,sabnzbd\.holy-grail\.ch,192\.168\.1\.102$' "$(md_sab mode=get_config | jq -r '.config.misc.host_whitelist | join(",")' 2>&1)"
  check md.sab-unwanted-ext '^22 2 775$' "$(md_sab mode=get_config | jq -r '.config.misc | "\(.unwanted_extensions | length) \(.action_on_unwanted_extensions) \(.permissions)"' 2>&1)"
  check md.sab-low-space '^5758G 5758G (true|1)$' "$(md_sab mode=get_config | jq -r '.config.misc | "\(.download_free) \(.complete_free) \(.fulldisk_autoresume)"' 2>&1)"
  check md.sab-integrity-script '^/clamav-hook \*=download-integrity\.py ok$' "$(md_sab mode=get_config | jq -r '"\(.config.misc.script_dir) *=\(.config.categories[] | select(.name == "*") | .script)"' 2>&1) $(docker exec movie-sabnzbd sh -c 'test -x /clamav-hook/download-integrity.py && test -x /clamav-hook/clamav-hook.py && command -v par2 >/dev/null && echo ok' 2>&1)"
  check md.sab-no-direct-write '^(0|false) (0|false)$' "$(md_sab mode=get_config | jq -r '.config.misc | "\(.direct_write) \(.enable_par_cleanup)"' 2>&1)"
  check md.sab-categories '^movies=movies movies-night=movies tv=tv tv-night=tv$' "$(md_sab mode=get_config | jq -r '[.config.categories | sort_by(.name)[] | select(.name | test("^(movies|tv)(-night)?$")) | "\(.name)=\(.dir)"] | join(" ")' 2>&1)"
  check md.sab-clamd-reach 'PONG' "$(docker exec movie-sabnzbd python3 -c 'import socket; s = socket.create_connection(("clamav", 3310), 3); s.sendall(b"zPING\0"); print(s.recv(8).decode().strip("\0"))' 2>&1)"
fi
if want bazarr; then
  check md.bazarr-postprocessing '^true /scripts/subtitle-check\.py ok$' "$(md_bz /system/settings | jq -r '.general | "\(.use_postprocessing) \(.postprocessing_cmd | split(" ")[0])"' 2>&1) $(docker exec movie-bazarr sh -c 'test -x /scripts/subtitle-check.py && command -v python3 >/dev/null && echo ok' 2>&1)"
  check md.bazarr-mod-common '^common$' "$(md_bz /system/settings | jq -r '.general.subzero_mods | if type == "array" then join(",") else . end' 2>&1)"
  check md.bazarr-utf8 '^true$' "$(md_bz /system/settings | jq -r .general.utf8_encode 2>&1)"
  check md.bazarr-subsync '^true true false false$' "$(md_bz /system/settings | jq -r '.subsync | "\(.use_subsync) \(.use_original_language) \(.no_fix_framerate) \(.gss)"' 2>&1)"
  check md.bazarr-backup-daily '^Daily 14 4$' "$(md_bz /system/settings | jq -r '.backup | "\(.frequency) \(.retention) \(.hour)"' 2>&1)"
  check md.bazarr-profiles '^(2[2-9]|[3-9][0-9]) profiles$' "$(md_bz /system/languages/profiles | jq -r '"\(length) profiles"' 2>&1)"
  check md.bazarr-forced-audio-only '^forced=audio-only full=always$' "$(md_bz /system/languages/profiles | jq -r '[.[].items[]] | "forced=\(if all(.[] | select(.forced == "True"); .audio_only_include == "True") then "audio-only" else "ALWAYS" end) full=\(if any(.[] | select(.forced != "True"); .audio_only_include == "True" or .audio_exclude == "True") then "AUDIO-BOUND" else "always" end)"' 2>&1)"
fi
if want gluetun vpn-status qbittorrent; then
  check md.vpn-port-forward '^true tun0 connected$' "$(curl -s -m 8 http://127.0.0.1:8087/api/status | jq -r '"\(.match) \(.qbit_interface) \(.qbit_connection)"' 2>&1)"
fi
if want gluetun; then
  check md.idle-vpn-probe '^0$' "$(sed -n 's/^beefy_idle_busy{reason="probe_error"} //p' /run/beefy-idle.prom 2>&1)"
fi
check md.network-ip-range '^172\.28\.10\.0/24 172\.28\.10\.0/25$' "$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{.IPRange}}{{end}}' movie-downloads 2>&1)"
if want autoheal; then
  check md.autoheal-label '^[8-9] labelled$' "$(echo "$(docker ps -q --filter label=autoheal-movie=true | wc -l) labelled")"
fi
check md.api-unit-sandbox '^6 installed strict$' "$(n=0; for u in clear-data-wipe clear-data-wipe-added seerr-sync request-state night-downloads media-metrics; do cmp -s "$MD_DIR/systemd/$u.service" "/etc/systemd/system/$u.service" && [ "$(systemctl show -p ProtectSystem --value $u.service)" = strict ] && n=$((n+1)); done; echo "$n installed strict")"
