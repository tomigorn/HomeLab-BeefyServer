# Service customizations (beefy)

Everything an image update can silently drop. Rule: claude-config `machines/beefy.md` "Service customizations". Each row has a check in `_global/post-update.d/<Project>.sh`; `update-project.sh` and the nightly auto-update run it and roll back when one fails (README "Image auto-update").

```bash
Docker/_global/scripts/post-update-check.sh all              # everything, ~15 s, read-only
Docker/_global/scripts/post-update-check.sh Movie-Downloads  # one project
SERVICES="radarr" Docker/_global/scripts/post-update-check.sh Movie-Downloads   # only the want-blocks of these services
Docker/_global/scripts/test_post_update_check.sh             # framework tests + ids here vs checks
```

- New customization: a row here **and** a `check <id> <regex> "<actual>"` line in the same change; the test fails when the two disagree.
- Checks run as buntu in `auto-update.service` (docker group, no sudo, `NoNewPrivileges`): no `sudo` in a check, never print a key.
- Host-side pieces (timers, firewall) sit inside the `want` block of the service they serve, so an unrelated update does not roll back over them.

## Seerr

| Customization | Where | Reapply | Verify | Update can break |
|---|---|---|---|---|
| `preview-new-oidc` build pinned by digest | `Seerr/docker-compose.yaml` | Seerr README "Back to stable" | `seerr.image-pin`: Config.Image → @sha256:28744b1c…; `seerr.oidc-build`: /api/v1/status version → develop-2d1a2ed | a stable tag without OIDC removes the Holy Grail login |
| Holy Grail only: oidcLogin on, localLogin off, mediaServerLogin off, trustProxy | `settings.json` | `Seerr/scripts/configure.sh` (Seerr stopped) | `seerr.login-public`: /settings/public → [false,false,"holy-grail"]; `seerr.login-settings`: settings.json → [true,false,false,true,"holy-grail"] | settings schema change drops the OIDC keys |
| App URL, default permissions 8352 | `settings.json` | `configure.sh` | `seerr.main`: → requests.holy-grail.ch, 8352 | |
| Radarr/Sonarr profiles incl. anime profile | `settings.json` | `configure.sh` | `seerr.arr-profiles`: activeProfileName / activeAnimeProfileName → the 4K + anime names | profile renamed by Recyclarr |
| Jobs in the maintenance window | Seerr settings | `Seerr/scripts/night-jobs.py` | `seerr.night-jobs`: night-jobs.py --dry-run → 0 job(s) would change | new job ids, reset schedules |
| Webhook to Floppy (types 142) | Seerr settings | `Seerr/scripts/seerr-webhook.py apply` | `seerr.webhook-floppy`: settings.json → True 142 http://192.168.1.102:8012/webhook/seerr/global/ | agent config renamed |
| Avatar DB write path (fastpi Avatar-Picker writes the user row as uid 65109) | `db/db.sqlite3` | HomeLab-FastPi `Avatar-Picker/README.md` | `seerr.avatar-db`: owner, user.avatar + avatarVersion columns, linked_accounts rows → 65109 2 n | migration renames columns, other uid |
| Reaches sso (OIDC discovery) over the LAN | `extra_hosts` sso → 192.168.1.2 + default route on `jellyfin` net (gw_priority) | compose | `seerr.sso-reach`: wget discovery in the container → requests-login issuer; `seerr.extra-hosts`: getent → 192.168.1.2 | network/route change; without extra_hosts sso resolves via Cloudflare |
| seerr-proxy injects bar.css, bar.js, can-wait.js, menu-links.js, night-label.js, download-now.js | `Seerr/indexer-bar/nginx.conf`, `www/` | `docker restart seerr-proxy` | `seerr.proxy-inject`: the 6 files in the page → all 6 | Seerr gzip/HTML change defeats sub_filter |
| Download now button (Manage panel) + backend `download-now` (Seerr admin/manage-requests via `/api/v1/auth/me`, keys in `secrets/download-now`, uid 65127, qBit bypass 172.28.10.205) | `Seerr/download-now/`, `docker-compose.yaml`, `indexer-bar/www/download-now.js`, `nginx.conf` `/_download-now/` | `Seerr/scripts/download-now-setup.sh`, `docker compose up -d download-now` | `seerr.download-now-health`: → {"ok": true}; `seerr.download-now-auth`: no login → 403; `seerr.download-now-backend`: dry run of a Radarr title → a state | Seerr renames permissions or `auth/me`, Manage panel markup (no button, Seerr unchanged), rotated Radarr/Sonarr/SAB key (re-run setup) |
| Indexer bar data (usage.json, indexer-usage.timer) | `/srv/appdata/indexer-bar` | Seerr README "Indexer bar" | `seerr.indexer-bar-data`: usage.json → true, n indexers | |
| Night label data (night-waiting.json from night-downloads.py) | `/srv/appdata/indexer-bar` | Movie-Downloads `night-downloads.timer` | `seerr.night-label-data`: .waiting → array | |

## Jellyfin

| Customization | Where | Reapply | Verify | Update can break |
|---|---|---|---|---|
| Local build with Intel OpenCL | `Jellyfin/Dockerfile` | `docker compose up -d --build --pull` | `jf.local-build`: image → jellyfin-local; `jf.opencl`: /etc/OpenCL/vendors → intel.icd intel_legacy1.icd | a plain lscr image loses tone-mapping fallback |
| /dev/dri + render group | compose | compose | `jf.render-device`: renderD128 writable → ok | render gid changed by an OS upgrade |
| QSV encoding, low-power encoders, VPP + OpenCL tone mapping | `/config/encoding.xml` | API, Jellyfin README "Settings" | `jf.encoding`: → ["qsv",true,true,true,true,true] | reset on config migration |
| Fallback font Noto Sans JP | `/config/fonts` | README "Network, previews" | `jf.fallback-font`: → true NotoSansJP-Regular.otf | |
| Known proxy fastpi, LAN + VPN subnets | network config | API | `jf.network`: → 192.168.1.2, 192.168.1.0/24 + 172.24.255.5/32 | every client looks remote/local |
| Trickplay with hardware acceleration | system config | API | `jf.trickplay-hw`: → true | |
| OIDC plugin, password login off for all | `Jellyfin.Plugin.Oidc.xml` | Dashboard → Plugins | `jf.plugin-oidc`: → Active 0.3.x; `jf.oidc-sso-only`: → [true,"DisableForAllUsers","Holy Grail",false]; `jf.users-password-disabled`: every user → OidcPasswordDisabledProvider | new ABI marks the plugin NotSupported = password login back |
| JavaScript Injector + "Rate in Tracker" | plugin config | `Jellyfin/scripts/tracker-link.py` | `jf.plugin-js-injector`: → Active; `jf.js-injector-index`: index.html → public.js; `jf.rate-in-tracker`: → true | ABI break, web client change |
| Webhooks (shemanaev) to Floppy, one per member | `Jellyfin.Webhooks.xml` | `Jellyfin/scripts/tracker-webhook.py add` | `jf.plugin-webhooks`: → Active; `jf.webhooks-floppy`: hooks to 192.168.1.102:8012 → ≥ 1 | ABI break |
| IMDb Ratings NG | plugin | Dashboard → Plugins | `jf.plugin-imdb`: → Active | ABI break |
| Scheduled tasks in the maintenance window | Jellyfin tasks | `Jellyfin/scripts/night-tasks.py` | `jf.night-tasks`: --dry-run → 0 task(s) would change | new task keys, reset triggers |
| API keys avatar-sync (fastpi) and Seerr | Jellyfin DB | Dashboard → API Keys | `jf.api-keys`: → ok | DB migration |
| extra_hosts sso → fastpi | compose | compose | `jf.extra-hosts`: getent ahostsv4 → 192.168.1.2 | |
| New-user defaults (user timer): audio = original (empty + default track), subtitles Smart | `Jellyfin/scripts/user-defaults.py` | README "Audio and subtitle language" | `jf.user-defaults-timer`: → active; `jf.user-defaults-config`: script defaults → original/Smart | user API change (script errors) |
| Home Screen Sections 3.0.3 + File Transformation 3.0.2 + Plugin Pages 3.0.2 (IAmParadox27), 15 sections on for all users (Watch Again off and locked off) (README order), locked (`AllowUserOverride` off globally and per section, every user `useModularHome=true`), pagination 30/page so each home load is fresh, Upcoming from Sonarr/Radarr | `Jellyfin.Plugin.HomeScreenSections.xml` (600, holds the arr API keys) | Dashboard → Plugins → Catalog, restart, `Jellyfin/scripts/home-sections.py` | `jf.plugin-file-transformation`, `jf.plugin-plugin-pages`, `jf.plugin-home-sections`: → Active 3.x; `jf.home-sections-index`: index.html → HSS script; `jf.home-sections-config`: --dry-run → unchanged; `jf.home-sections-locked`: Meta → Enabled, no override; `jf.home-sections-users`: --check → all users ok; `jf.upcoming-windows`: → Sonarr 1 Months, Radarr 3 Months, DD/MM/YYYY . | new ABI marks them NotSupported = vanilla home; web client change breaks the loadSections patch; a plugin update that resets the config unlocks it (check fails, rerun the script) |
| Empty home sections: every enabled Home Screen Section keeps its heading plus a grey hint when empty; Upcoming cards get a short date (`Wed 11 Nov`), Upcoming Shows one card per series; Settings → Home section pickers hidden (`lockHomeSettings`) | JavaScript Injector entry "Empty home sections" | `Jellyfin/scripts/home-placeholders.py` | `jf.empty-home-sections`: → true; `jf.empty-home-sections-served`: private.js → placeholder script; `jf.upcoming-cards-served`: private.js → upcomingSeries + upcomingDates; `jf.home-settings-hidden`: private.js → hss-lock-style | HSS markup change (`homeSectionsContainer`, `verticalSection <SectionId>`, `HssPageMeta`): the script then does nothing; Settings → Home markup change (`homeScreenSettingsContainer`, `selectHomeSection<N>`): pickers visible again (cosmetic, the lock is server-side) |
| Recently Watched Movies + Recently Watched TV Series & Collections home rows (after Recently Added Shows, films first): films played in the last 60 days; series watched completely (specials/unaired ignored) and collections with every film played, last play in the last 60 days; newest first, max 20 each; placeholder when empty Built by the injector with the user's own session, not by HSS (`RegisterSection` would make Jellyfin POST user ids to an unauthenticated endpoint on the public URL) | JavaScript Injector entry "Empty home sections" (`home-placeholders/empty-sections.js`) | `Jellyfin/scripts/home-placeholders.py` | `jf.recently-watched-served`: private.js → loadRecent, syncRecent, pickSeries; `jf.recently-watched-api`: played-by-date Items query → array; `jf.recently-watched-series`: series `UnplayedItemCount` → number, unwatched-specials query (`ExcludeLocationTypes=Virtual`, `ParentIndexNumber=0`) → array | web client card markup change (row looks off, clicks dead); Items API drops `SortBy=DatePlayed` (rows empty) or `UnplayedItemCount` (series row empty) |
| Automatic movie collections: `LibraryOptions.AutomaticallyAddToCollection` on the Movies library (franchises become BoxSets, min size stays the Jellyfin default of 2) | Jellyfin library options (DB) | `Jellyfin/scripts/library-options.py`, then a library refresh (Default mode, nothing replaced) | `jf.auto-collections`: `library-options.py --check` → 0 libraries would change | a library re-created in the UI starts with it off (check fails, rerun the script) |
| Intro Skipper 12.0.6: skip buttons only (native media segments, no auto skip, no injected button), analysis 22:05 + 04:13 | `IntroSkipper.xml`, Jellyfin task triggers | Dashboard → Plugins → Catalog, restart, plugin config via API, `Jellyfin/scripts/night-tasks.py` | `jf.plugin-intro-skipper`: → Active 12.x; `jf.intro-skipper-buttons-only`: → [false,false,false,false,true]; `jf.intro-skipper-detect`: → all true; `jf.segment-provider`: → true null; `jf.intro-skipper-night`: → 22:05 04:13 | new ABI marks it NotSupported = no buttons; plugin update renames settings or the task key |

## Movie-Downloads

| Customization | Where | Reapply | Verify | Update can break |
|---|---|---|---|---|
| Authentication External (Authentik in front) | `config.xml` | app UI → Security | `md.radarr-auth`, `md.sonarr-auth`, `md.prowlarr-auth`: → External | reset to Forms on migration |
| Recyclarr profiles (7 per app) | `recyclarr/recyclarr.yml` | `docker compose run --rm recyclarr sync` | `md.radarr-profiles`, `md.sonarr-profiles`: → ok | Recyclarr/TRaSH rename |
| Cutoffs 25000000 / English original 20000000 / Swiss German 1200000 | `recyclarr.yml` | recyclarr sync, `scripts/language-policy.sh` | `md.radarr-cutoffs`, `md.sonarr-cutoffs`: → 25000000 20000000 25000000 20000000 1200000 | |
| Own CFs BlackBit, Vyndros, Swiss German, resolutions, Language Original + English / Not Original, Season Pack (Sonarr), DVD (Radarr) | `recyclarr/custom-formats/` | recyclarr sync | `md.radarr-own-cfs`, `md.sonarr-own-cfs`: → ok | |
| Language CFs "Extra audio: …" | `scripts/language-policy.sh` | language-policy.sh | `md.radarr-extra-audio-cfs`, `md.sonarr-extra-audio-cfs`: → ≥ 40 | |
| Scores and release order | recyclarr + language-policy.sh | both | `md.scoring`: scoring-check.py → all checks passed | parser change scores differently |
| Swiss German profile, tags, hook | language-policy.sh | language-policy.sh | `md.swiss-german`: swiss-german-check.py → all checks passed | Recyclarr resets the profile language |
| Connect: Jellyfin + scripts grab-guard, language-policy-hook, title-wipe-hook, original-audio-hook, download-now-hook | app DB | language-policy.sh | `md.radarr-connect`, `md.sonarr-connect`: → ok | |
| Hook runtime: bash curl jq flock, /scripts, /secrets | compose mounts, image | compose | `md.radarr-hook-deps`, `md.sonarr-hook-deps`: → ok | image drops jq/flock |
| Download clients: download-now tagged + untagged night twins | app DB | `scripts/night-downloads-setup.sh` | `md.radarr-download-clients`, `md.sonarr-download-clients`: name:tag:category list; `md.radarr-night-paused`, `md.sonarr-night-paused`: qBit night initialState, SAB night priority → 2 -2 | |
| Tags download-now, old-rare, audio-swiss-german | app DB | night-downloads-setup.sh, language-policy.sh | `md.radarr-tags`, `md.sonarr-tags`: → ok | |
| Auto-tagging lang-xx | app DB | language-policy.sh | `md.radarr-autotagging`, `md.sonarr-autotagging`: → ≥ 20 lang- | |
| Torrent indexer copies old-rare (min seeders 1) next to min 5 | app indexers | `scripts/torrent-indexers.sh` | `md.radarr-old-rare-indexers`, `md.sonarr-old-rare-indexers`: → old-rare min1=n min5=n | Prowlarr sync wipes tags |
| Hardlinks, recycle bin 7 days, rescan after manual, extra files | media management | API | `md.radarr-media-management`, `md.sonarr-media-management`: → true /data/.recyclebin 7 afterManual true srt,… | |
| Metadata writers off | app metadata | API | `md.radarr-metadata-off`, `md.sonarr-metadata-off`: → 0 enabled | |
| Host timers: request-tags, night-downloads, night-search, clear-data-wipe, queue-watchdog, description-scout, nfo-scout, language-missing, original-audio, subtitle-check, original-audio-import.path | `Movie-Downloads/systemd/` | README per section | `md.host-timers`: → ok | |
| Night queue: movies in parallel, one TV series at a time in episode order (reads Sonarr queue with episodes) | `Movie-Downloads/scripts/night-downloads.py` | README "Can wait" | `md.night-downloads`: --dry-run → low tariff: …, waiting for the night: n | Sonarr queue API drops `episode` → order falls back to SxxEyy in the name |
| Recyclarr cron 04:15, local config + CFs mounted | compose | compose | `md.recyclarr-config`: → 15 4 * * * ok | env renamed |
| Prowlarr torrent indexers: 4 × min 5, 4 × min 1 | Prowlarr | torrent-indexers.sh | `md.prowlarr-torrent-indexers`: → min5=4 min1=4 | |
| FlareSolverr proxy for 1337x + EZTV | Prowlarr | torrent-indexers.sh | `md.prowlarr-flaresolverr`: → FlareSolverr 4 | |
| Treasure Maps limits (Supporter): query limit off, grab limit 400/day, unit Day; bar `downloads = 400`, deadline 2031-11-17 | Prowlarr; `Seerr/indexer-bar/limits.toml` | `scripts/treasure-maps.sh` (re-run) | `md.prowlarr-treasure-limits`: → query=null grab=400 unit=0 | Re-adding the indexer resets the limits; the provider can change tier limits (`t=user`) |
| Indexer priorities 25/26/30/50 | Prowlarr | API / treasure-maps.sh | `md.prowlarr-priorities`: → NZBgeek=25 NzbPlanet=26 Treasure Maps=30 NZBIndex=50 | |
| Apps Radarr + Sonarr fullSync | Prowlarr | UI | `md.prowlarr-apps`: → Radarr:fullSync Sonarr:fullSync | |
| qBittorrent auth bypass subnets (7, incl. fastpi + clamav-queue + Seerr download-now) | `qBittorrent.conf` | Web UI → Options; .205: `Seerr/scripts/download-now-setup.sh` | `md.qbit-auth-bypass`: → true 7 … | auth reset locks out the scripts |
| qBittorrent categories incl. night | qBittorrent | night-downloads-setup.sh | `md.qbit-categories`: → radarr, radarr-night, sonarr, sonarr-night paths | |
| qBittorrent ClamAV autorun | qBittorrent | `scripts/clamav-hooks.sh` | `md.qbit-clamav-autorun`: → true /clamav-hook/clamav-hook.py --qbit … ok | |
| qBittorrent bound to tun0, ratio 2, 30 days | qBittorrent | UI | `md.qbit-tun0-seeding`: → tun0 2 43200 | leak outside the VPN |
| qBittorrent queueing on (max 3 active downloads): the night TV torrent goes to the top of the queue | qBittorrent | Web UI → Options → BitTorrent | `md.qbit-queueing`: → true n | queueing off = no limit and topPrio fails (logged, not fatal) |
| SABnzbd host whitelist | `sabnzbd.ini` | Config → Special | `md.sab-host-whitelist`: → sabnzbd,gluetun,sabnzbd.holy-grail.ch,192.168.1.102 | |
| SABnzbd unwanted extensions (22, abort), permissions 775 | `sabnzbd.ini` | Config → Switches | `md.sab-unwanted-ext`: → 22 2 775 | |
| SABnzbd ClamAV post-processing script | `sabnzbd.ini` | clamav-hooks.sh | `md.sab-clamav-script`: → /clamav-hook *=clamav-hook.py ok; `md.sab-clamd-reach`: zPING to clamav:3310 → PONG | |
| SABnzbd categories incl. night (priority on the Radarr/Sonarr night client) | `sabnzbd.ini` | night-downloads-setup.sh | `md.sab-categories`: → movies, movies-night, tv, tv-night dirs | |
| Bazarr post-processing subtitle-check.py | Bazarr config | `scripts/subtitle-check-setup.sh` | `md.bazarr-postprocessing`: → true /scripts/subtitle-check.py ok | image drops python3 |
| Bazarr mod common only, utf8, ffsubsync on original audio | Bazarr config | subtitle-check-setup.sh, language-policy.sh | `md.bazarr-mod-common`: → common; `md.bazarr-utf8`: → true; `md.bazarr-subsync`: → true true false | settings keys renamed |
| Bazarr language profiles | Bazarr DB | language-policy.sh | `md.bazarr-profiles`: → ≥ 22 profiles | |
| VPN port forward to qBittorrent | gluetun + `scripts/qbit-port.sh` | gluetun restart | `md.vpn-port-forward`: vpn-status → true tun0 connected | gluetun control API auth change |
| Idle watcher reads the VPN tunnel | `Server/8-Idle-Watcher` | – | `md.idle-vpn-probe`: probe_error → 0 | gluetun interface rename |
| autoheal-movie label on 8 containers | compose | compose | `md.autoheal-label`: → 8 labelled | |

## Floppy

| Customization | Where | Reapply | Verify | Update can break |
|---|---|---|---|---|
| Pinned by digest | `Floppy/.env` FLOPPY_TAG | Floppy README "Update" | `floppy.image-pin`: → @sha256:c4959b1f… | |
| Security env: SSO only, admin/demo/debug off, secure cookies, no X-Forwarded-Host, ComicVine off | compose | compose | `floppy.security-env`: env list; `floppy.sso-only-login`: login page → sso 0 password | renamed env vars = default (password form) back |
| Seerr webhook shared secret | `secrets/floppy_seerr_webhook_secret` | Seerr seerr-webhook.py apply | `floppy.seerr-webhook-secret`: POST without secret → 401 | |
| Pinned subnet 172.28.16.0/24, internal redis net | compose | compose | `floppy.subnet`: → 172.28.16.0/24 172.28.16.x internal-redis=true | |
| Egress: internet 80/443 only, no LAN/Docker | `_global/scripts/docker-user-guard.sh` | `sudo docker-user-guard.sh --persist` | `floppy.egress-lan-blocked`: → blocked; `floppy.egress-internet`: tmdb 443 → open | subnet change escapes the rule |
| Ingress: fastpi + jellyfin subnet only (webhooks via 192.168.1.102:8012) | docker-user-guard.sh | docker-user-guard.sh | `floppy.ingress-narrowed`: → jellyfin=open seerr=open sonarr=blocked | |
| ABS progress pushed, no ABS pull: no recurring ABS import task, account rows hold a placeholder, not a key; one ABS service key (`floppy-sync`, admin, root-only on beefy) syncs every ABS user with a same-named Floppy user | `scripts/abs-push*.py`, `systemd/floppy-abs-push.*`, key `secrets/floppy-sync-abs-key`, exclude `/etc/floppy-abs-push/exclude` | README "Audiobookshelf push" | `floppy.abs-pull-off`: → 0 tasks 0 keys; `floppy.abs-push-selftest`: → ok dry-run; `floppy.abs-push-timer`: → enabled success; `floppy.abs-push-key`: → root:root 600 | a Floppy update renaming the importer internals it calls (`AudiobookshelfImporter.import_data`, `client.get_me/get_library_item`, `decrypt_or_raise`, `AudiobookshelfAccount`, `image_cache.store_cover`/`MAX_IMAGE_BYTES`, cover key `abs-cover:<account>:<item>`): selftest fails → rollback (tier notify); ABS moving `/api/users/:id` mediaProgress |

## Antivirus

| Customization | Where | Reapply | Verify | Update can break |
|---|---|---|---|---|
| Local build, clamd on TCP 3310 | `Antivirus/clamav/` | `docker compose up -d --build` | `av.local-build`: → clamav-local; `av.clamd-tcp`: zPING → PONG | |
| Queue worker heartbeat | compose clamav-queue | compose | `av.queue-worker`: heartbeat < 15 min → alive | |
| Queue + quarantine dirs, group media 2770 | `Antivirus/install.sh` | install.sh | `av.queue-perms`: → 65112:1100 2770 n:1100 2770 | uid change |
| Scan timers | `Antivirus/systemd/` | install.sh | `av.timers`: → active active | |

Hooks in SABnzbd / qBittorrent: Movie-Downloads `md.sab-clamav-script`, `md.qbit-clamav-autorun`.

## Dependency-Track

| Customization | Where | Reapply | Verify | Update can break |
|---|---|---|---|---|
| OIDC login (Authentik app dt-login) | `.env` ALPINE_OIDC_* | compose | `dt.oidc-available`: → true; `dt.frontend-oidc`: config.json → issuer dt-login + client id | env renamed |
| Team mapping homelab-admins → Administrators | DT DB | `Dependency-Track/scripts/oidc-team-mapping.sh` | `dt.team-mapping`: psql in dt-db → homelab-admins pipe Administrators | schema migration |
| Reaches sso (extra_hosts + DOCKER-USER rule) | compose, docker-user-guard.sh | both | `dt.sso-reach`: → 192.168.1.2 200 | |

## Cup-Agent

| Customization | Where | Reapply | Verify | Update can break |
|---|---|---|---|---|
| Agent API for fastpi's Cup | `Cup-Agent/cup.json` | compose | `cup.api`: /api/v3/json → n images | API path change |
| Read-only socket proxy | compose | compose | `cup.proxy-read-only`: env → BUILD=0 EXEC=0 NETWORKS=0 POST=0 VOLUMES=0 | |

Portainer-Agent: no customization (the tag must match fastpi's Portainer, notify tier).

## Host

Only in `post-update-check.sh all` (no compose project).

| Customization | Where | Reapply | Verify | Update can break |
|---|---|---|---|---|
| DOCKER-USER: containers cannot reach the LAN | `_global/scripts/docker-user-guard.sh` | `sudo docker-user-guard.sh --persist` | `host.docker-user-lan`: sonarr → fastpi:22 blocked | docker restart with stale rules |
| BEEFY-INPUT: containers cannot reach the host | docker-user-guard.sh | same | `host.beefy-input`: sonarr → bridge gateway :22 blocked | |
| Allowed egress jellyfin subnet → sso | docker-user-guard.sh | same | `host.sso-egress`: jellyfin → sso discovery 200 | |
| Idle watcher running, metrics fresh | `Server/8-Idle-Watcher` | its install.sh | `host.idle-watcher`: → active fresh probe_error=0 | |
| Maintenance window, auto-update, image cleanup timers | `Server/9-Night-Schedule`, `_global/systemd` | install scripts | `host.timers`: → enabled enabled enabled | |

## Drift found 2026-10-27 (docs vs live, not changed)

| Item | Live | Docs |
|---|---|---|
| description-scout.service | installed copy differs from `Movie-Downloads/systemd/` in Description only | reinstall when touched |
| ryot-db-backup user timer | unit files present, disabled | Ryot retired |
