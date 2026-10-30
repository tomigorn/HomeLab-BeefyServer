# Jellyfin customizations (Docker/CUSTOMIZATIONS.md "Jellyfin"). Sourced by post-update-check.sh.
jf_api() { curl -s -m 8 -H "Authorization: MediaBrowser Token=\"$(cat "$DIR/../../Jellyfin/secrets/api-key")\"" "http://127.0.0.1:8096$1"; }
jf_user_systemctl() { # buntu's user units; as root (sudo, nightly auto-update) through the repo owner's user manager
  local u; u=$(stat -c %U "$DIR")
  if [ "$(id -u)" = 0 ] && [ "$u" != root ]; then sudo -n -u "$u" env XDG_RUNTIME_DIR="/run/user/$(id -u "$u")" systemctl --user "$@"
  else XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user "$@"; fi; }
jf_plugin() { jf_api /Plugins | jq -r --arg n "$1" '[.[] | select(.Name == $n) | "\(.Status) \(.Version)"] | first // "missing"' 2>&1; }
if want jellyfin; then
  check jf.local-build '^jellyfin-local:' "$(docker inspect -f '{{.Config.Image}}' jellyfin 2>&1)"
  check jf.opencl 'intel\.icd intel_legacy1\.icd' "$(docker exec jellyfin ls /etc/OpenCL/vendors 2>&1 | xargs)"
  check jf.render-device '^ok$' "$(docker exec jellyfin sh -c 'test -w /dev/dri/renderD128 && echo ok' 2>&1)"
  check jf.encoding '^\["qsv",true,true,true,true,true\]$' "$(jf_api /System/Configuration/encoding | jq -c '[.HardwareAccelerationType,.EnableHardwareEncoding,.EnableIntelLowPowerH264HwEncoder,.EnableIntelLowPowerHevcHwEncoder,.EnableVppTonemapping,.EnableTonemapping]' 2>&1)"
  check jf.fallback-font '^true NotoSansJP-Regular\.otf$' "$(jf_api /System/Configuration/encoding | jq -r .EnableFallbackFont 2>&1) $(docker exec jellyfin ls /config/fonts 2>&1 | xargs)"
  check jf.network '^\[\["192\.168\.1\.2"\],\["192\.168\.1\.0/24","172\.24\.255\.5/32"\]\]$' "$(jf_api /System/Configuration/network | jq -c '[.KnownProxies,.LocalNetworkSubnets]' 2>&1)"
  check jf.trickplay-hw '^true$' "$(jf_api /System/Configuration | jq -c .TrickplayOptions.EnableHwAcceleration 2>&1)"
  check jf.plugin-oidc '^Active 0\.3\.' "$(jf_plugin 'OIDC Authentication')"
  check jf.oidc-sso-only '^\[true,"DisableForAllUsers","Holy Grail",false\]$' "$(jf_api /Plugins/4c5b9b9680cd4c3d9e3d23fa4ebf6ce4/Configuration | jq -c '[.Enabled,.PasswordLoginMode,.ProviderName,.RedirectSignInPageToProvider]' 2>&1)"
  check jf.users-password-disabled '^\["Jellyfin\.Plugin\.Oidc\.Identity\.OidcPasswordDisabledProvider"\]$' "$(jf_api /Users | jq -c '[.[].Policy.AuthenticationProviderId] | unique' 2>&1)"
  check jf.plugin-js-injector '^Active ' "$(jf_plugin 'JavaScript Injector')"
  check jf.js-injector-index '^JavaScriptInjector/public\.js' "$(curl -s -m 8 http://127.0.0.1:8096/web/index.html | grep -o 'JavaScriptInjector/public\.js' | head -1)"
  check jf.rate-in-tracker '^true$' "$(jf_api /Plugins/f5a34f7b2e8a4e6aa7223a216a81b374/Configuration | jq -c '[.CustomJavaScripts[] | select(.Name == "Rate in Tracker") | .Enabled] | first' 2>&1)"
  check jf.plugin-webhooks '^Active ' "$(jf_plugin 'Webhooks')"
  check jf.webhooks-floppy '^[1-9][0-9]* hooks$' "$(jf_api /Plugins/d8ca599bab3c41b0a4ea6de1d52b9996/Configuration | jq -r '[.Hooks[] | select(.Url | startswith("http://192.168.1.102:8012/webhook/jellyfin/"))] | "\(length) hooks"' 2>&1)"
  check jf.plugin-imdb '^Active ' "$(jf_plugin 'IMDb Ratings NG')"
  check jf.plugin-file-transformation '^Active 3\.' "$(jf_plugin 'File Transformation')"
  check jf.plugin-plugin-pages '^Active 3\.' "$(jf_plugin 'Plugin Pages')"
  check jf.plugin-home-sections '^Active 3\.' "$(jf_plugin 'Home Screen Sections')"
  check jf.home-sections-index '^plugin="Jellyfin\.Plugin\.HomeScreenSections"$' "$(curl -s -m 8 http://127.0.0.1:8096/web/index.html | grep -o 'plugin="Jellyfin\.Plugin\.HomeScreenSections"' | head -1)"
  check jf.empty-home-sections '^true$' "$(jf_api /Plugins/f5a34f7b2e8a4e6aa7223a216a81b374/Configuration | jq -c '[.CustomJavaScripts[] | select(.Name == "Empty home sections") | .Enabled] | first' 2>&1)"
  check jf.empty-home-sections-served '^[1-9]' "$(jf_api /JavaScriptInjector/private.js | grep -c 'data-hss-placeholder' 2>&1)"
  check jf.home-paging-end '^[1-9]' "$(jf_api /JavaScriptInjector/private.js | grep -c 'endPaging' 2>&1)"
  check jf.home-studio-cards '^2$' "$(jf_api /JavaScriptInjector/private.js | grep -o -e 'window.HssStudios = ' -e 'function openList' | sort -u | wc -l 2>&1)"
  check jf.upcoming-windows '^\[1,"Months",3,"Months","DD/MM/YYYY","\."\]$' "$(jf_api /Plugins/b8298e012697407ab44daa8dc795e850/Configuration | jq -c '[.Sonarr.UpcomingTimeframeValue,.Sonarr.UpcomingTimeframeUnit,.Radarr.UpcomingTimeframeValue,.Radarr.UpcomingTimeframeUnit,.DateFormat,.DateDelimiter]' 2>&1)"
  check jf.recently-watched-served '^3$' "$(jf_api /JavaScriptInjector/private.js | grep -o -E 'function (loadRecent|syncRecent|pickSeries)' | sort -u | wc -l)"
  check jf.recently-watched-api '^array$' "$(jf_api "/Items?userId=$(jf_api /Users | jq -r '.[0].Id')&Recursive=true&IsPlayed=true&SortBy=DatePlayed&SortOrder=Descending&IncludeItemTypes=Movie,Episode&Limit=1" | jq -r '.Items | type' 2>&1)"
  check jf.recently-watched-series '^number array$' "$(jf_api "/Items?userId=$(jf_api /Users | jq -r '.[0].Id')&Recursive=true&IncludeItemTypes=Series&Fields=RecursiveItemCount&Limit=1" | jq -r '.Items[0].UserData.UnplayedItemCount | type' 2>&1) $(jf_api "/Items?userId=$(jf_api /Users | jq -r '.[0].Id')&Recursive=true&IncludeItemTypes=Episode&IsPlayed=false&ExcludeLocationTypes=Virtual&ParentIndexNumber=0&EnableImages=false&EnableUserData=false&Limit=1" | jq -r '.Items | type' 2>&1)"
  check jf.home-discover-served '^3$' "$(jf_api /JavaScriptInjector/private.js | grep -o -E 'function (loadDiscover|syncDiscover|homeStyles)' | sort -u | wc -l)"
  check jf.home-networks '^true$' "$(jf_api /Plugins/f5a34f7b2e8a4e6aa7223a216a81b374/Configuration | jq -c '[.CustomJavaScripts[] | select(.Name == "Home networks") | .Enabled] | first' 2>&1)"
  check jf.home-layout '^true$' "$(jf_api /Plugins/f5a34f7b2e8a4e6aa7223a216a81b374/Configuration | jq -c '[.CustomJavaScripts[] | select(.Name == "Home layout") | .Enabled] | first' 2>&1)"
  check jf.home-layout-served '^3$' "$(jf_api /JavaScriptInjector/private.js | grep -o -E 'function (comingSoon|pickSeed|homeExts)' | sort -u | wc -l)"
  check jf.home-upcoming-pop '^true$' "$(jf_api /Plugins/f5a34f7b2e8a4e6aa7223a216a81b374/Configuration | jq -c '[.CustomJavaScripts[] | select(.Name == "Home upcoming") | .Enabled] | first' 2>&1)"
  check jf.studio-logos '^[1-9]' "$(jf_api '/Studios?Recursive=true&EnableImageTypes=Logo&ImageTypeLimit=1' | jq '[.Items[] | select(.ImageTags.Logo)] | length' 2>&1)"
  check jf.studio-logos-timer '^active$' "$(jf_user_systemctl is-active jellyfin-studio-logos.timer 2>&1)"
  check jf.upcoming-cards-served '^2$' "$(jf_api /JavaScriptInjector/private.js | grep -o -E 'function upcoming(Series|Dates)' | sort -u | wc -l)"
  check jf.home-sections-config '^unchanged$' "$(python3 "$DIR/../../Jellyfin/scripts/home-sections.py" --dry-run 2>&1 | tail -1)"
  check jf.home-sections-locked '^\{"Enabled":true,"AllowUserOverride":false' "$(jf_api /HomeScreen/Meta | jq -c . 2>&1)"
  check jf.home-sections-users '^all [1-9][0-9]* users ok$' "$(python3 "$DIR/../../Jellyfin/scripts/home-sections.py" --check 2>&1 | tail -1)"
  check jf.home-settings-hidden '^[1-9]' "$(jf_api /JavaScriptInjector/private.js | grep -c 'hss-lock-style' 2>&1)"
  check jf.auto-collections '^0 libraries would change$' "$(python3 "$DIR/../../Jellyfin/scripts/library-options.py" --dry-run 2>&1 | tail -1)"
  check jf.night-tasks '^0 task\(s\) would change' "$(python3 "$DIR/../../Jellyfin/scripts/night-tasks.py" --dry-run 2>&1 | tail -1)"
  check jf.plugin-intro-skipper '^Active 12\.' "$(jf_plugin 'Intro Skipper')"
  check jf.intro-skipper-buttons-only '^\[false,false,false,false,true\]$' "$(jf_api /Plugins/c83d86bba1e04c35a113e2101cf4ee6b/Configuration | jq -c '[.AutoSkipIntro,.AutoSkipCredits,.UseFileTransformationPlugin,.AutoDetectIntros,.UpdateMediaSegments]' 2>&1)"
  check jf.intro-skipper-detect '^\[true,true,true,true,true,true\]$' "$(jf_api /Plugins/c83d86bba1e04c35a113e2101cf4ee6b/Configuration | jq -c '[.ScanIntroduction,.ScanCredits,.ScanRecap,.ScanPreview,.EnableSubtitleRecapDetection,.EnableSubtitlePreviewDetection]' 2>&1)"
  check jf.segment-provider '^true null$' "$(jf_api '/Libraries/AvailableOptions?libraryContentType=tvshows' | jq -c '[.MediaSegmentProviders[] | select(.Name == "Intro Skipper") | .DefaultEnabled] | first' 2>&1) $(jf_api /Library/VirtualFolders | jq -c '[.[] | select(.CollectionType == "tvshows" and .Name != "Trending shows") | .LibraryOptions.DisabledMediaSegmentProviders | index("Intro Skipper")] | first' 2>&1)"
  check jf.intro-skipper-night '^22:05 04:13$' "$(jf_api /ScheduledTasks | jq -r '[.[] | select(.Key == "IntroSkipperDetectSegmentsTask") | .Triggers[] | .TimeOfDayTicks / 600000000 | "\(. / 60 | floor | tostring | if length < 2 then "0" + . else . end):\(. % 60 | tostring | if length < 2 then "0" + . else . end)"] | join(" ")' 2>&1)"
  check jf.api-keys '^ok$' "$(jf_api /Auth/Keys | jq -r '[.Items[].AppName] as $k | if ($k | index("avatar-sync")) and ($k | index("Seerr")) and ($k | index("hss-popular-hook")) then "ok" else ($k | join(",")) end' 2>&1)"
  check jf.extra-hosts '^192\.168\.1\.2$' "$(docker exec jellyfin getent ahostsv4 sso.holy-grail.ch 2>&1 | awk 'NR==1{print $1}')"
  check jf.user-defaults-timer '^active$' "$(jf_user_systemctl is-active jellyfin-user-defaults.timer 2>&1)"
  check jf.user-defaults-config '^\["",true,"Smart"\]$' "$(python3 -c 'import importlib.util as u,json,sys;s=u.spec_from_file_location("m",sys.argv[1]);m=u.module_from_spec(s);s.loader.exec_module(m);d=m.DEFAULTS;print(json.dumps([d["AudioLanguagePreference"],d["PlayDefaultAudioTrack"],d["SubtitleMode"]],separators=(",",":")))' "$DIR/../../Jellyfin/scripts/user-defaults.py" 2>&1)"
  # Native TV Collections & Trending (Jellyfin README "Native TV")
  check jf.trending-library '^movies /config/trending/library false false \[\] \["Nfo"\]$' "$(jf_api /Library/VirtualFolders | jq -r '[.[] | select(.Name == "Trending items")][0] | "\(.CollectionType) \(.Locations[0]) \(.LibraryOptions.EnableRealtimeMonitor) \(.LibraryOptions.AutomaticallyAddToCollection) \([.LibraryOptions.TypeOptions[] | select(.Type == "Movie") | .MetadataFetchers][0] | tojson) \(.LibraryOptions.LocalMetadataReaderOrder | tojson)"' 2>&1)"
  check jf.trending-shows-library '^tvshows /config/trending/shows false false false \[\] \["Nfo"\] true$' "$(jf_api /Library/VirtualFolders | jq -r '[.[] | select(.Name == "Trending shows")][0] | "\(.CollectionType) \(.Locations[0]) \(.LibraryOptions.EnableRealtimeMonitor) \(.LibraryOptions.EnableTrickplayImageExtraction) \(.LibraryOptions.EnableChapterImageExtraction) \([.LibraryOptions.TypeOptions[].MetadataFetchers] | add | tojson) \(.LibraryOptions.LocalMetadataReaderOrder | tojson) \(.LibraryOptions.DisabledMediaSegmentProviders | index("Intro Skipper") != null)"' 2>&1)"
  check jf.trending-hidden '^all [1-9][0-9]* users hide Trending items and Trending shows$' "$(python3 "$DIR/../../Jellyfin/scripts/trending-setup.py" --check 2>&1 | tail -1)"
  check jf.trending-collections '^37 SortName$' "$(jf_api '/Items?Recursive=true&IncludeItemTypes=BoxSet&Tags=hss-trending&Fields=DisplayOrder&CollapseBoxSetItems=false' | jq -r '"\(.Items | length) \([.Items[].DisplayOrder] | unique | join(","))"' 2>&1)"
  check jf.trending-collections-name '^Collections & Trending$' "$(jf_api /Library/VirtualFolders | jq -r '[.[] | select(.CollectionType == "boxsets") | .Name] | first' 2>&1)"
  check jf.trending-index '^fresh [1-9]' "$(python3 -c 'import json,sys,datetime as d;a=json.load(open(sys.argv[1]));print(("fresh" if d.datetime.now(d.timezone.utc)-d.datetime.fromisoformat(a["updated"])<d.timedelta(days=9) else "stale"),len(a["items"]))' /srv/appdata/jellyfin-popular/trending-index.json 2>&1)"
  check jf.trending-dirs '^ok ok ok$' "$(test -w /srv/appdata/jellyfin/trending/library && echo -n ok || echo -n no; echo -n ' '; test -w /srv/appdata/jellyfin/trending/shows && echo -n ok || echo -n no; echo -n ' '; test -s /srv/appdata/jellyfin/trending/clips/now.mp4 && echo ok || echo no)"
  check jf.trending-hide-served '^[1-9]' "$(jf_api /JavaScriptInjector/private.js | grep -c 'window.HssTrendingHide = ' 2>&1)"
  check jf.trending-path '^active$' "$(jf_user_systemctl is-active jellyfin-trending.path 2>&1)"
  check jf.trending-webhook '^ok$' "$(python3 "$DIR/../../Jellyfin/scripts/trending-webhook.py" check 2>&1)"
  check jf.trending-unwatched '^0 placeholders played$' "$(python3 "$DIR/../../Jellyfin/scripts/trending-unwatched.py" --check 2>&1 | tail -1)"
  GEMS=$(jf_api '/Items?Recursive=true&IncludeItemTypes=BoxSet&Tags=hss-hidden-gems&Fields=DisplayOrder&EnableImages=false')
  GEMS_ADMIN=$(jf_api /Users | jq -r '[.[] | select(.Policy.IsAdministrator)][0].Id' 2>&1)
  check jf.hidden-gems '^1 Hidden Gems Default [1-9][0-9]*$' "$(echo "$GEMS" | jq -r '"\(.Items | length) \([.Items[] | "\(.Name) \(.DisplayOrder)"] | join(","))"' 2>&1) $(jf_api "/Items?ParentId=$(echo "$GEMS" | jq -r '.Items[0].Id' 2>&1)&userId=$GEMS_ADMIN&Limit=0" | jq -r '.TotalRecordCount' 2>&1)"
  # Moonbase (Moonfin apps): Seerr = our facade, the Similar override and the push relay stay off; left nav and the list rows are the defaults
  check jf.plugin-moonbase '^Active 2\.' "$(jf_plugin 'Moonbase')"
  check jf.moonbase-config '^\[true,"http://jellyfin-popular:8099",false,"","left",true,false,true,true,true,true\]$' "$(jf_api /Plugins/8c5d0e914f2a4b6d9e3f1a7c8d9e0f2b/Configuration | jq -c '[.SeerrEnabled,.SeerrUrl,.RecommendationsProviderEnabled,.PushRelayUrl,.DefaultUserSettings.navbarPosition,([.DefaultUserSettings.homeSections[]? | select(.kind == "pluginDynamic")] | length > 0),.DefaultUserSettings.mergeContinueWatchingNextUp,.DefaultUserSettings.displaySeerrRows,.DefaultUserSettings.mediaBarAutoAdvance,.DefaultUserSettings.displayStudiosRows,(.DefaultUserSettings.studiosRowSelectedIds // "" | length > 0)]' 2>&1)"
  check jf.moonbase-order '^recentlyAdded$' "$(jf_api /Plugins/8c5d0e914f2a4b6d9e3f1a7c8d9e0f2b/Configuration | jq -r '[.DefaultUserSettings.mediaBarSourceType] | join(" ")' 2>&1)"
fi
if want popular jellyfin; then
  POP=http://127.0.0.1:8097/hss-popular
  POP_POST=(-s -o /dev/null -w '%{http_code}' -m 8 -X POST -H 'Content-Type: application/json' -d '{"id":"x"}')
  check jf.popular-health '^\{"ok": true\}$' "$(curl -s -m 5 "$POP/health" 2>&1)"
  check jf.popular-hardening '^65128:65128 true \[ALL\]$' "$(docker inspect -f '{{.Config.User}} {{.HostConfig.ReadonlyRootfs}} {{.HostConfig.CapDrop}}' jellyfin-popular 2>&1)"
  check jf.popular-refusals '^401 403 401$' "$(curl "${POP_POST[@]}" -H 'Origin: https://jellyfin.holy-grail.ch' "$POP/request") $(curl "${POP_POST[@]}" -H 'Origin: https://evil.example.com' "$POP/request") $(curl "${POP_POST[@]}" -H 'Origin: https://jellyfin.holy-grail.ch' -H "X-Emby-Token: $(cat "$DIR/../../Jellyfin/secrets/api-key")" "$POP/request")"
  # Code for TV (one-time Seerr facade sign-in code): only the caller's own token, browser Origin only
  check jf.popular-tv-code-refusals '^401 403 401$' "$(curl "${POP_POST[@]}" -H 'Origin: https://jellyfin.holy-grail.ch' "$POP/tv-code") $(curl "${POP_POST[@]}" -H 'Origin: https://evil.example.com' "$POP/tv-code") $(curl "${POP_POST[@]}" -H 'Origin: https://jellyfin.holy-grail.ch' -H "X-Emby-Token: $(cat "$DIR/../../Jellyfin/secrets/api-key")" "$POP/tv-code")"
  check jf.popular-allow-list '^fresh [1-9]' "$(python3 -c 'import json,sys,datetime as d;a=json.load(open(sys.argv[1]));print(("fresh" if d.datetime.now()-d.datetime.fromisoformat(a["updated"])<d.timedelta(days=9) else "stale"),len(a["entries"]))' /srv/appdata/jellyfin-popular/allow.json 2>&1)"
  check jf.popular-state-db '^facade_users rejects requests sessions user_requests$' "$(docker exec jellyfin-popular python3 -c 'import sqlite3; print(*sorted(r[0] for r in sqlite3.connect("file:/state/state.db?mode=ro", uri=True).execute("select name from sqlite_master where type = ?", ("table",))))' 2>&1)"
  check jf.popular-served '^[1-9][0-9]* [1-9]' "$(jf_api /JavaScriptInjector/private.js | grep -c 'window.HssPopular = ' 2>&1) $(jf_api /JavaScriptInjector/private.js | grep -c 'hssPopularNav' 2>&1)"
  # Details overlay: answers only signed-in users (an API key has no user), sidecars written by popular.py and upcoming-popularity.py
  check jf.popular-details-refusals '^401 401$' "$(curl -s -o /dev/null -w '%{http_code}' -m 8 "$POP/details?id=x") $(curl -s -o /dev/null -w '%{http_code}' -m 8 -H "X-Emby-Token: $(cat "$DIR/../../Jellyfin/secrets/api-key")" "$POP/details?id=x")"
  check jf.popular-details-sidecars '^fresh [1-9][0-9]* fresh [0-9]+$' "$(python3 -c 'import json,sys,datetime as d;print(*[("fresh" if d.datetime.now()-d.datetime.fromisoformat(a["updated"])<d.timedelta(days=9) else "stale")+" "+str(len(a["entries"])) for a in map(lambda f: json.load(open(f)), sys.argv[1:])])' /srv/appdata/jellyfin-popular/details.json /srv/appdata/jellyfin-popular/coming.json 2>&1)"
  check jf.details-served '^[1-9][0-9]* [1-9]' "$(jf_api /JavaScriptInjector/private.js | grep -c 'HssDetails = api' 2>&1) $(jf_api /JavaScriptInjector/private.js | grep -c 'window.HssUpcomingInfo = ' 2>&1)"
  check jf.popular-home-slot '^[1-9]' "$(jf_api /JavaScriptInjector/private.js | grep -c 'HssExtraRows' 2>&1)"
  # Jellyfin calls pause() on every .itemsContainer of the home tab: a plain one of ours breaks every route change away from home.
  check jf.home-pause-safe '^[1-9][0-9]* 0$' "$(jf_api /JavaScriptInjector/private.js | grep -c 'function pausable' 2>&1) $(jf_api /JavaScriptInjector/private.js | grep -c 'class=\"itemsContainer hssTopScroll' 2>&1)"
  # the hook listener (8098) is internal only: a wrong secret inside the container gives 403, the public path 404
  check jf.popular-hook-refusals '^403 404$' "$(docker exec jellyfin-popular python3 -c 'import urllib.request as u,urllib.error as e
try: u.urlopen(u.Request("http://127.0.0.1:8098/hook/x", b"{}", method="POST"), timeout=5)
except e.HTTPError as x: print(x.code, end="")' 2>&1) $(curl "${POP_POST[@]}" https://jellyfin.holy-grail.ch/hss-popular/hook/x)"
  # Seerr facade for Moonfin (8099): internal only, so every check runs inside the container
  FACADE_PY='import urllib.request as u,urllib.error as e,json
def c(m,p,h={}):
 try: return u.urlopen(u.Request("http://127.0.0.1:8099/api/v1/"+p,b"{}" if m=="POST" else None,h,method=m),timeout=5)
 except e.HTTPError as x: return x'
  check jf.facade-status '^200 true$' "$(docker exec jellyfin-popular python3 -c "$FACADE_PY
r=c('GET','status')
print(r.status, json.dumps('version' in json.load(r)))" 2>&1)"
  check jf.facade-public '^false false$' "$(docker exec jellyfin-popular python3 -c "$FACADE_PY
d=json.load(c('GET','settings/public'))
print(json.dumps(d['movie4kEnabled']), json.dumps(d['series4kEnabled']))" 2>&1)"
  check jf.facade-refusals '^403 403 401 404 401 403$' "$(docker exec jellyfin-popular python3 -c "$FACADE_PY
print(c('GET','auth/me').code, c('GET','auth/me',{'Cookie':'connect.sid=s%3Aforged.sig'}).code, c('POST','auth/jellyfin').code, c('GET','settings/radarr').code, c('GET','service/radarr').code, c('DELETE','request/1').code)" 2>&1)"
  check jf.facade-unpublished '^0$' "$(docker inspect -f '{{json .NetworkSettings.Ports}}' jellyfin-popular 2>&1 | grep -c 8099)"
  check jf.facade-secret '^600 65128$' "$(stat -c '%a %u' "$DIR/../../Jellyfin/secrets/popular/facade.secret" 2>&1)"
  check jf.popular-timer '^active$' "$(jf_user_systemctl is-active jellyfin-popular.timer 2>&1)"
  # fastpi Traefik route jellyfin.holy-grail.ch/hss-popular/ (HomeLab-FastPi Docker/Traefik/traefik/dynamic/jellyfin-popular.yml)
  check jf.popular-route '^\{"ok": true\} 200$' "$(curl -s -m 10 -w ' %{http_code}' https://jellyfin.holy-grail.ch/hss-popular/health 2>&1)"
fi
