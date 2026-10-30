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
| Networks | joins `movie-downloads` and `jellyfin` (external): `radarr:7878`, `sonarr:8989`, `jellyfin:8096`; `seerr-front` (own) with `seerr-proxy` and `download-now`. Default route stays on `jellyfin` (`gw_priority`): only that subnet may reach sso on fastpi; `extra_hosts` pins `sso.holy-grail.ch` to 192.168.1.2 so OIDC stays on the LAN |
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
- New Jellyfin users never get an account (`newPlexLogin: false`, 2026-10-30): Quick Connect's API ignores `mediaServerLogin`, so with it on any Jellyfin user who can reach `:5055` directly could create a Seerr account past the group gate. OIDC new users are governed by the provider's `newUserLogin`, not this.

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

Avatars: a pick in the Avatar Picker reaches Seerr without a new login, pushed from fastpi while beefy is up (`HomeLab-FastPi` `Avatar-Picker/README.md`, "How a pick reaches other apps"). Seerr's API cannot set an avatar, so that job updates the one `user` row as uid 65109: Jellyfin-type user (the owner): new `avatarVersion` (`/avatarproxy/<id>?v=<version>`) + empty `cache/images/avatar`; Holy Grail-created user: the new picker URL. Backup before the first run: `/srv/appdata/seerr/db.bak-avatar-sync-20261027.sqlite3`.

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

## First login (one-time, by hand) — done 2026-10-19, owner is jane; friends auto-approve + advanced requests + view requests (defaultPermissions 24736)

Seerr's **first** user becomes the owner and must sign in with a Jellyfin
**username + password** (Quick Connect is refused until a user exists, and the
settings API needs that user). Jellyfin is SSO-only, so open a short password
window for your own account, then close it again. On beefy:

```bash
cd ~/Projects/Docker/Jellyfin && sudo docker compose stop jellyfin
C=/srv/appdata/jellyfin/data
sudo sqlite3 $C/data/jellyfin.db ".backup $C/data/jellyfin.db.bak-pre-seerr"
sudo sqlite3 $C/data/jellyfin.db "SELECT quote(Password), quote(AuthenticationProviderId) FROM Users WHERE Username='<admin-user>';" | sudo tee /root/jf-auth.sav
sudo sqlite3 $C/data/jellyfin.db "UPDATE Users SET AuthenticationProviderId='Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider' WHERE Username='<admin-user>';"
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
sudo sqlite3 $C/data/jellyfin.db "UPDATE Users SET Password=$PW, AuthenticationProviderId=$PR WHERE Username='<admin-user>';"
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
| Login | Holy Grail OIDC (`oidcLogin: true`, provider `holy-grail`, new users allowed, logo `holy-grail-logo.png`), Jellyfin form and Quick Connect off (`mediaServerLogin: false`), no new Jellyfin/Quick Connect accounts (`newPlexLogin: false`), local login off (`localLogin: false`) |
| Network | `trustProxy: true` (Traefik on fastpi in front; Authentik's guide asks for it) |

Permissions: the owner (you) is admin (implies every permission). `configure.sh` adds to the default permissions of new users, keeping what is set:

| Bit | Permission | Effect |
|---|---|---|
| 32 | `REQUEST` | may request |
| 128 | `AUTO_APPROVE` | requests go straight to Radarr/Sonarr |
| 8192 | `REQUEST_ADVANCED` | advanced options in the request dialog (profile, root folder) |
| 16384 | `REQUEST_VIEW` | sees everyone's requests (Requests list, who requested) — owner 2026-10-31; existing users 5 and 7 set by hand the same day |

Never: `MANAGE_REQUESTS` (16) or `ADMIN` (2) for anyone but the owner (user 1). Check: `seerr.permissions` (post-update) = default has 16384, every human user has it, only user 1 has 16/2.

Applies to users created afterwards; an existing user: Settings -> Users -> edit -> Advanced Requests. Change the defaults under Settings -> Users -> Default Permissions. Notifications (Telegram,
email) are not set up: Settings -> Notifications.

## How to request

| Want | Do |
|---|---|
| a film or series | **Request**, leave the profile on the default (4K HDR, 1080p fallback) |
| it now, not tonight | untick **Can wait — download tonight (cheaper power)** ([Can wait](#can-wait)) |
| it now, at daytime power price (already requested: your own request, or any title for admins) | title page → **⚡ Download now — at daytime power price** ([Download now](#download-now)) |
| an old or niche title | nothing: on add it moves to **4K preferred · older versions allowed** by itself (released ≤ 12 years ago, or ≥ 2 years old with few votes) |
| force the older-versions profile for a new title | **Advanced** → Quality Profile **4K preferred · older versions allowed** |
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
| `seerr-proxy` | `nginxinc/nginx-unprivileged`, uid 65124, read-only, no caps, 64 MB. Takes host port `5055`, proxies everything to `seerr:5055` (websockets too), adds `<link>` + `<script>` for `/_indexer-bar/bar.css` + `bar.js` (and `can-wait.js`, `menu-links.js`, `night-label.js`, `download-now.js`, `my-request.js`, `request-sizes.js`, `request-origin.js`: [Can wait](#can-wait), [Menu links](#menu-links), [Night label](#night-label), [Download now](#download-now), [My request](#my-request), [Request sizes](#request-sizes), [Request origin](#request-origin)); `/_download-now/` goes to the `download-now` backend before `</head>` (`sub_filter`, Seerr's gzip off, nginx compresses again). Seerr down (just woke): 503 page that reloads itself. Config `indexer-bar/nginx.conf` |
| Jellyfin sign-in blocked | `/api/v1/auth/jellyfin` and below (password login, Quick Connect initiate/check/authenticate) answer 403 in nginx (case-insensitive); users sign in via OIDC (`/api/v1/auth/oidc/…` untouched). Quick Connect ignores `mediaServerLogin`, so a Jellyfin-linked account could otherwise get a session without the SSO step. Gap: a client on a docker network reaching `seerr:5055` directly skips the proxy. Check: `seerr.jellyfin-auth-blocked` |
| Session gate | `usage.json`, `night-waiting.json`, `request-state.json` and `/_download-now/` (not `/health`) need a Seerr session: nginx `auth_request` asks Seerr `GET /api/v1/auth/me` with the caller's own cookie (or `X-Api-Key`/`X-Api-User`), cached 10 s per credentials; none/invalid = 401. The bar scripts render only after a 200, never on `/login`, `/setup`, `/resetpassword`, and re-check after client-side navigation (login without reload). Check: `seerr.data-needs-session` |
| Shared code | `bar.js` + `bar.css` are one codebase with the requests bar above AudioBookRequest (fastpi, HomeLab-FastPi `Docker/Audiobook-Requests`): canonical here, vendored there unchanged; config = attributes on the `<script>` tag, app wording (disk hints, panel note) comes from `usage.json` (`indexer-usage.py`). Format: `indexer-bar/schema.md`. Change here, run both test suites, then on fastpi `Docker/Audiobook-Requests/scripts/sync-bar.sh` (fastpi's post-update check `books.bar-shared` compares the hashes) |
| `/_indexer-bar/` | `indexer-bar/www/` (bar.js, bar.css, starting.html, can-wait.js, menu-links.js, night-label.js, download-now.js); `usage.json` and `night-waiting.json` from `/srv/appdata/indexer-bar/` |
| Collector | `scripts/indexer-usage.py` as buntu, `systemd/indexer-usage.timer` every minute: Prowlarr `GET /api/v1/indexer` + `/api/v1/history/since`, counts queries + RSS (searches) and grabs (downloads) per indexer in its window. Writes names and counts only, no keys, no queries |
| Storage | `SSD` (ssd-hot + ssd-hot2 together) and `HDD` chips: used/total and %, from `os.statvfs` of each branch (`1 − f_bavail/f_blocks`, the mergerfs measure). SSD green < 70 %, yellow 70-80, red ≥ 80; HDD green < 80, yellow 80-90, red ≥ 90. Hover (and **more**): free space, both SSDs' %, and the rule (mover from 70 % down to 60 % with titles unplayed for 30 days, from 80 % new downloads go to the HDD; HDD from 90 % no imports). EN/DE. The collector also writes `~/.local/state/indexer-bar/storage.prom` (sizes only), which fastpi pulls for its alerts |
| Limits + deadlines | `indexer-bar/limits.toml` (committed, no secrets); picked up on the next run, no restart |
| Colours | limits: green < 70 %, amber 70-95 %, red ≥ 95 % with the reset time. Subscriptions: yellow ≤ 30 days left, orange ≤ 7, red on the end date and after, `lifetime` without countdown (same stages as the Telegram alerts) |
| Windows | `rolling`: last 24 h, resets when the oldest counted event ages out (Prowlarr's own rule). `day`: calendar day Europe/Zurich, resets at midnight (DST-aware) |
| Failure | Prowlarr unreachable: "usage unavailable", subscriptions still shown. usage.json older than 10 min or missing: "usage unavailable". The bar never blocks Seerr (own script, all errors caught) |

Change a limit or renew a subscription: edit `indexer-bar/limits.toml`, commit.

Treasure Maps (Supporter until 2031-11-17): no search limit, so only `downloads = 400` is listed (the bar shows downloads used / 400 and the subscription countdown; searches are counted but unlimited). Real limits: Newznab `t=user` (`downloadrequests`). Rules (burst 5 requests/s → 429, per-tier NZB limit, key personal, no redistribution): Movie-Downloads README "Treasure Maps rules".

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

The request dialog has a checkbox, ticked by default: ☑ **Can wait — download tonight (cheaper power)** (German UI: **Kann warten — heute Nacht herunterladen (günstigerer Strom)**). Ticked: the download is added paused and starts in the low tariff (22:00-06:00, all Sunday). Unticked: the request carries the Radarr/Sonarr tag `download-now` and downloads at once (first download only). Everyone who may request may untick it. Mechanism: `../Movie-Downloads/README.md` "Can wait: downloads at night".

| Part | What |
|---|---|
| Script | `indexer-bar/www/can-wait.js`, injected by `seerr-proxy` next to `bar.js` |
| Box | added to a dialog whose title is "Request Movie/Series/Collection (in 4K)" or the German one ("Film anfragen" …), above the buttons; MutationObserver |
| Tag | wraps `XMLHttpRequest`: an unticked box adds the `download-now` tag id of the request's Radarr/Sonarr server (`/api/v1/service/<radarr\|sonarr>`, default server for `is4k` when none is picked) to `tags` of `POST /api/v1/request`; tags picked under Advanced and server default tags stay |
| Fallback | Seerr changed its dialog, tag missing, API error: no box or no tag, the request waits for the night. **Advanced → Tags → `download-now`** does the same as unticking |
| After a Seerr update | open a request dialog: the box must be there |

## Menu links

`indexer-bar/www/menu-links.js` (injected like `can-wait.js`) adds **Audiobooks** (German UI: **Hörbücher**) after Series in the desktop sidebar, the phone drawer and the phone bottom bar (5th item, while it has room and no "more" button; otherwise in the "more" list), opening `https://books.holy-grail.ch/` (AudioBookRequest on fastpi, public via Cloudflare, Holy Grail login, group `streaming-requesters`) in the same tab. Links and URL: `LINKS` at the top of the file. Seerr without a `/discover/tv` link: no entry, nothing breaks. Only menu links count: `/discover/tv` links inside `<main>` (Discover's **Popular Series** row title) are skipped, otherwise an "Audiobooks" title lands squeezed beside it (fixed 2026-10-28; Discover gets no own Audiobooks row, the menu entry covers it).

| Check | Command |
|---|---|
| Tests (jsdom, Node ≥ 20) | `cd tests/js && npm ci && node --test` |
| Injected | `curl -sL localhost:5055/ \| grep -o '_indexer-bar/[a-z-]*\.js'` |
| After editing `indexer-bar/nginx.conf` | `sudo docker restart seerr-proxy` (a single-file bind mount keeps the old file on reload); scripts in `www/` are live at once |

## Night label

A download held by the night queue ([Can wait](#can-wait)) shows **🌙 Tonight** (German UI: **🌙 Heute Nacht**) in the status pill instead of "Processing" at 0 %, and **Waiting for the night 🌙** in Seerr's download popup (bar kept at 0 %, grey, no animation; patched before the popup paints), with the hint "starts automatically at 22:05 or on Sunday; untick 'Can wait' next time for an immediate download". Real downloads, items paused by hand outside the night categories and failed ones look as before. In the low tariff nothing is labelled (queued night items show Seerr's normal status).

Status icons, for every open request (pending or approved, not available). Every requested title that is not available always has one (state table below):

| Icon | When | Hint (EN / DE) |
|---|---|---|
| 🔍 (drawn magnifier, pulsing; static with reduced motion) | Radarr/Sonarr has a queued or started search for the title (`MoviesSearch` `movieIds`, `SeriesSearch`/`SeasonSearch` `seriesId`; a search over all missing titles counts for every released title without a download) | Searching indexers now (started 13:24) — usually a few minutes / Sucht gerade in den Indexern (seit 13:24) — dauert meist ein paar Minuten; queued: Waiting for other searches (2 ahead) / Wartet auf andere Suchen (2 davor) |
| 🔎 (drawn magnifier, still) | approved, no search since the request yet (also from Seerr's own request data in the first minutes, before the file knows it) | Requested — search starts shortly / Angefragt — die Suche startet gleich; after 5 min: Requested — not searched yet; tonight's scheduled search (04:17) tries |
| ⏳ | request waits for approval | Waiting for approval / Wartet auf Freigabe |
| ⚠️ | Radarr/Sonarr queue: import blocked, failed (`trackedDownloadState` importBlocked/failedPending/failed, status error); Seerr request failed | Problem — see Requests/Activity: <Radarr/Sonarr message, paths and URLs cut> / Problem — siehe Anfragen/Aktivität: …; Seerr: Problem — Seerr could not hand the request to Radarr/Sonarr |
| 🌙 | request is **Can wait** (title without `download-now` in Radarr/Sonarr; not yet there: no `download-now` in the Seerr request), from the request until downloaded; not while Seerr shows a running download | Waiting for the night — downloads at night (cheaper power) / Wartet auf die Nacht — lädt nachts herunter (günstigerer Strom) |
| 🧭 | searched (Radarr `lastSearchTime`; Sonarr: missing aired episodes of the requested seasons), nothing queued, no search running, released | No release found yet — searched again in tonight's scheduled search (04:17) / Noch kein Release gefunden — die geplante Suche heute Nacht (04:17) sucht weiter |
| 📅 | not released: Radarr `isAvailable` false (date: `inCinemas` for minimum availability In Cinemas, else `releaseDate`); Sonarr: none of the missing episodes of the requested seasons has aired (date: next air date), or no episodes yet and status `upcoming` (`nextAiring`) | Not released yet — expected 2 Feb 2027 / Noch nicht erschienen — erwartet 2. Feb. 2027 |
| ◌ (spinning ring) | download in progress (Seerr's own spinner badge replaced; ring is static with reduced motion) | Downloading now — 40 % / Lädt gerade herunter — 40 % |
| ✅ | available (Seerr's green check) | Available in Jellyfin / In Jellyfin verfügbar |
| ◐ (half-filled circle, drawn) | partly available: aired requested episodes still missing (Seerr's green minus replaced) | Partly available / Teilweise verfügbar |
| ◌ from the queue | Radarr/Sonarr downloads or imports while Seerr shows no download yet | Downloading now / Importing… (Wird importiert…) |
| ✅ (downloaded) | file on disk, Seerr not yet available | Downloaded — in Jellyfin shortly / Heruntergeladen — gleich in Jellyfin (minutes: every import triggers a Seerr scan, [Movie-Downloads README](../Movie-Downloads/README.md#seerr-availability-after-an-import)) |
| ✅📅🌙 complete so far | series: every aired requested episode here, only unaired left (or the requested seasons done while Seerr says partly); 📅 next air date, 🌙 if Can wait; ended series: ✅ alone | Everything released so far is in Jellyfin · Next episode expected 11 Nov · downloads at night |

| State of a requested title | Badge |
|---|---|
| waiting for approval | ⏳ (+🌙) |
| requested, no search yet (first minutes) | 🔎 Requested — search starts shortly |
| search queued / running | 🔍 pulsing (+🌙) |
| found, Can wait (paused for the night) | 🌙 |
| found, downloading / importing | ◌ (Seerr's own spinner on pills; cards ours, "Importing…") |
| imported, Seerr not yet available | ✅ Downloaded — in Jellyfin shortly (Seerr flips to Available within about 3 to 13 min of the import) |
| available | ✅ (Seerr's green pill) |
| series: aired episodes missing | ◐ + state of the missing part (🌙 🧭 📅 🔍 ◌) |
| series: complete so far | ✅📅🌙 ("Available so far" pill, green) |
| searched, nothing found | 🧭 (+🌙) |
| not released | 📅 (+🌙) |
| download/import problem, Seerr failed | ⚠️ |

Status icons (◌ ✅ ◐) come first, in the same purple circle/pill. ✅ never combines; ◐ combines with 🌙🧭📅 and with ◌ (series: some episodes here, more downloading now); a download paused for the night shows 🌙 instead of ◌. ◐ hints: 🌙 alone adds "the rest downloads at night", 🧭 reads "searched again tonight (04:17)", 📅 reads "next episode 11 Nov". Overview of all rows: Claude-Mockups `seerr-all-badges`; percent from Seerr's `downloadStatus` sizes, left out when unknown. Same on every card: Discover, search, collections, person pages. Title-page pills stay Seerr's look, but every state carries a hint (below).

Order ⚠️⏳🔍🌙🧭📅, combined. Placements: status badges (title page, request cards, Requests list; after "Requested"/"Pending"), Discover cards (in Seerr's status circle: one icon = the 26px circle, two = a pill). Pills with Seerr's own spinner/progress get nothing of ours (its popup shows the download). **Partially Available** pills are sky blue (`#0284c7`, not Seerr's green) and start with ◐; never ✅ inside. One merged hint on the whole badge / pill, e.g. 🌙🧭 "Waiting for the night · No release found yet — searched again in tonight's scheduled search (04:17), downloads at night when found": shown in a static box anchored to the badge on mouse hover or keyboard focus, for every pill whether Seerr gives it a popup or not (users without Manage Requests get none): above a status pill (below it when Seerr's cursor-following popup opens above), below a card badge; never follows the mouse, nothing of ours inside Seerr's popup, no move handlers, `aria-describedby`, Escape closes; no `title` tooltips; touch only: the first tap shows it, a second tap opens the link or card as usual, mouse clicks go straight through. While a 🔍/🔎 shows, the page reads `request-state.json` every 15 s and swaps the icon in place (spinner, 🧭, 🌙 when the search ends).

**Title-page pill hints** (`night-label.js`, `.media-status`): the pill always has a static hint, also where Seerr's popup exists or no icon of ours applies. Icons of ours (🌙 🔍 ◐ …) merge into the hint as above; otherwise by the pill's text (EN / DE):

| Pill | Symbol | Hint |
|---|---|---|
| Processing + Seerr's spinner | Seerr's spinner | Downloading now — 40 % (the percent is Seerr's own bar behind the pill; none while 0) / Lädt gerade herunter — 40 % |
| Processing, no download | - | Processing — handed to Radarr/Sonarr / Wird verarbeitet — an Radarr/Sonarr übergeben |
| Requested | - | Requested — approved, waiting for the search / Angefragt — freigegeben, wartet auf die Suche |
| Pending | ⏳ | Waiting for approval / Wartet auf Freigabe |
| Available | ✅ | Available in Jellyfin / In Jellyfin verfügbar |
| Partially available | ◐ | Partly available (+ missing part's state) |
| Declined | - | Declined — an admin refused the request / Abgelehnt — ein Admin hat die Anfrage abgelehnt |
| Unknown, Deleted | - | Unknown — Seerr has no state for it · Deleted — removed from Jellyfin (DE: Unbekannt — …, Gelöscht — …) |

| Part | What |
|---|---|
| Data | `/_indexer-bar/night-waiting.json` (`waiting`: download ids), written every 2 min by `../Movie-Downloads/scripts/night-downloads.py` to `/srv/appdata/indexer-bar/`; empty in the low tariff |
| Icon data | `/_indexer-bar/request-state.json` (`canWait`: `movie\|tv/<tmdb id>` list, `unreleased`: id → date or null, `noRelease`: id → last search, `searching`: id → `{status: started, started}` / `{status: queued, ahead}`, `fresh`: id → request time or null, `pending`, `imported`, `soFar`: lists, `queue`: id → `{state: paused\|downloading\|importing\|problem, message}`), same script; reads Seerr `/api/v1/request` with `secrets/api-key` and Radarr/Sonarr; ids and short Radarr/Sonarr status messages (paths and URLs cut), no user data. Every 2 min with the night run; `--request-state` (`../Movie-Downloads/systemd/request-state.timer`, every 15 s) rewrites it only while a search runs or a request is from the last 5 min (2 commands + 1 Seerr call otherwise). Seerr or Radarr/Sonarr not readable: file kept; older than 10 min = no icons. Visible to every Seerr session (all titles, not only the viewer's): accepted, it holds title and request ids, states and sizes only, no user names or ids, and Seerr itself shows every title's request status on its cards to everyone |
| Match | Seerr's `downloadStatus` item: `status` `paused` **and** `downloadId` in `waiting`; a badge only when every download behind it waits |
| Script | `indexer-bar/www/night-label.js`: status badge (title page, request cards, Requests list), Discover card status circle → 🌙/🧭/📅 and ◌/✅/◐ (downloading/available/partly), download blocks (Manage panel, badge tooltip: label, bar grey). Titles from Seerr's own API responses (read-only) or `GET /api/v1/movie|tv/<id>` |
| Fallback | file missing or older than 10 min, API error, Seerr changed its markup: Seerr's normal display |
| Check | `curl -s localhost:5055/_indexer-bar/night-waiting.json \| head -c 200`, `curl -s localhost:5055/_indexer-bar/request-state.json`; tests in [Menu links](#menu-links) (`node --test`) |

## Download now

**⚡ Download now — at daytime power price** (German UI: **⚡ Jetzt herunterladen — zum Tagstrompreis**) sits on the title page, first in the button row next to Request/Play (amber, Seerr's button shape; on the phone its own line above the row).

| Who | Sees the button on |
|---|---|
| Admins, users with **Manage Requests** | every title |
| Everyone else (requesters, e.g. the owner's everyday account `jane`) | only titles with **their own** request (any status but declined); never on a friend's title, so nobody starts daytime downloads for someone else |

Why not in the Manage panel any more: requesters have no Manage button. One place for everyone keeps it simple; the Manage-panel button is gone (2026-10-28).

| Before the press | Title page shows (tooltip = the note) |
|---|---|
| downloads wait in a night category | the button; note "Waiting for the night 🌙. Starts at once instead, at the higher daytime power price." |
| nothing grabbed yet, file missing | the button; note "Nothing grabbed yet. Searches now …" |
| search queued / running (Radarr runs searches one after the other, minutes each) | chip "🔍 Waiting for other searches (n ahead)" / "🔍 Searching… (started HH:MM)", no button; asks the backend every 15 s until done or the page is left |
| searched, nothing found | chip "🧭 No release found yet (searched HH:MM) — searched again in tonight's scheduled search (04:17); downloads at night when found" + secondary **⚡ Search again now — at daytime power price** |
| grabbed, title tagged `download-now` | chip "◌ Downloading" (drawn spinner, never ✅ = available), static hint "Downloading now at the daytime power price (started with Download now)" (tap / hover / focus, same box as the status badges) |
| already downloading, downloaded, not in Radarr/Sonarr, not allowed | nothing (Seerr's status badge says the rest) |

A press: tag `download-now` on the title (Radarr/Sonarr) → the title's unfinished night downloads move to the normal category (qBittorrent `radarr-night`→`radarr`, `sonarr-night`→`sonarr`; SABnzbd `movies-night`→`movies`, `tv-night`→`tv`), paused ones resume → nothing grabbed: `MoviesSearch` / `SeriesSearch`, the grab goes to the tagged normal clients. Out of the night categories `night-downloads.py` never pauses or queues them again. **Search finds nothing:** `night-downloads.py` (every 2 min) removes `download-now` again (searched, nothing queued, no search running, no file), so a later find (tonight's 04:17 search, RSS) downloads at night; the title shows 🌙🧭. Feedback in place of the button: ✅ Started / ✅ Searching / Already downloading / ❌ Failed; the 🌙 label goes at once (`night-label.js` `release`). Items paused by hand in a normal category stay paused.

| Part | What |
|---|---|
| Button | `indexer-bar/www/download-now.js` (injected by `seerr-proxy`): title page (`/movie/<id>`, `/tv/<id>`) → `.media-header .media-actions`; logged in → asks the backend (GET); 403 or any error = no button. Follows Seerr's in-app navigation (old button goes, new title asked) |
| Backend | `download-now` container (`seerr-download-now`, `download-now/download_now.py`, python alpine, uid 65127, read-only, no caps, no port). `seerr-proxy` routes `/_download-now/` to it |
| Who may | every call (GET and POST): Seerr `/api/v1/auth/me` with the caller's `connect.sid` cookie (or Seerr's API key), then `GET /api/v1/movie\|tv/<id>` as the caller: permission ADMIN (2) or MANAGE_REQUESTS (16), **or** a request in `mediaInfo.requests` with `requestedBy.id` = the caller and status ≠ declined (3), else 403. Series: one own request (any season) allows the press for the whole series. POST only with `Origin: https://requests.holy-grail.ch` and JSON; searches at most every 2 min per title |
| API | `GET /_download-now/api?type=movie\|tv&tmdb=<id>`: what a press would do (no change). `POST /_download-now/api` `{"type","tmdbId"}`: do it. Answer: `state`, `released` (download ids), `moved`, `now` (tagged `download-now`), `search` (`status` queued/started/null, `started`, `ahead` = searches queued before it, `last` = last search; null when `/command` is not readable) |
| Keys | `secrets/download-now/{radarr,sonarr,sabnzbd,seerr}.apikey` (buntu:65127 0640; seerr = Seerr's own key, for [My request](#my-request)), never sent to the browser. qBittorrent: no-password bypass for 172.28.10.205 |
| Accepted risk | the qBittorrent bypass is the full WebUI API (preferences include "run external program" = code execution in the qBit container): qBit has one WebUI user and no scoped access, a password would grant the same. Narrowed where possible: the address is pinned and Movie-Downloads' `ip_range` (.0–.127) keeps Docker from handing .205 to another container; the backend only calls `torrents/info`, `torrents/setCategory`, `torrents/start`. Narrower would need a path-allowlist proxy in front of qBit (not built) |
| Setup | `scripts/download-now-setup.sh` (keys + qBittorrent bypass; idempotent), then `sudo docker compose up -d download-now`; after a Radarr/Sonarr/SAB key change re-run it and `sudo docker restart seerr-download-now` |
| Log | `sudo docker logs seerr-download-now`: one line per press (user id, `manager` or `own request`, title, state) |
| Fallback | backend down, Seerr changed the title page: no button, Seerr unchanged; the request's **Can wait** box still works |

| Check | Command |
|---|---|
| Tests | `python3 tests/test_download_now.py`; JS: `node --test` in `tests/js` |
| Dry run (admin, Seerr API key) | `curl -s -H "X-Api-Key: $(cat secrets/api-key)" 'localhost:5055/_download-now/api?type=movie&tmdb=<id>'` |
| Requester | `-H 'X-Api-User: <requester id>'`: own request's title → 200, a title only others requested → 403 (`seerr.download-now-requester` checks both) |
| Refused | same without the key → 401 (the session gate; the backend itself answers 403 for a session without the right) |

## My request

Title page → card **My request** under the title header (German **Meine Anfrage**), for everyone on their **own** requests; admins / **Manage Requests** see every request as **Requests (n)** with the requester. Collapsed by default; the summary shows profile and 🌙🧭📅 ([Night label](#night-label)).

| Shows | Change |
|---|---|
| Quality profile | any profile of the Radarr/Sonarr server (as the request dialog's Advanced); a picked profile stays when tags change later (tag `profile-pinned`, shown "Quality kept (your pick)"), picking the default again or adding `audio-swiss-german` ends that; picking Swiss German = adding that tag |
| Audio & subtitles (`audio-*`, `subs-*` as "Audio: French", "Subtitles: Spanish") | remove (×), add from the existing `audio-*`/`subs-*` tags only; other tags never |
| Can wait | on/off: off = [Download now](#download-now) (tag, night downloads move, search); on = `download-now` removed |
| Requested date, status, seasons, 4K, `old-rare` "(automatic)", `profile-pinned` "Quality kept (your pick)", folder only when there is more than one | read-only |
| **Delete request** | confirm dialog says what goes (below). Files on disk and the delete would clear the title: no plain confirm, a red warning "This also deletes the downloaded files (<file>, 61 GB) from the library", default (focused) **Remove request only, keep the files**; **Delete files too** needs a second tap ("Tap again to delete 61 GB of files") |

| Delete when | What goes |
|---|---|
| nobody else requested the title and Radarr/Sonarr added it with this request | everything, like **Clear Data**: Seerr request + title, Radarr/Sonarr entry + files (7 days recycle bin), downloads, Jellyfin (`clear-data-wipe.sh` within ~2 min) |
| others requested it too, or it was in Radarr/Sonarr before | only this Seerr request; the title and its files stay |

| Part | What |
|---|---|
| Who | same check as Download now (Seerr `auth/me` + `mediaInfo.requests`): own non-declined request, or ADMIN / MANAGE_REQUESTS. A requester cannot change a title someone else requested too (read-only, "ask an admin"); delete of their own request still works |
| By design (owner) | a requester may delete their own title including its files when theirs is the only live request (to the 7-day recycle bin; the default keeps the files, deleting them needs a second confirmation), and may pick any quality profile and tag of the list on their own requests (in Seerr alone that is REQUEST_ADVANCED). The user is read again after a sign-out (`/login`), so a new user on the same tab gets their own language and rights |
| Approved request | Seerr edits only pending requests (409), so the backend changes the Radarr/Sonarr title (`/movie\|series/editor`: profile, tags add/remove); `../Movie-Downloads/scripts/request-tags.sh` sees the change within 2 min and recomputes the title: default rules + every remaining tag, a removed tag goes back to the default, a picked profile is pinned (`../Movie-Downloads/README.md` "Tag changes"); one search if profile or audio changed |
| Pending request | `PUT /api/v1/request/<id>` (profile, tags incl. `download-now` and `profile-pinned` for a picked non-Swiss profile, seasons kept); Seerr hands it to Radarr/Sonarr on approval |
| Delete | `DELETE /api/v1/media/<id>` (Clear Data) or `DELETE /api/v1/request/<id>`, with Seerr's API key (`secrets/download-now/seerr.apikey`; a requester's own approved request cannot be deleted with their session) |
| API | `GET /_download-now/request?type=movie\|tv&tmdb=<id>` → `requests` (`id`, `status`, `profile`, `tags`, `auto`, `canWait`, `seasons`, `editable`, `deleteEffect` clear/request-only, `by` for managers), `profiles`, `tags`. `POST /_download-now/request` `{"type","tmdbId","action":"update","requestId",["profileId"],["tags"],["canWait"]}` or `{"…","action":"delete","requestId",["files":"keep"\|"delete"]}`; a delete that would clear a title with files needs `files` (else 409 `{"error":"files","files":{count,size,name}}`, nothing changes; `keep` = only the request); `requests[].files` = `{count, size, name}` or null; Origin + JSON only like Download now |
| Script | `indexer-bar/www/my-request.js` (injected by `seerr-proxy`), `<details>` card after `.media-header`; 403 or error = no card |
| Log | `sudo docker logs seerr-download-now`: one line per change (user, role, request, title, what changed / deleted how) |
| Deleted right after the request | wiped too: the Radarr/Sonarr add triggers a `clear-data-wipe.sh` snapshot within seconds (`../Movie-Downloads/README.md` "Delete a title") |
| Tests | `python3 tests/test_my_request.py`; JS `node --test` in `tests/js` (`my-request.test.mjs`) |

## Request sizes

Requests page → every row gets a **Size** line under Profile (German **Grösse**), and a **GB** sort control right of Seerr's sort. Title page (movie, series) → a **Size** row after Status in the facts panel.

| Row shows | When |
|---|---|
| `45.3 GB` | movie file on disk (Radarr `movieFile.size`) |
| `22.9 GB · 5 of 6 episodes` | series: the requested seasons' files (Sonarr season `sizeOnDisk`), episodes while some are missing |
| `≈ 5.4 GB · in queue` | nothing on disk yet, download in the Radarr/Sonarr queue (each download counted once) |
| `—` | nothing on disk or queued, title not in Radarr/Sonarr, 4K requests |

| Title page shows | When |
|---|---|
| `45.3 GB · 2160p HDR10` | movie file on disk, resolution + Radarr `videoDynamicRangeType` |
| `143.7 GB · 16 of 16 episodes` | series: every season on disk, monitored aired episodes (Sonarr `episodeCount`); hover / tap: per season (`Season 1: 82.0 GB · 8 of 8 episodes`) |
| `≈ 5.4 GB · in queue` | nothing on disk, downloads queued |
| `—` | nothing known but the title has a request (Seerr `mediaInfo.requests`) |
| no row | nothing known, no request; no **Status** row found (Seerr in another language than EN/DE) |

| Part | What |
|---|---|
| Sort | **GB** select: Off / Largest / Smallest (Aus / Grösste / Kleinste). Rows without a size last. Moves rows with CSS `order` only (React's DOM untouched); Seerr's sort group dimmed while active. Turning it on with more requests than shown switches Seerr's own page size to 100 (all requests on one page; above 100: sorted within the page, hint on the select); Off restores the old page size. Picking a Seerr sort or direction = Off (page size restored after Seerr's new list loaded). Choice kept per browser (`localStorage`) |
| Data | `request-state.json` `sizes`: Seerr request id → `{size}` / `{size, files, of}` / `{queued}`, and `media`: `movie\|tv/<tmdb id>` → `{size, quality}` / `{size, files, of, seasons}` / `{queued}` for every Radarr/Sonarr title (bytes, ids only, no paths; behind the Seerr session like the rest of the file), written by `../Movie-Downloads/scripts/night-downloads.py` in the 2-min run (one `/movie` + one `/series` call); the 15 s run keeps them. Arrs unreadable: last sizes kept |
| Match | rows ↔ Seerr's own `/api/v1/request?take=…` answer (XHR, read-only; missed: re-read once from resource timing), checked by each loaded row's title link; mismatch or no `sizes` = Seerr unchanged |
| Layout | line / row added inside the MutationObserver callback (before paint; title page: in the first frame the facts panel exists, measured); on wide screens (≥1280 px) every row, placeholders too, is 8.5rem instead of 7rem so the fifth line fits and nothing jumps; the controls wrap to a second line when there is no room |
| Script | `indexer-bar/www/request-sizes.js` (injected by `seerr-proxy`) |
| Tests | `python3 -m unittest tests.test_night_downloads` (Movie-Downloads, class `Sizes`); JS `node --test` in `tests/js` (`request-sizes.test.mjs`) |

## Request origin

Requests made with Jellyfin's Popular buttons get a pill in the **Requested** line of the Requests list and a **Requested via** row (German **Angefragt über**) after Size on the title page. Requests made in Seerr itself: no pill.

| Pill | Made with |
|---|---|
| `via Jellyfin` | 🌙/⚡ on Jellyfin's Popular page (web) |
| `via Moonfin` | Request in the Moonfin app (Seerr facade) |
| `via TV` | playing a Popular placeholder to its end on the TV |
| `… · Jane Doe` | Seerr shows the service user **Jellyfin popular** (the person has no Seerr account, or is not auto-approved): the real person from our audit |

| Part | What |
|---|---|
| Requester | Jellyfin's backend requests as the person's own Seerr account where it can (Jellyfin README "Currently popular", "As whom"), so Seerr's own "by" and My request work; the service user only as fallback. Old service-user requests keep their Seerr requester (Seerr changes `requestedBy` only on pending requests), the pill shows the person |
| Data | `/srv/appdata/jellyfin-popular-export/origins.json` (Jellyfin `popular`), mounted read-only into `download-now`; `GET /_download-now/origins` (Seerr session via `auth_request`): ADMIN / MANAGE_REQUESTS / REQUEST_VIEW get every request, everyone else only their own (`seerrUser` or `person` = them); no file = `{}` |
| Match | Requests rows ↔ request ids from `request-sizes.js` (`hgRequestSizes.rows`); title page: Seerr's `/api/v1/movie\|tv/<id>` `mediaInfo.requests` (one read per title) |
| Script | `indexer-bar/www/request-origin.js` (injected by `seerr-proxy`); hint: hover / tap (night label's hint box) |
| Tests | `python3 tests/test_download_now.py` (class `Origins`); JS `node --test` in `tests/js` (`request-origin.test.mjs`); Jellyfin `popular/test_app.py` (`Attribution`, `Origins`) |

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
