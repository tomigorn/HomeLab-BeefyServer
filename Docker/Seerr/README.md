# Seerr — requests for films & series

[Seerr](https://docs.seerr.dev) (Overseerr and Jellyseerr merged, v3) lets everyone
with Jellyfin access browse TMDB and request a film or a series. Approved requests
go to Radarr/Sonarr (`../Movie-Downloads`), availability comes from Jellyfin.
Public at `https://requests.holy-grail.ch` (wakes beefy, ~45 s).

Jellyseerr itself is frozen at 2.7.3 (`fallenbagel/jellyseerr:2.7.3-deprecated`); the
successor image is `ghcr.io/seerr-team/seerr`. **Temporarily** this runs the
`preview-new-oidc` build for the Holy Grail login, pinned by digest (see
[Preview image](#preview-image-temporary)).

## Deploy

```bash
sudo mkdir -p /srv/appdata/seerr && sudo chown 65109:1000 /srv/appdata/seerr && sudo chmod 2770 /srv/appdata/seerr
cd ~/Projects/Docker/Seerr && sudo docker compose up -d
curl -s http://127.0.0.1:5055/api/v1/status     # preview: {"version":"develop-2d1a2ed...",...}
```

| | |
|---|---|
| Port | `5055` on beefy, reachable from fastpi only (DOCKER-USER rule); owned by `seerr-proxy` (nginx, [Indexer bar](#indexer-bar)), Seerr itself publishes nothing |
| Networks | joins `movie-downloads` and `jellyfin` (external): `radarr:7878`, `sonarr:8989`, `jellyfin:8096`; `seerr-front` (own) with `seerr-proxy`. Default route stays on `jellyfin` (`gw_priority`): only that subnet may reach sso on fastpi |
| Config | `/srv/appdata/seerr` (`settings.json` holds the API key and the Jellyfin token; `db/db.sqlite3`) |
| Traefik / Authentik | `HomeLab-FastPi` -> `Traefik/traefik/dynamic/requests.yml`; Authentik apps `requests` (forward auth) and `requests-login` (OIDC, hidden tile), group **`streaming-requesters`** (`Authentik/scripts/provision-media-groups.py`) |
| OIDC client | `SEERR_OIDC_CLIENT_ID` / `SEERR_OIDC_CLIENT_SECRET` in `.env` (gitignored), written from fastpi by the provisioning run (`HomeLab-FastPi` `Authentik/README.md`); `scripts/configure.sh` puts them into `settings.json` |

## How the login works

| Layer | What |
|---|---|
| 1. Holy Grail gate | Traefik forward auth, Authentik app `requests`, group `streaming-requesters` (by hand, not automatic on invite). Nobody else reaches Seerr. Stays on. |
| 2. Seerr session | **Holy Grail** button (OIDC, provider slug `holy-grail`, Authentik app `requests-login`). Passes silently: the gate already holds the Holy Grail session. |
| Fallback | none since 2026-10-19: `mediaServerLogin=false` hides the Jellyfin form and Quick Connect, Holy Grail is the only button. Recovery: set it back to true in `settings.json` with Seerr stopped. |

- New Holy Grail users get a Seerr account on first sign-in (`newUserLogin: true`, default permissions).
- An existing Seerr user (e.g. the owner, a Jellyfin-type user) must **link** first, see below. Signing in with Holy Grail before linking creates a second, separate account (Jellyfin users have no real email in Seerr, so nothing matches); if the Holy Grail email already belongs to a Seerr user, the sign-in is refused ("You do not have permission").
- The Jellyfin form and Quick Connect are hidden (`mediaServerLogin=false`, done after the owner was linked). Linking a further pre-existing user needs it on temporarily: edit `settings.json` with Seerr stopped, link, set it back.
- Local (email + password) login is off (`localLogin: false`). Recovery is shell-only via the API key.

### Link an existing account (owner: once)

1. With `mediaServerLogin=true` (temporarily, see above): `https://requests.holy-grail.ch` -> **Quick Connect**, approve the code in Jellyfin as that user.
   While linking, set the provider's `newUserLogin` to false too, otherwise a Holy Grail press from a tab without a Seerr session creates a duplicate account (happened twice on 2026-10-19).
2. Avatar (top right) -> **Settings** -> **Linked Accounts** (`/profile/settings/linked-accounts`) -> **Link Account** -> **Holy Grail**.
3. Back on Linked Accounts, "Holy Grail" is listed.
4. Sign out, then **Holy Grail** on the login page: same user (admin, your requests).

Check from the shell:

```bash
sudo sqlite3 /srv/appdata/seerr/db/db.sqlite3 "SELECT l.userId, l.provider, l.username FROM linked_accounts l;"
```

A duplicate account (signed in before linking): delete it under Users as admin, then link.

Avatar of a Jellyfin-type user (the owner): Seerr refreshes it only on a Jellyfin login, which no longer happens, so `avatarVersion` stays empty and the proxy serves the grey fallback. One-time fix applied 2026-10-19: `sqlite3 db/db.sqlite3 "UPDATE user SET avatarVersion=1 WHERE id=1"`; the proxy then serves Jellyfin's current image (the Holy Grail avatar via the OIDC picture claim). After changing the avatar: hard-refresh; if it stays old, delete `cache/images/avatar`. Holy Grail-created users get the `picture` claim directly.

### Preview image (temporary)

Seerr 3.5.0 has no OIDC. It lives in the open PR seerr-team/seerr#2715, published as `ghcr.io/seerr-team/seerr:preview-new-oidc`; Authentik's Seerr guide uses the same tag.

| | |
|---|---|
| Image | `preview-new-oidc@sha256:28744b1ced522beb7af8e5c966b73c6e46b1dc2c4c8cb497ad2f12d3139699a3` (commit `2d1a2ed`, built 2026-09-08) |
| Base | older than v3.5.0 (~v3.4.1 + OIDC): v3.5.0 fixes are missing until OIDC ships in a release |
| DB | adds the `linked_accounts` table; v3.5.0's migrations stay applied |
| Config | OIDC exists only in `settings.json` (no UI, no API): `main.oidcLogin`, `oidc.providers[]`, `network.trustProxy`, written by `scripts/configure.sh` with Seerr stopped |
| Backup before the switch | `~/backups/seerr-2026-10-19.tgz` (`/srv/appdata/seerr`, v3.5.0 state) |
| Cup | lists `preview-new-oidc` (flags a rebuilt preview) and still `v3.5.0` (flags new releases). Keep the unused `v3.5.0` image for that. `preview-new-oidc` is a local tag on the pinned image; redo after a re-pin: `sudo docker tag ghcr.io/seerr-team/seerr@sha256:<digest> ghcr.io/seerr-team/seerr:preview-new-oidc` |

### Back to stable

When a release's notes include OIDC (PR #2715 merged):

```bash
cd ~/Projects/Docker/Seerr
# docker-compose.yaml: image: ghcr.io/seerr-team/seerr:vX.Y.Z, drop the "Temporary" comment
sudo docker compose pull && sudo docker compose up -d
./scripts/configure.sh          # re-applies OIDC, Radarr/Sonarr, main settings
```

Linked accounts stay in the DB. If the release configures OIDC differently (UI/API), adapt `configure.sh`. Back to plain v3.5.0 instead: same steps with `v3.5.0`; the Holy Grail button disappears, Quick Connect remains.

## First login (one-time, by hand) — done 2026-10-19, owner is tomigorn; friends auto-approve + advanced requests (defaultPermissions 8352)

Seerr's **first** user becomes the owner and must sign in with a Jellyfin
**username + password** (Quick Connect is refused until a user exists, and the
settings API needs that user). Jellyfin is SSO-only, so open a short password
window for your own account, then close it again. On beefy:

```bash
cd ~/Projects/Docker/Jellyfin && sudo docker compose stop jellyfin
C=/srv/appdata/jellyfin/data
sudo sqlite3 $C/data/jellyfin.db ".backup $C/data/jellyfin.db.bak-pre-seerr"
sudo sqlite3 $C/data/jellyfin.db "SELECT quote(Password), quote(AuthenticationProviderId) FROM Users WHERE Username='tomigorn';" | sudo tee /root/jf-auth.sav
sudo sqlite3 $C/data/jellyfin.db "UPDATE Users SET AuthenticationProviderId='Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider' WHERE Username='tomigorn';"
sudo docker compose start jellyfin
# set a temporary password (API key: secrets/api-key in the Jellyfin project), user id from GET /Users
JK=$(cat ~/Projects/Docker/Jellyfin/secrets/api-key); U=<your user id>
curl -H "Authorization: MediaBrowser Token=$JK" -H 'Content-Type: application/json' -X POST "http://127.0.0.1:8096/Users/$U/Password" -d '{"CurrentPw":"","NewPw":"<temp>","ResetPassword":false}'
```

Then `https://requests.holy-grail.ch/setup`: media server **Jellyfin**, hostname
`jellyfin`, port `8096`, your Jellyfin username + the temporary password. Finish
the wizard or stop after the login and run `scripts/configure.sh` (Jellyfin
libraries, Radarr and Sonarr entries incl. the Sonarr anime profile (`Anime - Bluray +
WEB 1080p`, series type `anime`, same root `/data/media/tv`; see `../Movie-Downloads/README.md`
"Languages"), app URL, initialize - it reads the keys from
`/srv/appdata`). Finally close the window:

```bash
cd ~/Projects/Docker/Jellyfin && sudo docker compose stop jellyfin
PW=$(sudo cut -d'|' -f1 /root/jf-auth.sav); PR=$(sudo cut -d'|' -f2 /root/jf-auth.sav)
sudo sqlite3 $C/data/jellyfin.db "UPDATE Users SET Password=$PW, AuthenticationProviderId=$PR WHERE Username='tomigorn';"
sudo rm /root/jf-auth.sav && sudo docker compose start jellyfin
# every user must show OidcPasswordDisabledProvider again:
curl -s -H "Authorization: MediaBrowser Token=$JK" http://127.0.0.1:8096/Users | grep -o '"AuthenticationProviderId":"[^"]*"'
```

The Seerr session token survives the restore (it is a Jellyfin session, not a
password), and Seerr created its own Jellyfin API key ("Seerr") during the login.

## What `scripts/configure.sh` sets

| Setting | Value |
|---|---|
| Jellyfin | `http://jellyfin:8096`, external `https://jellyfin.holy-grail.ch`, libraries Movies + Shows enabled |
| Radarr | `radarr:7878`, profile **4K HDR - UHD Bluray + WEB (1080p fallback)**, root `/data/media/movies`, min availability released, default |
| Sonarr | `sonarr:8989`, profile **4K HDR - WEB-2160p (1080p fallback)**, root `/data/media/tv`, season folders, default |
| App | URL `https://requests.holy-grail.ch`, title "Requests" |
| Login | Holy Grail OIDC (`oidcLogin: true`, provider `holy-grail`, new users allowed, logo `holy-grail-logo.png`), Jellyfin form and Quick Connect off (`mediaServerLogin: false`), local login off (`localLogin: false`) |
| Network | `trustProxy: true` (Traefik on fastpi in front; Authentik's guide asks for it) |

Permissions: the owner (you) is admin (implies every permission). `configure.sh` adds to the default permissions of new users, keeping what is set:

| Bit | Permission | Effect |
|---|---|---|
| 32 | `REQUEST` | may request |
| 128 | `AUTO_APPROVE` | requests go straight to Radarr/Sonarr |
| 8192 | `REQUEST_ADVANCED` | advanced options in the request dialog (profile, root folder) |

Applies to users created afterwards; an existing user: Settings -> Users -> edit -> Advanced Requests. Change the defaults under Settings -> Users -> Default Permissions. Notifications (Telegram,
email) are not set up: Settings -> Notifications.

## How to request

| Want | Do |
|---|---|
| a film or series | **Request**, leave the profile on the default (4K HDR, 1080p fallback) |
| it now, not tonight | untick **Can wait — download tonight (cheaper power)** ([Can wait](#can-wait)) |
| an old or niche title | nothing: on add it moves to **Any quality - old & rare (upgrades to 4K)** by itself (released ≤ 12 years ago, or ≥ 2 years old with few votes) |
| force Any quality for a new title | **Advanced** → Quality Profile **Any quality - old & rare (upgrades to 4K)** |
| a must-have audio language | **Advanced** → Tags `audio-<language>`: not grabbed without it |
| Swiss German (Mundart), hard | **Advanced** → Tags `audio-swiss-german` (or, for a title not yet in Radarr/Sonarr, Quality Profile **Swiss German (Mundart) only - any quality**: same effect): only Swiss German, any quality, also over a better High German; none = nothing grabbed, and after 7 days one Telegram "Swiss German not found" |
| extra subtitles | **Advanced** → Tags `subs-<language>`: Bazarr fetches them on top of EN + DE + original |

**Advanced** needs `REQUEST_ADVANCED` (above). Any profile other than the default (or its English twin) is kept as picked. Details, exact rules and the full tag list: `../Movie-Downloads/README.md`, "Old and rare titles" and "Per-request audio and subtitles".

| Example | Tags |
|---|---|
| Swiss film in Swiss German | `audio-swiss-german` (release name must say `SwissGerman`/`Swiss.German`/`Schweizerdeutsch`/…; Swiss German subtitles do not exist) |
| Foreign film, Swiss German dub | `audio-swiss-german` (e.g. *Babe*: `…German.Schwyzerduetsch…DVDRip`) |
| English film, also with German audio | `audio-german` |
| Korean film with French subtitles | `subs-french` (Korean subs come anyway) |
| Anime with English dub and Italian subs | `audio-english` + `subs-italian` |

- Seerr's tag picker shows the Radarr/Sonarr tag label as the option text, nothing else (checked 2026-10-23 in the running image, `src/components/RequestModal/AdvancedRequester/index.tsx`: `label: tag.label`); Seerr has no per-tag description, no custom help text and no setting for one. Labels allow only `a-z 0-9 -`, so "Swiss German (Mundart)" and the hint "Hard filter — only this audio, everything else is skipped." cannot be shown there. Possible only by patching Seerr (not done). The friendly names: `../Movie-Downloads/README.md` "Per-request audio and subtitles"; the Quality Profile dropdown shows the full name **Swiss German (Mundart) only - any quality**.
- Films and series behave the same (tags, Swiss German profile, description scout, not-found alert, Remove/Clear Data): parity table in `../Movie-Downloads/README.md`, "Radarr / Sonarr parity".
- Requesting a film that is already in Radarr/Sonarr: Seerr drops the tags ("Movie is already monitored in Radarr"); `../Movie-Downloads/scripts/request-tags.sh` (timer, every 2 min) copies them over and searches.
- `lang-xx` and `bz-…` tags are set automatically: do not pick them.
- `audio-` is a hard filter on the release name: no matching release = nothing is grabbed (Radarr/Sonarr → Activity / Interactive Search shows "Does not contain one of the required terms").
- Tags changed after the request: add or change the tag (or profile) in Radarr/Sonarr, search; for `subs-` and `audio-swiss-german` tags run `../Movie-Downloads/scripts/language-policy.sh` once.

## Indexer bar

A thin bar at the top of every Seerr page (everyone who can open Requests sees it): limited indexers used / allowed, subscription deadlines; **more** lists everything. Collapsible (remembered per browser). Docs for users: wiki `apps/requests.md` "The indexer bar at the top" (HomeLab-FastPi).

| Part | What |
|---|---|
| `seerr-proxy` | `nginxinc/nginx-unprivileged`, uid 65124, read-only, no caps, 64 MB. Takes host port `5055`, proxies everything to `seerr:5055` (websockets too), adds `<link>` + `<script>` for `/_indexer-bar/bar.css` + `bar.js` (and `can-wait.js`, `menu-links.js`: [Can wait](#can-wait), [Menu links](#menu-links)) before `</head>` (`sub_filter`, Seerr's gzip off, nginx compresses again). Seerr down (just woke): 503 page that reloads itself. Config `indexer-bar/nginx.conf` |
| `/_indexer-bar/` | `indexer-bar/www/` (bar.js, bar.css, starting.html, can-wait.js, menu-links.js); `usage.json` from `/srv/appdata/indexer-bar/` |
| Collector | `scripts/indexer-usage.py` as buntu, `systemd/indexer-usage.timer` every minute: Prowlarr `GET /api/v1/indexer` + `/api/v1/history/since`, counts queries + RSS (searches) and grabs (downloads) per indexer in its window. Writes names and counts only, no keys, no queries |
| Limits + deadlines | `indexer-bar/limits.toml` (committed, no secrets); picked up on the next run, no restart |
| Colours | limits: green < 70 %, amber 70-95 %, red ≥ 95 % with the reset time. Subscriptions: yellow ≤ 30 days left, orange ≤ 7, red on the end date and after, `lifetime` without countdown (same stages as the Telegram alerts) |
| Windows | `rolling`: last 24 h, resets when the oldest counted event ages out (Prowlarr's own rule). `day`: calendar day Europe/Zurich, resets at midnight (DST-aware) |
| Failure | Prowlarr unreachable: "usage unavailable", subscriptions still shown. usage.json older than 10 min or missing: "usage unavailable". The bar never blocks Seerr (own script, all errors caught) |

Change a limit or renew a subscription: edit `indexer-bar/limits.toml`, commit.

Telegram warnings for the deadlines run on fastpi (beefy sleeps): 🟡 30 days, 🟠 7 days, 🔴 end date, 🔴 the day after, each once per end date. fastpi keeps a cached copy of `limits.toml` (refreshed over ssh whenever beefy is awake). Renewing = change `expires` and commit: the old date is never warned about again, the new date starts fresh. Details: `HomeLab-FastPi` `Docker/_global/scripts/README.md` "subscription-alerts".

```toml
[[subscriptions]]
name = "Eweka"
kind = "Usenet provider"
expires = 2026-12-18     # the day it ends; or: lifetime = true
```

Keep the indexer limits equal to Prowlarr's own Query/Grab Limit (`../Movie-Downloads/README.md` "Indexers").

Install (once):

```bash
sudo install -d -o buntu -g buntu -m 0755 /srv/appdata/indexer-bar
sudo install -m 0644 systemd/indexer-usage.service systemd/indexer-usage.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now indexer-usage.timer
sudo docker compose up -d
```

| Check | Command |
|---|---|
| Tests | `python3 tests/test_indexer_usage.py` |
| Injected | `curl -sL localhost:5055/ \| grep _indexer-bar` |
| Data | `scripts/indexer-usage.py && python3 -m json.tool /srv/appdata/indexer-bar/usage.json \| head` |
| Timer | `systemctl list-timers indexer-usage.timer; journalctl -u indexer-usage -n 5` |

Remove the bar: delete the `seerr-proxy` service, put `ports: ["${SEERR_PORT}:5055"]` back on `seerr`, `sudo docker compose up -d --remove-orphans`, `sudo systemctl disable --now indexer-usage.timer`.

## Can wait

The request dialog has a checkbox, ticked by default: ☑ **Can wait — download tonight (cheaper power)**. Ticked: the download is added paused and starts in the low tariff (22:00-06:00, all Sunday). Unticked: the request carries the Radarr/Sonarr tag `download-now` and downloads at once (first download only). Everyone who may request may untick it. Mechanism: `../Movie-Downloads/README.md` "Can wait: downloads at night".

| Part | What |
|---|---|
| Script | `indexer-bar/www/can-wait.js`, injected by `seerr-proxy` next to `bar.js` |
| Box | added to a dialog whose title is "Request Movie/Series/Collection (in 4K)" or the German one ("Film anfragen" …), above the buttons; MutationObserver |
| Tag | wraps `XMLHttpRequest`: an unticked box adds the `download-now` tag id of the request's Radarr/Sonarr server (`/api/v1/service/<radarr\|sonarr>`, default server for `is4k` when none is picked) to `tags` of `POST /api/v1/request`; tags picked under Advanced and server default tags stay |
| Fallback | Seerr changed its dialog, tag missing, API error: no box or no tag, the request waits for the night. **Advanced → Tags → `download-now`** does the same as unticking |
| After a Seerr update | open a request dialog: the box must be there |

## Menu links

`indexer-bar/www/menu-links.js` (injected like `can-wait.js`) adds **Audiobooks** after Series in the desktop sidebar, the phone drawer and the phone bottom bar (5th item, while it has room and no "more" button; otherwise in the "more" list), opening `https://books.holy-grail.ch/` (AudioBookRequest on fastpi, LAN/VPN, group `streaming-requesters`) in the same tab. Links and URL: `LINKS` at the top of the file. Seerr without a `/discover/tv` link: no entry, nothing breaks.

| Check | Command |
|---|---|
| Tests (jsdom, Node ≥ 20) | `cd tests/js && npm ci && node --test` |
| Injected | `curl -sL localhost:5055/ \| grep -o '_indexer-bar/[a-z-]*\.js'` |
| After editing `indexer-bar/nginx.conf` | `sudo docker restart seerr-proxy` (a single-file bind mount keeps the old file on reload); scripts in `www/` are live at once |

## Delete a title

Title page → **Manage** (admin):

| Button | What goes |
|---|---|
| **Remove from Radarr/Sonarr** | the title in Radarr/Sonarr (with its files), nothing else |
| **Advanced → Clear Data** | everything: Seerr request, Radarr/Sonarr entry + files, qBittorrent torrent + files, SABnzbd history and Jellyfin (within ~2 min) |

- A later request after Clear Data starts completely fresh (new search, new download).
- The library file sits 7 days in the recycle bin.
- How it works (`clear-data-wipe.sh` timer): `../Movie-Downloads/README.md` "Delete a title".

## Tracker (Floppy) webhook
Seerr's Webhook agent posts pending, approved, auto-approved and available requests to Floppy (`http://192.168.1.102:8012/webhook/seerr/global/`, beefy-local, never Traefik). Floppy adds the title as Planning for every member who opted in with that Seerr name (`../Floppy/README.md` "Integrations"). The payload carries the shared secret `../Floppy/secrets/floppy_seerr_webhook_secret`.

| Task | Command (in `scripts/`) |
|---|---|
| Apply / after a secret change | `python3 seerr-webhook.py apply` |
| Show | `python3 seerr-webhook.py show` → `True 142 http://192.168.1.102:8012/webhook/seerr/global/` |
| Test | `python3 seerr-webhook.py test`, Floppy log: `Missing requester` (secret OK) |
| Off | Settings → Notifications → Webhook → disable |

## Scheduled jobs

Seerr's own jobs run in beefy's maintenance window (`Server/9-Night-Schedule.md`), not at night while beefy sleeps. `scripts/night-jobs.py` sets them through the API (idempotent, `--dry-run`; tests `python3 -m unittest test_night_jobs`; stored in Seerr's settings, so re-run it after a fresh install).

| Job | Time |
|---|---|
| radarr-scan / download-sync-reset / sonarr-scan | 04:00 / 04:03 / 04:05 |
| jellyfin-full-scan / availability-sync / image-cache-cleanup | 04:22 / 04:30 / 04:32 |
| process-blocklisted-tags | Sunday 04:34 |
| download-sync (1 min), jellyfin-recently-added-scan (5 min) | live, unchanged |

## Related

- `../Movie-Downloads` — Radarr, Sonarr, Prowlarr, download clients, Bazarr, Recyclarr
- `../Jellyfin` — playback; libraries `/media/movies` and `/media/tv`
