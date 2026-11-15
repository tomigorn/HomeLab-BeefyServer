# Seerr — requests for films & series

[Seerr](https://docs.seerr.dev) (Overseerr and Jellyseerr merged, v3) lets everyone
with Jellyfin access browse TMDB and request a film or a series. Approved requests
go to Radarr/Sonarr (`../Movie-Downloads`), availability comes from Jellyfin.
Public at `https://requests.holy-grail.ch` (wakes beefy, ~45 s).

Jellyseerr itself is frozen at 2.7.3 (`fallenbagel/jellyseerr:2.7.3-deprecated`); the successor is `ghcr.io/seerr-team/seerr`. Its releases have no OIDC yet, so this runs **our own image** `seerr-local:current`: newest stable release + the OIDC PR, rebuilt weekly ([Own image](#own-image-until-oidc-is-released)).

## Deploy

```bash
sudo mkdir -p /srv/appdata/seerr && sudo chown 65109:1000 /srv/appdata/seerr && sudo chmod 2770 /srv/appdata/seerr
sudo install -d -o 65127 -g 65127 -m 0700 /srv/appdata/seerr-download-now   # Manual interaction problem list
cd ~/Projects/Docker/Seerr && sudo docker compose up -d
curl -s http://127.0.0.1:5055/api/v1/status     # {"version":"3.5.1","commitTag":"v3.5.1-oidc2e52312",...}
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

### Own image (until OIDC is released)

Owner decision 2026-11-01: we build and run our own image from the newest stable release + OIDC PR seerr-team/seerr#2715 (+ our patches) until a release contains OIDC.

| | |
|---|---|
| Image | `seerr-local:current` (`pull_policy: never`), also tagged `seerr-local:<release>-oidc<pr sha7>`; previous one `seerr-local:rollback`. Labels `holy-grail.seerr.{tree,upstream,pr}` |
| Now | `v3.5.1-oidc2e52312` (2026-11-02, rebuilt 2026-11-15 with patch 20), upstream tests green; Next.js 16.4.3; Trivy critical 12 → 0, high 141 → 114 |
| Built, not deployed | `v3.5.2-oidc2e52312` (2026-11-15, `seerr-local:candidate`): resolution `image/resolutions/v3.5.2-2e523125e8e7.patch`, typecheck + 331 tests green, throwaway start on a config copy OK (status 3.5.2, OIDC start redirects to sso); no DB migration over v3.5.1 |
| Script | `scripts/seerr-rebuild.sh [--force] [--no-deploy] [--deploy-release]`, unit `systemd/seerr-rebuild.service` (buntu + docker group, state and clone in `/var/lib/seerr-rebuild`) |
| Timer | `seerr-rebuild.timer` Tue 04:27 (maintenance window waits for it) |
| Tests | `bash tests/test_seerr_rebuild.sh` (fake upstream git + fake docker) |

What a run does:

1. Newest stable tag (GitHub `releases/latest`) and the PR head; clone/fetch in `/var/lib/seerr-rebuild/work`. Only the PR commit in `image/pr-reviewed` is built: a moved head is reported once (Telegram) and the reviewed commit stays until you check the diff and put the new sha into that file (an unmerged third-party branch never reaches production by itself). File missing: nothing built.
2. Applies the PR's own diff (`merge-base(develop, PR)..PR`, without `docs/`) onto the tag with `git apply --3way`. A committed `image/resolutions/<tag>-<pr sha12>.patch` replaces that diff for exactly that pair (a hand-made conflict resolution, reproducible).
3. Applies `image/patches/*.patch` (our in-image changes: the Next.js bootstrap and the request cards below, everything else of ours lives outside the image, see `../CUSTOMIZATIONS.md`).
4. Merges `image/dependency-overrides.json` into `package.json` (`pnpm.overrides`, `scripts`) and refreshes the lockfile in the Dockerfile's node image. Overrides use `pkg@<x` selectors, so they stop mattering once upstream has caught up.
5. Same source tree as the running image (label): stop. Otherwise upstream build stage, `pnpm typecheck && pnpm test`, final image `seerr-local:candidate` (an already tested candidate of the same tree is reused).
6. **New upstream release** (tag ≠ label `holy-grail.seerr.upstream` of the running image): not deployed, it may migrate the DB one way. Telegram once, the candidate stays. Deploy it yourself (backup, verify, rollback as below):

   ```bash
   sudo systemd-run --uid=buntu --gid=buntu -p SupplementaryGroups=docker -p StateDirectory=seerr-rebuild --wait --pipe --quiet -E HOME=/home/buntu /bin/bash /home/buntu/Projects/Docker/Seerr/scripts/seerr-rebuild.sh --deploy-release
   ```

   Same release, only the reviewed PR commit / overrides / patches changed: deployed automatically.
7. Deploy: Seerr stopped, `/srv/appdata/seerr` tarred (`~/backups/seerr/`, last 6, mode 600, SQLite integrity checked), tag swap, start, verify (healthy, `commitTag`, OIDC start redirects to sso, post-update check `Seerr`). Any failure: config restored from that backup, previous image back, Telegram.

| Telegram | When |
|---|---|
| does not apply onto vX | PR conflicts with a new release: image kept, resolve below; told once per release + PR head (state `told-conflict-<tag>-<sha12>`), the weekly run stays quiet until then |
| OIDC PR moved to X | review `git diff <reviewed> <new>` in `/var/lib/seerr-rebuild/work/seerr`, then `echo <new sha> > image/pr-reviewed`, commit |
| build / tests failed | image kept |
| vX built and tested, not deployed | new release held: deploy with `--deploy-release` (step 6) |
| failed its checks, rolled back | config + image restored |
| OIDC is in release vX | PR merged and released: back to the official image (below); until then the job builds the plain release |

Resolve a conflict (once per release + PR head), as buntu after the failed run (conflict markers in the tree):

```bash
cd /var/lib/seerr-rebuild/work/seerr
# fix the files, git add them, then:
git diff --binary --cached <tag> > ~/Projects/Docker/Seerr/image/resolutions/<tag>-<pr sha12>.patch
sudo systemctl start seerr-rebuild.service
```

- `pnpm-lock.yaml`: resolve only the conflict hunk (keeps the PR's reviewed dependency versions), then `pnpm install --lockfile-only --ignore-scripts` in the Dockerfile's node image (`sudo docker run … --user 1000:1000 -v $PWD:/src -w /src <node image> corepack pnpm install --lockfile-only --ignore-scripts`) must leave it unchanged. Regenerating from scratch would lift openid-client/jose to unreviewed versions.
- Test a resolution with `--no-deploy` first (systemd-run command of step 6 with `--no-deploy`).

| Resolution | What |
|---|---|
| `v3.5.2-2e523125e8e7` | `package.json`/lockfile: upstream nodemailer 10.2.1 + the PR's openid-client 6.8.4, jose 6.2.3, fetch-mock. `usersettings.ts`: upstream's security fix (`canModifyUser`, a manager may not change an admin) kept in the Plex/Jellyfin unlink routes together with the PR's "password or linked account" rule; the same check added to the PR's own OIDC unlink route `DELETE /linked-accounts/:acctId` (+ 2 tests) |

Next.js 16.4.3 (override; 16.2.6 had 5 critical CVEs). Under Turbopack it inlines a per-page bootstrap `<script>` (chunk list of `/_app` + the page, so a CSP hash would differ per page and per build), which our CSP (`script-src 'self'`, fastpi Traefik) blocks. `image/patches/10-turbopack-bootstrap-out-of-line.patch` (`_document.tsx`) puts that data into `<script type="application/json" id="__HG_TURBOPACK_BOOTSTRAP__">` and runs it from the static `/hg-turbopack-bootstrap.js`: the CSP stays unchanged. Checks `seerr.no-inline-script` (0) and `seerr.turbopack-bootstrap` roll a build back where Next changes this. (`next build --webpack` breaks title pages: SVG imports.)

Request cards (Requests page, Recent Requests, profile): upstream re-fetched every card's `/api/v1/request/<id>` and its title on mount and on every tab focus, plus every 15 s while downloading (one open tab: 11,093 calls overnight, 100 per focus at 100 rows, each through the SSO check). `image/patches/20-request-cards-no-self-polling.patch` (`src/utils/requestCardSwr.ts` + test, `RequestList/RequestItem`, `RequestCard`): a card shows the list's answer, fetches on its own only while a download runs (every 60 s, until its own answer shows the download done), the newer of list and own answer wins; titles are fetched once. Approve / decline / retry / edit still refresh the card. Check `seerr.request-cards-no-poll`.

Changing `image/` (overrides, patches): `--no-deploy` first, then a logged-in browser run (title page, request dialog) before deploying; the checks cannot render pages behind a session.

Manual rollback to the September preview (no DB migration between them):

```bash
sudo docker tag ghcr.io/seerr-team/seerr:preview-new-oidc seerr-local:current && cd ~/Projects/Docker/Seerr && sudo docker compose up -d --no-deps seerr
```

DB: v3.5.1 + PR adds no migration over the preview (56 both, 2026-11-02); v3.5.2 adds none either (only edits of old, already applied SQLite migrations; 56 after a start on a copy, 2026-11-15); a later release may migrate one way (held, step 6), the backup of step 7 is the way back. Backup before the switch: `~/backups/seerr-2026-11-01-pre-own-image.tgz`.

### Back to stable

When the job reports "OIDC is in release vX":

```bash
cd ~/Projects/Docker/Seerr
# docker-compose.yaml: image: ghcr.io/seerr-team/seerr:vX.Y.Z, drop pull_policy and the "Own image" comment
sudo systemctl disable --now seerr-rebuild.timer
sudo docker compose up -d seerr
./scripts/configure.sh          # re-applies OIDC, Radarr/Sonarr, main settings
```

Then: `../_global/auto-update.conf`: a notify row for `seerr` (the sidecar rows stay auto); checks `seerr.image-local`, `seerr.rebuild-timer` and their `CUSTOMIZATIONS.md` rows out. Linked accounts stay in the DB. If the release configures OIDC differently (UI/API), adapt `configure.sh`.

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

## Hint boxes

Every hover / tap hint of our scripts goes through one module, `indexer-bar/www/tip.js` (`window.hgTip`, injected first). No browser `title` tooltips (delay, other look, nothing on phones), `bar.js` included (also on AudioBookRequest, which vendors `tip.js` with it); a page without `tip.js` shows the bar without hints (`aria-label`s stay).

| Part | What |
|---|---|
| Look | Seerr's tooltip: gray-800 box, gray-600 border, rounded, shadow, text-sm, max 22rem, wraps; kept inside the viewport (below, flips above) |
| Open | mouse hover and keyboard focus at once (only a moving mouse: an element that slides under a resting cursor, on page load or a re-render, waits for the next move); touch: first tap shows, second tap goes through (menu links, sort select, tag ×: tap goes straight through); Escape, outside click, scroll close; one box at a time; `role="tooltip"`, `aria-describedby`, `aria-label` on elements without text |
| Content | bold headline (emoji where it helps), then short rows (`label: value`), release / file names on their own line in monospace, thin line between sections; text only, never markup |
| Seerr's own tooltip | opens for the same element (status pill: download popup, "Manage Movie", "Play on Jellyfin"): our text goes into Seerr's box as its first block, no second box; kept on top across React re-renders. Seerr's box hidden (phones hide the download popup) or none: our box |
| Seerr's tooltips pinned | every Seerr tooltip (status pills, request / blocklist / watchlist buttons, badges, with or without our block) sits like ours: below its trigger, left edges aligned, flips above, inside the viewport, follows scroll, resize and its own re-render, instead of following the mouse (`data-hg-tip-pin`, `!important` CSS beats popper's inline transform). Trigger: Seerr's portal has no link to it, so it is the badge / button / link around the last `pointerover` / `focusin` target when the box appears (kept while open; moves inside Seerr's box don't count). The 6 px gap keeps the interactive download popup reachable. Nothing hovered or focused: left to Seerr |
| Bar hints | storage chip: **name — sub**, `Used:`, `Free:`, each SSD's %, then the rule one sentence per row; subtitle chip: **name**, provider, `Searches:` / `Downloads:`, paid ★, pause reason, `Counted:`, ≈ note as its own section; quota chip: **My requests** + the rule; ★: the paid text; Indexers button: Show / Hide (a tap toggles at once). Hinted chips are focusable; a bar re-render closes an open hint |
| Tests | `tip.test.mjs` (incl. plain Seerr tooltip pinned across mouse moves, trigger detection); `no-title.test.mjs` (no `title` set in any script); the test fixture fails every test whose script sets a `title`; `bar*.test.mjs` (hint contents, page without `tip.js`); `tests/test_bar_layout.py` (hints inside the screen at 390/1280 px) |

## Indexer bar

A thin bar at the top of every Seerr page (everyone who can open Requests sees it): limited indexers used / allowed, subscription deadlines; **more** lists everything. Collapsible (remembered per browser). Docs for users: wiki `apps/requests.md` "The indexer bar at the top" (HomeLab-FastPi).

| Part | What |
|---|---|
| `seerr-proxy` | `nginxinc/nginx-unprivileged`, uid 65124, read-only, no caps, 64 MB. Takes host port `5055`, proxies everything to `seerr:5055` (websockets too), adds first `bar-early.js` (render-blocking, no `defer`: puts `ib-on` / `ib-mini` on `<html>` before the first paint, so `bar.css` holds the bar's place as an empty dark strip and the page never drops when the bar arrives; not on the no-bar paths), then `<link>` + `<script>` for `/_indexer-bar/bar.css` + `bar.js` (and `can-wait.js`, `menu-links.js`, `night-label.js`, `download-now.js`, `my-request.js`, `request-sizes.js`, `request-filter.js`, `request-origin.js`: [Can wait](#can-wait), [Menu links](#menu-links), [Night label](#night-label), [Download now](#download-now), [My request](#my-request), [Request sizes](#request-sizes), [Request filter](#request-filter), [Request origin](#request-origin)); `/_download-now/` goes to the `download-now` backend before `</head>` (`sub_filter`, Seerr's gzip off, nginx compresses again). Seerr down (just woke): 503 page that reloads itself. Config `indexer-bar/nginx.conf` |
| Jellyfin sign-in blocked | `/api/v1/auth/jellyfin` and below (password login, Quick Connect initiate/check/authenticate) answer 403 in nginx (case-insensitive); users sign in via OIDC (`/api/v1/auth/oidc/…` untouched). Quick Connect ignores `mediaServerLogin`, so a Jellyfin-linked account could otherwise get a session without the SSO step. Gap: a client on a docker network reaching `seerr:5055` directly skips the proxy. Check: `seerr.jellyfin-auth-blocked` |
| Session gate | `usage.json`, `night-waiting.json`, `request-state.json` and `/_download-now/` (not `/health`) need a Seerr session: nginx `auth_request` asks Seerr `GET /api/v1/auth/me` with the caller's own cookie (or `X-Api-Key`/`X-Api-User`), cached 10 s per credentials; none/invalid = 401. The bar scripts render only after a 200 (a navigation while the first answer is pending keeps the reserved strip and draws nothing, never "usage unavailable"), never on `/login`, `/setup`, `/resetpassword`, and re-check after client-side navigation (login without reload); 401 or no answer takes the reserved strip away. Check: `seerr.data-needs-session` |
| Shared code | `bar-early.js` + `bar.js` + `bar.css` (and the hint box `tip.js`) are one codebase with the requests bar above AudioBookRequest (fastpi, HomeLab-FastPi `Docker/Audiobook-Requests`): canonical here, vendored there unchanged; config = attributes on the `<script>` tag, app wording (disk hints, panel note) comes from `usage.json` (`indexer-usage.py`). Format: `indexer-bar/schema.md`. Change here, run both test suites, then on fastpi `Docker/Audiobook-Requests/scripts/sync-bar.sh` (fastpi's post-update check `books.bar-shared` compares the hashes) |
| `/_indexer-bar/` | `indexer-bar/www/` (bar-early.js, bar.js, bar.css, starting.html, can-wait.js, menu-links.js, night-label.js, download-now.js); `usage.json` and `night-waiting.json` from `/srv/appdata/indexer-bar/` |
| Caching | every injected URL ends in `?v=<version>` (first 12 hex of sha256 over `www/`, names + contents); that URL is cached a year (`private, max-age=31536000, immutable`: browser only, never a shared cache), any other `?v=` or none `no-cache`; the JSON data stays `no-store`. No manual bump: `systemd/seerr-bar-assets.path` runs `scripts/bar-assets.sh` on every change in `www/`, which writes `/srv/appdata/indexer-bar/asset-version.conf` (`set $hg_v "…";`, included by `nginx.conf`) and reloads seerr-proxy (a second later every page names the new URL). Version: `scripts/bar-assets.sh version`. Check `seerr.asset-cache` |
| Collector | `scripts/indexer-usage.py` as buntu, `systemd/indexer-usage.timer` every minute: Prowlarr `GET /api/v1/indexer` + `/api/v1/history/since`, counts queries + RSS (searches) and grabs (downloads) per indexer in its window; Bazarr `/api/providers`, `/api/system/jobs`, `/api/movies/history`, `/api/episodes/history` and the provider accounts (below) for the subtitle chips. Writes names and counts only, no keys, no queries, no titles |
| Subtitles | one chip per Bazarr provider in `limits.toml` `[subtitles.*]`, per UTC day (resets 00:00 UTC = 01:00 CET). **SubDL**: requests and downloads used / limit, exact from SubDL's `GET /api/v1/me` (works with the free key; does not count as a request), every 5 min. **OpenSubtitles**: downloads used / allowed, exact from `/api/v1/infos/user` every 10 min (login token kept 12 h), VIP = ★; red with "download limit reached, retry …" while Bazarr pauses it (`/api/providers`). Downloads are the higher of the account count and Bazarr's history (provider, actions downloaded/manual/upgraded), so new downloads show within a minute. Account down or no key: SubDL ≈ searches / 2000 (estimate: Bazarr's finished search jobs, movie ≈ 1 request, episode ≈ 3), OpenSubtitles downloads from the history without a limit. Bazarr down: "Subtitle usage unavailable", indexers unaffected |
| Subtitle keys | `scripts/subtitle-keys.py` copies the SubDL key and the OpenSubtitles login + the API key Bazarr uses (from Bazarr's config and `get_providers.py`, needs sudo) to `secrets/indexer-bar/` (`subdl.apikey`, `opensubtitles.json`, 0400, gitignored). Re-run after changing a key/password in Bazarr or a Bazarr update (`seerr.bar-subtitle-accounts` fails). Bazarr key `../Movie-Downloads/secrets/hook/bazarr.apikey` (read only). State: `~/.local/state/indexer-bar/subtitle-searches.json` (hashed job ids and counts), `subtitle-accounts.json` (0600, cached numbers + OpenSubtitles token). Errors log only the HTTP status, never a credential |
| Premium ★ | after the name of every paid account or subscription (indexer, subtitle and subscription rows, bar and **more**): names with a `[[subscriptions]]` entry, `premium = true` on an `[indexers."…"]`/`[subtitles.…]` entry, OpenSubtitles VIP. Hover / focus: "Paid account (lifetime)", "Paid subscription until 18 Nov 2026", "Paid subscription, ended 3 Mar 2026", "Paid account" (German when Seerr is German) |
| Phones | the **more** popup stays inside the screen from 320 px: below 520 px each value cell wraps under its name. Browser test `tests/test_bar_layout.py` (320/390/430/1280 px) |
| Storage | `SSD` (ssd-hot + ssd-hot2 together) and `HDD` chips: used/total and %, from `os.statvfs` of each branch (`1 − f_bavail/f_blocks`, the mergerfs measure). SSD green < 70 %, yellow 70-80, red ≥ 80; HDD green < 80, yellow 80-90, red ≥ 90. Hover / tap (and **more**): used, free space, both SSDs' %, and the rule (mover from 70 % down to 60 % with titles unplayed for 30 days, from 80 % new downloads go to the HDD; HDD from 90 % no imports). EN/DE. The collector also writes `~/.local/state/indexer-bar/storage.prom` (sizes only), which fastpi pulls for its alerts |
| Limits + deadlines | `indexer-bar/limits.toml` (committed, no secrets): indexers, subtitle providers, subscriptions; picked up on the next run, no restart |
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
sudo install -m 0644 systemd/seerr-bar-assets.path systemd/seerr-bar-assets.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now indexer-usage.timer seerr-bar-assets.path && sudo systemctl start seerr-bar-assets.service
sudo docker compose up -d
```

| Check | Command |
|---|---|
| Tests | `python3 tests/test_indexer_usage.py`, `python3 tests/test_subtitle_keys.py`, `bash tests/test_bar_assets.sh`; JS `node --test` in `tests/js` (`bar*.test.mjs`, `inject.test.mjs`: injection order) |
| Browser test | once `sudo docker build --network host -t seerr-bar-test:local tests/browser-image`, then `sudo docker run --rm --network none -v $PWD:/w:ro -w /w seerr-bar-test:local python3 tests/test_bar_layout.py` (screenshots of the popup and the storage / subtitle hints: `-e SHOT_DIR=/shots -v <dir>:/shots`) |
| Subtitle keys | `scripts/subtitle-keys.py` (once, and after a key change in Bazarr) |
| Injected | `curl -sL localhost:5055/ \| grep _indexer-bar` (each with `?v=` = `scripts/bar-assets.sh version`) |
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

`indexer-bar/www/menu-links.js` (injected like `can-wait.js`) adds two entries after Series in the desktop sidebar, the phone drawer and the phone bottom bar:

`indexer-bar/www/menu-links.css` (linked before `</head>`) keeps Seerr's sidebar and phone bar invisible until the script marks `html[data-hg-menu]` (on the frame after our entries are in, not waiting for images), so a fresh load (e.g. coming from books.holy-grail.ch) shows the menu once, complete, instead of entries jumping in; shown anyway after 2.5 s. `menu-links.js` is the first deferred script after `tip.js` and adds nothing before React has hydrated Seerr's server-rendered page (React's `__reactFiber$…` key on `#__next`'s first element, checked every frame, 10 s at most): an entry put in earlier made React throw error #418 and redraw the whole page.

| Entry | Opens | Phone bottom bar (no "more" button; with one: in the "more" list) |
|---|---|---|
| **Audiobooks** (German UI: **Hörbücher**) | `https://books.holy-grail.ch/` (AudioBookRequest on fastpi, public via Cloudflare, Holy Grail login, group `streaming-requesters`), same tab | while the bar has at most 5 items with it |
| **Manual interaction** (**Manuell beheben**) + amber count of open problems (none at 0; in the sidebar on the icon's corner and the label on one line, so the entry never grows; at the end in the phone drawer); active look on its page | `/manual` inside Seerr ([Manual interaction](#manual-interaction)), with Seerr's own router showing its 404 page under `/manual` (no reload: Next reloads the whole page for a path it has no page for, the menu flashed) | while the bar has at most 6 items with it (so phones without a "more" list still reach it) |

Count: `GET /_download-now/manual/count` once per page load, again on Seerr navigation at most every 5 min; no session or an error = no badge. Links and URL: `LINKS` at the top of the file. Seerr without a `/discover/tv` link: no entry, nothing breaks. Only menu links count: `/discover/tv` links inside `<main>` (Discover's **Popular Series** row title) are skipped, otherwise an "Audiobooks" title lands squeezed beside it (fixed 2026-10-28; Discover gets no own Audiobooks row, the menu entry covers it).

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
| download/import problem, Seerr failed | ⚠️ (no 🌙 while the queue state is problem; also next to Seerr's own spinner/progress) |

Status icons (◌ ✅ ◐) come first, in the same purple circle/pill. ✅ never combines; ◐ combines with 🌙🧭📅 and with ◌ (series: some episodes here, more downloading now); a download paused for the night shows 🌙 instead of ◌. ◐ hints: 🌙 alone adds "the rest downloads at night", 🧭 reads "searched again tonight (04:17)", 📅 reads "next episode 11 Nov". Overview of all rows: Claude-Mockups `seerr-all-badges`; percent from Seerr's `downloadStatus` sizes, left out when unknown. Same on every card: Discover, search, collections, person pages. Title-page pills stay Seerr's look, but every state carries a hint (below).

Order ⚠️⏳🔍🌙🧭📅, combined. Placements: status badges (title page, request cards, Requests list; after "Requested"/"Pending"), Discover cards (in Seerr's status circle: one icon = the 26px circle, two = a pill). Pills with Seerr's own spinner/progress get nothing of ours except ⚠️ (its popup shows the download). **Partially Available** pills are sky blue (`#0284c7`, not Seerr's green) and start with ◐; never ✅ inside (hint per case: "Partially Available pill" below). One hint on the whole badge / pill ([Hint boxes](#hint-boxes)), one section per icon, e.g. 🌙🧭: "🌙 Waiting for the night / Downloads at night (cheaper power)", line, "🧭 No release found yet / Searched again in tonight's scheduled search (04:17)"; ⚠️ needs import: "⚠️ Needs a manual import", "Release:", the release name (monospace), "What to do: open Manual interaction". Where Seerr opens its own popup for the pill, the hint is the first block inside it. `data-hg-hint` keeps the one-line form. While a 🔍/🔎 shows, the page reads `request-state.json` every 15 s and swaps the icon in place (spinner, 🧭, 🌙 when the search ends).

**Title-page pill hints** (`night-label.js`, `.media-status`): the pill always has a static hint, also where Seerr's popup exists or no icon of ours applies. Icons of ours (🌙 🔍 ◐ …) merge into the hint as above; otherwise by the pill's text (EN / DE):

| Pill | Symbol | Hint |
|---|---|---|
| Processing + Seerr's spinner | Seerr's spinner | Downloading now — 40 % (the percent is Seerr's own bar behind the pill; none while 0) / Lädt gerade herunter — 40 % |
| Processing, no download | - | Processing — handed to Radarr/Sonarr / Wird verarbeitet — an Radarr/Sonarr übergeben |
| Requested | - | Requested — approved, waiting for the search / Angefragt — freigegeben, wartet auf die Suche |
| Pending | ⏳ | Waiting for approval / Wartet auf Freigabe |
| Available | ✅ | Available in Jellyfin / In Jellyfin verfügbar |
| Partially available | ◐ | Partly available + the case below |
| Declined | - | Declined — an admin refused the request / Abgelehnt — ein Admin hat die Anfrage abgelehnt |
| Unknown, Deleted | - | Unknown — Seerr has no state for it · Deleted — removed from Jellyfin (DE: Unbekannt — …, Gelöscht — …) |

**Partially Available pill** (Requests page, request cards, title page): ◐ "Partly available", then the first case that applies (EN / DE). Data: `request-state.json` (open requests) and Seerr's `GET /api/v1/tv/<id>` (requested seasons, Seerr's season states, TMDB's seasons, downloads), fetched for every such pill (cached 60 s). Nothing known: only "Partly available".

| Case | Symbol | Hint |
|---|---|---|
| ⚠️ ⏳ 🔍 🔎 🌙 🧭 (open request) | as above | as above |
| Downloading (Seerr's `downloadStatus` `downloading`, else queue) | ◌ | ⬇️ Downloading S01E05 (and 2 more) / Lädt S01E05 herunter (und 2 weitere); no episode: Downloading now |
| Not released yet (open request) | 📅 | 📅 Next episode expected 18 Nov / Nächste Folge erwartet |
| Every requested season in Jellyfin | - | ✅ Everything you requested is in Jellyfin / Alles Angefragte ist in Jellyfin; row: Seasons 3–7 were not requested / Staffeln 3–7 wurden nicht angefragt |
| Requested season incomplete, nothing else known | - | Not everything you requested is in Jellyfin yet; In Jellyfin: Season 1; Episodes missing: Season 2; (Season 3 was not requested) |

| Part | What |
|---|---|
| Data | `/_indexer-bar/night-waiting.json` (`waiting`: download ids), written every 2 min by `../Movie-Downloads/scripts/night-downloads.py` to `/srv/appdata/indexer-bar/`; empty in the low tariff |
| Icon data | `/_indexer-bar/request-state.json` (`canWait`: `movie\|tv/<tmdb id>` list, `unreleased`: id → date or null, `noRelease`: id → last search, `searching`: id → `{status: started, started}` / `{status: queued, ahead}`, `fresh`: id → request time or null, `pending`, `imported`, `soFar`: lists, `queue`: id → `{state: paused\|downloading\|importing\|problem, message}`; a problem also has `code`: `seerr` (Seerr could not hand it over), `wrong-film` (+`release`, `releaseYear`, `year`: an import blocked with "matched … by ID" whose release year is more than 2 years off the title's, the watchdog replaces it) or `needs-import` (+`release`: same block, year fits, manual import); the page words the code in EN/DE), same script; reads Seerr `/api/v1/request` with `secrets/api-key` and Radarr/Sonarr; ids and short Radarr/Sonarr status messages (paths and URLs cut), no user data. Every 2 min with the night run; `--request-state` (`../Movie-Downloads/systemd/request-state.timer`, every 15 s) rewrites it only while a search runs or a request is from the last 5 min (2 commands + 1 Seerr call otherwise). Seerr or Radarr/Sonarr not readable: file kept; older than 10 min = no icons. Visible to every Seerr session (all titles, not only the viewer's): accepted, it holds title and request ids, states and sizes only, no user names or ids, and Seerr itself shows every title's request status on its cards to everyone |
| Match | Seerr's `downloadStatus` item: `status` `paused` **and** `downloadId` in `waiting`; a badge only when every download behind it waits |
| Script | `indexer-bar/www/night-label.js`: status badge (title page, request cards, Requests list), Discover card status circle → 🌙/🧭/📅 and ◌/✅/◐ (downloading/available/partly), download blocks (Manage panel, Seerr's badge popup: label, bar grey). Titles from Seerr's own API responses (read-only) or `GET /api/v1/movie|tv/<id>` |
| Fallback | file missing or older than 10 min, API error, Seerr changed its markup: Seerr's normal display |
| Check | `curl -s localhost:5055/_indexer-bar/night-waiting.json \| head -c 200`, `curl -s localhost:5055/_indexer-bar/request-state.json`; tests in [Menu links](#menu-links) (`node --test`) |

## Download now

**⚡ Download now — at daytime power price** (German UI: **⚡ Jetzt herunterladen — zum Tagstrompreis**) sits on the title page, first in the button row next to Request/Play (amber, Seerr's button shape; on the phone its own line above the row).

| Who | Sees the button on |
|---|---|
| Admins, users with **Manage Requests** | every title |
| Everyone else (requesters, e.g. the owner's everyday account `jane`) | only titles with **their own** request (any status but declined); never on a friend's title, so nobody starts daytime downloads for someone else |

Why not in the Manage panel any more: requesters have no Manage button. One place for everyone keeps it simple; the Manage-panel button is gone (2026-10-28).

| Before the press | Title page shows (hint box = the note, first sentence bold) |
|---|---|
| downloads wait in a night category | the button; note "Waiting for the night 🌙. Starts at once instead, at the higher daytime power price." |
| nothing grabbed yet, file missing | the button; note "Nothing grabbed yet. Searches now …" |
| search queued / running (Radarr runs searches one after the other, minutes each) | chip "🔍 Waiting for other searches (n ahead)" / "🔍 Searching… (started HH:MM)", no button; asks the backend every 15 s until done or the page is left |
| searched, nothing found | chip "🧭 No release found yet (searched HH:MM) — searched again in tonight's scheduled search (04:17); downloads at night when found" + secondary **⚡ Search again now — at daytime power price** |
| grabbed, title tagged `download-now` | chip "◌ Downloading" (drawn spinner, never ✅ = available), hint "Downloading now at the daytime power price (started with Download now)" ([hint box](#hint-boxes)) |
| already downloading, downloaded, not in Radarr/Sonarr, not allowed | nothing (Seerr's status badge says the rest) |

A press: tag `download-now` on the title (Radarr/Sonarr) → the title's unfinished night downloads move to the normal category (qBittorrent `radarr-night`→`radarr`, `sonarr-night`→`sonarr`; SABnzbd `movies-night`→`movies`, `tv-night`→`tv`), paused ones resume → nothing grabbed: `MoviesSearch` / `SeriesSearch`, the grab goes to the tagged normal clients. Out of the night categories `night-downloads.py` never pauses or queues them again. **Search finds nothing:** `night-downloads.py` (every 2 min) removes `download-now` again (searched, nothing queued, no search running, no file), so a later find (tonight's 04:17 search, RSS) downloads at night; the title shows 🌙🧭. Feedback in place of the button: ✅ Started / ✅ Searching / Already downloading / ❌ Failed; the 🌙 label goes at once (`night-label.js` `release`). Items paused by hand in a normal category stay paused.

| Part | What |
|---|---|
| Button | `indexer-bar/www/download-now.js` (injected by `seerr-proxy`): title page (`/movie/<id>`, `/tv/<id>`) → `.media-header .media-actions`; logged in → asks the backend (GET); 403 or any error = no button. Follows Seerr's in-app navigation (old button goes, new title asked) |
| Backend | `download-now` container (`seerr-download-now`, `download-now/download_now.py`, own image `seerr-download-now:local` = python alpine + ffprobe from `download-now-image/` (264 MB disk), `sudo docker build --network host -t seerr-download-now:local ./download-now-image && sudo docker compose up -d --no-deps download-now` (bridge builds have no DNS here; `update-project.sh` builds it with the host-network entitlement); uid 65127, read-only, no caps, no port). `seerr-proxy` routes `/_download-now/` to it |
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

Requests page → every row gets a **Size** line under Profile (German **Grösse**), series rows the episode count after their season badges, and a **GB** sort control right of Seerr's sort. Title page (movie, series) → a **Size** row after Status in the facts panel.

| Row shows | When |
|---|---|
| `45.3 GB · 15.3 GB/h` | movie file on disk (Radarr `movieFile.size`), per hour of Radarr `runtime` (no runtime: no GB/h) |
| `22.9 GB · 5 of 6 episodes · ⌀ 4.6 GB/ep · 6.1 GB/h` | series: the requested seasons' files (Sonarr season `sizeOnDisk`), episodes while some are missing, size / episode files (DE `⌀ 4.6 GB/Ep.`), size / (episode files × Sonarr `runtime`) |
| `⚠️ Remux` pill | the movie file or an episode file of the requested seasons is a remux (quality name contains "Remux"); also on the title page; hint: "⚠️ Remux / The untouched disc copy, far bigger than a normal release" |
| `⚠️ very big` pill | over 30 GB/h (film) or 20 GB/h (episodes), DE `⚠️ sehr gross`; also on the title page; hint: "⚠️ Very big / Over 30 GB per hour of running time / This one: 40.0 GB/h" |
| `≈ 5.4 GB · in queue` | nothing on disk yet, download in the Radarr/Sonarr queue (each download counted once); no GB/h, no pills |
| `—` | nothing on disk or queued, title not in Radarr/Sonarr, 4K requests |
| `Seasons ① ② ③ · 36 episodes` | series row: monitored aired episodes of the requested seasons (Sonarr `episodeCount`), DE `· 36 Episoden`; hover / tap: `Season 1: 10 episodes` (`Staffel 1: 10 Episoden`) |

| Title page shows | When |
|---|---|
| `45.3 GB · 2160p HDR10 · 15.3 GB/h` | movie file on disk, resolution + Radarr `videoDynamicRangeType`, GB/h + pills as in the rows |
| `143.7 GB · 16 of 16 episodes · 9.8 GB/h` | series: every season on disk, monitored aired episodes (Sonarr `episodeCount`); hover / tap: per season (`Season 1: 82.0 GB · 8 of 8 episodes`) |
| `≈ 5.4 GB · in queue` | nothing on disk, downloads queued |
| `—` | nothing known but the title has a request (Seerr `mediaInfo.requests`) |
| no row | nothing known, no request; no **Status** row found (Seerr in another language than EN/DE) |

| Part | What |
|---|---|
| Sort | **GB** select: Off, Largest / Smallest **total**, **per file** (a film is one file, a series size ÷ episode files), **per hour** (size ÷ running time) (Aus, Grösste / Kleinste gesamt, pro Datei, pro Stunde; owner 2026-11-15). Titles without that value last. Rows without a size last. Sorted over **all** requests, page by page: while it is on, Seerr's own list call gets `&hg_size=<mode>`, seerr-proxy sends that to download-now `/_download-now/by-size` (`by_size.py`: every request of the caller's view, asked from Seerr as the caller, sorted by `sizes`, one page in Seerr's format), so Seerr's page size and paging stay as they are (one call per page, no more 100-row pages). To make Seerr load again, turning it on flips Seerr's sort (added ↔ modified), another order clicks Seerr's direction, Off puts both back (`hg-size-sort-seerr`); Seerr's sort group dimmed while active. Rows also ordered with CSS `order` (React's DOM untouched). Picking a Seerr sort or direction = Off. A page size of 100 left by the older version goes back once. Choice kept per browser (`localStorage`) |
| Problem | a queued size of a title whose queue state is `problem` reads `≈ 58.8 GB · problem` (DE `Problem`) instead of `· in queue` (needs `request-state.json` `queue`) |
| Data | `request-state.json` `sizes`: Seerr request id → `{size, runtime, remux}` / `{size, files, of, episodes, runtime, remux}` (`episodes`: per requested season; a known series keeps `files, of, episodes` also with nothing on disk) / `{queued}`, and `media`: `movie\|tv/<tmdb id>` → `{size, quality, runtime, remux}` / `{size, files, of, seasons, runtime, remux}` / `{queued}` for every Radarr/Sonarr title (bytes, minutes per film or episode, `remux` only when true; ids only, no paths, no titles; behind the Seerr session like the rest of the file), written by `../Movie-Downloads/scripts/night-downloads.py` in the 2-min run (one `/movie` + one `/series` call, plus `/episodefile?seriesId=` per series with files, for remux); the 15 s run keeps them. Arrs unreadable: last sizes kept |
| Match | rows ↔ Seerr's own `/api/v1/request?take=…` answer (XHR, read-only; missed: re-read once from resource timing), checked by each loaded row's title link; mismatch or no `sizes` = Seerr unchanged |
| Layout | line / row added inside the MutationObserver callback (before paint; title page: in the first frame the facts panel exists, measured); on wide screens (≥1280 px) every row, placeholders too, is 8.5rem instead of 7rem so the fifth line fits and nothing jumps, and the facts column (`data-hg-facts`) ends 1rem before the buttons; without buttons (normal users) the empty buttons column is hidden and the facts take its width; on phones (<640 px) the facts column fits inside the card; its text-only values (Profile, Size, …) end in `…` with the full text as tooltip, the pills always stay visible; the controls wrap to a second line when there is no room |
| Script | `indexer-bar/www/request-sizes.js` (injected by `seerr-proxy`); hints ([hint boxes](#hint-boxes)): pills, episodes, per season, the sort select ("Sorted within this page"), and a cut-off value's full text (only while cut off) |
| Tests | `python3 -m unittest tests.test_night_downloads` (Movie-Downloads, class `Sizes`); `python3 tests/test_by_size.py`; JS `node --test` in `tests/js` (`request-sizes.test.mjs`) |

## Request filter

Two entries right after **All** in the Requests filter (funnel icon), as extra options with Seerr's own filter values: Seerr's state, URL, pagination and the size sort (`hg_size`) keep working. Seerr's own entries stay below.

| Entry (DE) | Seerr filter | Shows |
|---|---|---|
| ✅ Request fulfilled (✅ Anfrage erfüllt) | `available` | approved and done, title fully in Jellyfin (partially available is not finished) |
| ⏳ Request not yet finished (⏳ Anfrage noch nicht fertig) | `processing` | approved, title unknown / pending / downloading / partially available (waiting for approval: Pending) |

| Part | What |
|---|---|
| Why not `completed` | Seerr's `completed` takes every completed request whatever the title's state now (partially available, deleted too); `available` only fully available ones |
| Shown | the select shows the first option with the current value: ours, also when Seerr's own Available / Processing was picked |
| Script | `indexer-bar/www/request-filter.js` (injected by `seerr-proxy`); survives React re-renders and the language switch |
| Tests | JS `node --test` in `tests/js` (`request-filter.test.mjs`); check `seerr.request-filter` |

## Request origin

Requests made with Jellyfin's Popular buttons get a pill in the **Requested** line of the Requests list (on the same line, never shrinks: the date and name are cut first) and a **Requested via** row (German **Angefragt über**) after Size on the title page. Requests made in Seerr itself: no pill.

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
| Script | `indexer-bar/www/request-origin.js` (injected by `seerr-proxy`); hint ([hint box](#hint-boxes)): how it was requested; service-user requests add "Requested by: <person>" and the service-user note |
| Tests | `python3 tests/test_download_now.py` (class `Origins`); JS `node --test` in `tests/js` (`request-origin.test.mjs`); Jellyfin `popular/test_app.py` (`Attribution`, `Origins`) |

## Manual interaction

Sidebar → **Manual interaction** (German **Manuell beheben**, amber count of open problems) opens `/manual` inside Seerr's own page (sidebar, phone bottom bar, indexer bar): every download problem that needs a hand, for **every** Seerr user, also on other people's requests (owner 2026-11-15). Tabs **Open** / **Done** (with who did what). One card per title (`movie|tv/<tmdb>`, "2 problems"): each download of it is an item inside with its own buttons; tab counts and the menu badge count problems (each download). Each item reads in sections: what's wrong (plain EN/DE sentence from the arr message or our code; the raw message folded under **Details from Sonarr/Radarr**), the download (file name labelled on its own line, breakable at dots and dashes; one summary line "1080p · English · ✅ checks · ⏳ virus scan"; the full media, checks and virus scan only per file in the opened file list, owner 2026-11-15), what to do, the buttons. **Check files to finish importing** / **Show releases** toggle to **Hide …** (DE **Dateien prüfen und Import abschliessen** / **Dateien ausblenden**, **Releases zeigen / ausblenden**), disabled with "Loading…" while a list loads.

| Problem | Found by | Actions |
|---|---|---|
| Removed from Radarr/Sonarr | `request-state.json` `removed` | **Add again** (request's profile/folder, else Seerr's default; monitored, searched; the add hook applies the profile rules). **Delete request**: admins / Manage Requests only, second tap + warning: `DELETE /api/v1/request/<id>` for each of the title's requests; never the media row (that would run `clear-data-wipe.sh`), title data and files stay (owner 2026-11-15); a delete failing halfway logs which requests already went |
| Not matched (Seerr could not hand it over) | `queue` code `seerr` | Radarr/Sonarr lookup (search box, most probable result preselected: title similarity + year) → **Use this one**: added as above unless already there; same id as Seerr's → the failed Seerr request is retried. Only the requested title (film: same TMDB id; series: Seerr's TVDB id or the same TMDB id); another title: admins only (logged) |
| Downloaded, not importable | Radarr/Sonarr queue `importBlocked` / `importFailed` (incl. "matched by ID", year fits) | **Check files to finish importing**: the arr's manual-import proposals for that download only (`/manualimport?downloadId=<id>&filterExistingFiles=true`, no `seriesId`/`movieId`: with one Sonarr lists the series' library folder instead, seen 2026-11-15; proposals of another download are dropped), probable episode/film preselected (arr's match, else `S01E02` / `1x02` in the name, else the queue item's episode; films: biggest file), every episode of the series to pick from (specials last) → **Import** (`ManualImport`, mode auto). A file the arr rejected for another reason than "could not tell which title/episode" (not an upgrade, …), any sample, or an episode that already has a file: admins only (override logged) |
| Wrong film | same block, release year more than 2 years off | **Block this release and search again** (second tap): queue remove + blocklist + search (episodes or film) |
| Download failed | queue `failed` / `failedPending` | as wrong film |
| Nothing found for days | `noRelease`, oldest request ≥ 3 days; unreleased titles never | **Allow older versions (…)**: the `· older versions allowed` twin (anime → anime twin, English original → its twin; Swiss German or already older: none), suggested for titles ≥ 12 years old, offered for all (niche is not detectable). **Show releases** (interactive search, series per requested season): only releases the arr mapped to this title (Radarr `mappedMovieId`; Sonarr `mappedSeriesId` + the season; indexers that ignore an unknown query send their newest uploads, those are dropped and only counted in the log); allowed ones first (quality, size, GB/h, languages, seeders, score; **Details ↗** = the release page at the indexer, `infoUrl` only: http(s) to a public host, credential parameters (`apikey`, `passkey`, `token`, `r`, `i`, `uid`, …) cut, anything key-like left, an IP, `*.homelab`/`.local`, Prowlarr or gluetun = no link; never `downloadUrl` or a magnet; 🔒 "account needed" for usenet indexers; "📄 Description/NFO mentions: …" when Movie-Downloads' NFO scout read that release, from `nfo-scout.json` mounted read-only at `/run/scouts`; per release what Movie-Downloads' scouts read: "📄 NFO: German, French" / "📄 Description: Swiss German" plus up to 5 matching lines (`nfo-scout.json` `lines`, `description-scout.json` `read|<btih>` or the scout's own Swiss German match); not read yet and readable (usenet NZBgeek / NzbPlanet / Treasure Maps, torrents TPB / Knaben / 1337x): **Read description** (DE **Beschreibung lesen**) = POST `action` `describe` (`release`, `token`): a request file in `/run/scout-requests` (`/srv/appdata/seerr-scout-requests`) for Movie-Downloads' runner (README "Scout requests", which keeps the keys and the scouts' limits); the row shows "Reading the description…" and the page asks again every 3 s until the result is there (3 min at most); "daily limit reached, try tomorrow" when a limit is used up; at most 5 waiting reads per user, 30 in all) → **Download**; the rejected ones folded under "N more, not allowed — show why" with plain reasons (`cf-score`, `required-terms`, `quality`, `existing`, `seeders`, `blocklisted`, `size`), never pickable (after 7 days each gets **Download anyway**, see [Download anyway](#download-anyway)); none matched: "No release found for this title — the indexers don't know a version yet" (+ the older-versions hint) |

| Part | What |
|---|---|
| Page | `indexer-bar/www/manual.js`, injected by `seerr-proxy` on every page like the other scripts. Seerr does not know `/manual` and shows its 404 inside the normal layout; on that route the script puts its page first in `<main>` and hides the 404 by CSS (`main[data-hg-manual]`), keeps the same node and state when React re-renders, removes it on any other route; title "Manual interaction - Seerr". Every text as `textContent`. German from Seerr's German menu (Serien) or the user's Seerr language. `/_manual/` (first version) redirects to `/manual` |
| Reasons | `reason` codes from the arr messages (first match): `id-match` (matched by ID, e.g. a foreign-language name), `sample`, `no-eligible`, `not-upgrade`, `unparsable`, `not-in-release`, `dub-only`, `failed`, plus `wrong-film`, `no-release`, `removed`, `unmatched`; anything else `unknown` = a general sentence with the message itself |
| Media | `ffprobe` (in the `seerr-download-now:local` image, `download-now-image/Dockerfile`) on the files of that download only: resolution, codec, HDR (Dolby Vision with its fallback), audio (language, codec, channels), subtitle languages, size, minutes. Read-only mounts of only `/srv/video/usenet/complete` and `/srv/video/torrents` at the arr paths (`/data/...`, `DN_DOWNLOAD_ROOTS`), never the library: paths are resolved (symlinks too) and must stay inside them; only video extensions; `ffprobe -protocol_whitelist file -format_whitelist matroska,webm,mov,mp4,m4a,3gp,3g2,mj2,avi,mpegts` (no network, no HLS/concat playlists opening other inputs); 20 s per file, at most 20 files and 60 s per list, cached by path + size + mtime. The card probes the download's biggest video (no samples) from its own folder; an output path that is a category folder (`tv`, `movies` right under a root) is never walked, the arr's file list for that download id is used instead. Probes never run while the problem list is locked: after a scan, at most 30 s of probing (`PROBE_SCAN_BUDGET`), the next scan shows the result. Accepted: the backend can read finished downloads; residual risk: a parser bug in ffmpeg on a crafted file, inside the container that holds the API keys (it is read-only, no caps, no network beyond its bridges). Possible later hardening: a separate probe container without secrets |
| Checks | per file and on the card, against the title's quality profile and metadata (thresholds: `RULES` in `manual.py`): see the table below; a ⚠️/❌ row has a [hint box](#hint-boxes) (check in bold, the reason below) |
| Virus scan | ClamAV's per-file verdicts from `../Antivirus` (`clamav-results.jsonl`, read-only mount of `/var/lib/clamav-scan/textfile` at `/run/clamav`): ✅ clean (time), ⏳ pending (queued for the background scan) or not scanned (over 2 GiB, error), ❌ malware found (signature). "Clean" counts only for the very file scanned: logged size = current size and scan time ≥ the file's mtime, else "not scanned". Over clamd's 2 GiB limit (owner 2026-11-15): the Antivirus worker scans the parts around the video/audio bitstreams (`../Antivirus/README.md`); `parts-clean` and a running time within ±25 % of the expected one = import for everyone ("ClamAV: clean (large file: attachments and subtitles scanned, genuine video)"; hint: ClamAV scans at most 2 GiB, so the video stream itself is not scanned); running time off or unknown, or `parts-error` → admins only; `parts-infected` = malware. **Import**: malware → refused for everyone, admins too (403 `malware`); pending / not scanned → admins only (logged), others "wait for the virus scan" |
| Virus block | per file one overall verdict first, then each source as a short grey row in plain words (details in the hint boxes of the rows); wording only, the gates are the backend's: ❌ Malware — import blocked (ClamAV malware or VirusTotal ≥ 3) · ⚠️ Flagged — admins only (VirusTotal 1–2) · ⚠️ Not fully scannable — admins only (`parts-error`) · ⚠️ Running time doesn't match — admins only (`parts-clean` with `runtimeFits` false: the file list's per-file result of the import gate's running-time test, never worked out in the page; ClamAV row "large file: attachments and subtitles clean, genuine video, but running time 50 min vs expected 34 min", the hint box says why) · ⏳ Still being checked (pending / not scanned) · ✅ No virus found (ClamAV clean / parts clean with a fitting running time, VirusTotal anything else). VirusTotal "unknown" reads "no reports — this exact file isn't known there yet, normal for new downloads, not a warning" |
| VirusTotal | per file a row from `virustotal-results.jsonl` (same mount; hash lookups only, `../Antivirus/README.md` "VirusTotal fingerprint check"), valid only for the very file hashed (size, `hashed` ≥ mtime): ❌ known malware (n of N scanners, ≥ 3) → refused for everyone, admins too (403 `malware`); ⚠️ flagged by 1–2 scanners → admins only (403 `flagged`, admin import logged); known file, no scanner flags it; no reports (unknown there); not checked yet (lookup failed, retried); lookup queued (also while ClamAV's scan is queued) — those four change nothing. Details in the hint box (only the SHA-256 is looked up, the file is never uploaded; checked time). Card summary adds only ❌ known malware / ⚠️ flagged by VirusTotal |
| Backend | `download-now/manual.py` in `seerr-download-now`, `/_download-now/manual/`: GET `problems` (`open`, `done`, `admin`), `count`, `history`, `lookup?id=&term=`, `releases?id=[&season=]`, `files?id=`; POST `action` `{id, action, …}`: `readd`, `delete` (+`"confirm":"delete"`), `link` (+`pick`, `token`), `import` (+`files: [{n, episodeIds}]`, `token`), `block`, `profile`, `grab` (+`release`, `token`), `override` (+`release`, `token`) |
| Who | every call: Seerr `auth/me` with the caller's session, like Download now; everything for every user, `delete` and `history` only ADMIN / MANAGE_REQUESTS; POST only with Origin + JSON. Requester names are shown to every user (owner's ask): Seerr username, Jellyfin or Plex name, never the email (else `user <id>`) |
| Guards | picks are indexes into the list the backend itself read for that user and problem in the last 10 min (lookup, release list, file list), sent back with that list's `token`; rejected releases cannot be grabbed; episode ids must belong to the series; file paths, download links (`guid`) and keys never reach the browser; release searches wait in one queue and run one at a time (`QUEUE_MAX` 5 waiting, `QUEUE_PER_USER` 1 waiting + 1 running per user; beyond that "search queue full" / "too many searches", never "too many tries"; finished and failed searches are forgotten after 10 min); the page shows "Waiting for the search for <title> …" and asks again every 3 s until the list is there; the same problem again within 2 min gets the last list; lookups every 2 s per user and problem; a file list is shared by everyone for 10 s and re-opening one's own list within 10 min returns it (`&refresh=1` asks anew, then at most every 10 s per problem); at most `FLOOD_MAX` 20 list calls per user and minute ("too many tries"); actions every 20 s per problem (refused and failed tries count); an action is checked against the last scan first, only then scanned afresh; one scan at a time (waiting calls share it), at most 4 manual calls at once (else 429) |
| Tracking | `/srv/appdata/seerr-download-now/manual.json` (`/state`, 65127 0700, the container's only writable path): per problem `id`, `type`, `key` (`movie\|tv/<tmdb>`), title, year, requesters, `firstSeen`, `lastSeen`, `details`, `actions` (time, user, action, result), `resolved` (`at`, `how`: the action, or when the condition went by itself `imported` for import problems, `found` for nothing-found ones, else `gone`; Done shows "Fixed by <user> (<action>)", "Removed by admin", "Imported", "Found and downloaded", "Went away by itself"), `ident` (type + download id, or type + key). A problem whose key changes (e.g. Sonarr's TMDB id → Seerr's) keeps its record: older records of the same download (or, from before `ident`, same type and release name) are folded into it, first seen and history kept, a resolution caused by the key change dropped. Scanned every 10 min and on page/badge calls (at most once a minute); condition gone → Done (only when every source was readable: request-state fresh, Seerr list under 1000 requests, both queues answered); unreadable `manual.json` → never overwritten (logged), each save keeps `manual.json.bak`; fixed by an action → Done, back to Open if still there after 30 min; Done kept 180 days (a download-anyway record until a version the profile allows replaced it) |
| Sources | `request-state.json` (read-only mount; older than 10 min = ignored), Seerr `/api/v1/request` (Seerr's key: requesters, ids), Radarr/Sonarr `/queue`. A queue item finds its Seerr request by TMDB id, else by Seerr's Radarr/Sonarr id (`externalServiceId`) or TVDB id (Sonarr may hold another TMDB id than Seerr) and then uses Seerr's key |
| Log | `sudo docker logs seerr-download-now`: one `manual:` line per action (user id, action, title, result) |
| Tests | `python3 tests/test_manual.py`; JS `node --test` in `tests/js` (`manual.test.mjs`, `menu-links.test.mjs`); scan log: `python3 ../Antivirus/tests/test_hook.py` |

| Check (✅ ⚠️ ❌, – = no rule) | Rule |
|---|---|
| Original language (Radarr/Sonarr `originalLanguage`) | ✅ in the audio, ❌ missing (dub only) |
| English audio (not for English originals) | ✅ present, ⚠️ missing |
| German audio or subtitles | ✅ present, – otherwise |
| Quality (arr's quality of the file) | ✅ allowed by the profile (also below its cutoff: grey "may upgrade to … later"), ❌ not allowed (owner 2026-11-15) |
| HDR | ✅ HDR10, HLG, Dolby Vision with HDR10/HLG fallback, SDR (grey "may upgrade to 4K HDR later" on a 4K HDR profile), ❌ Dolby Vision without HDR fallback |
| Running time vs expected (Sonarr episode runtime, else series/film runtime) | ✅ within `runtime_ok` 10 %, ⚠️ up to `runtime_warn` 25 %, ❌ more (wrong file, sample, extras); "31 min (expected 30)" |
| Size | size and GB/h, ⚠️ above `gb_per_hour_film` 30 / `gb_per_hour_episode` 20 (as the Requests page), never ❌ |

| Check | Command |
|---|---|
| Open problems (Seerr key) | `curl -s -H "X-Api-Key: $(cat secrets/api-key)" localhost:5055/_download-now/manual/problems \| jq '.open[] \| {type, title}'` |
| Export for review | same with `/_download-now/manual/history` |

### Download anyway

A title with nothing found for **7 days** (oldest request, `OVERRIDE_DAYS`; same clock as the 3 days) gets **Download anyway** (DE **Trotzdem herunterladen**) on every not-allowed release in its list, the reasons in plain words beside it (owner 2026-11-15). Before that the rejected ones stay unpickable.

| Part | What |
|---|---|
| Who | every user of the page (requesters too); the same guards as **Download**: only releases Radarr/Sonarr mapped to this title, the user's own list token (10 min), action limits; a not rejected release or a press before day 7 → refused (400 / 403 `too early`). Malware rules unchanged (import gate) |
| Press | the title gets the tag `override` (created once), then `POST /release` with the release's guid + indexerId; Done shows "Downloaded anyway by <user>", the record keeps `override` (release, seeders, protocol, user, state) |
| Card | "⬇️ Downloading anyway (0 seeders — waits for a seeder, checked every night for a better version)"; with seeds "(n seeders — …)"; finished "✅ Downloaded anyway — …"; "🔄 A version the profile allows was found — it downloads instead"; "✅ Replaced by a version the profile allows". Seeds from qBittorrent (`torrents/info`, read only), the download ids learned from the arr queue (release title) at each scan |
| Every night | the backend's own loop, 04:00–04:40 (`NIGHT_CHECK`, beefy's maintenance window), once per night per open override: in the queue → interactive search; a release whose only rejections come from the override itself (queue, existing file, not an upgrade) = one the profile accepts on its own → override download removed from the queue (no blocklist) + normal search (`MoviesSearch` / `EpisodeSearch`), which grabs it. Not in the queue → normal search (the arr's upgrade flow replaces the file). An import of another download after the override (series: every episode it brought) = replaced: tag `override` removed, checks end. Never gives up otherwise; such a record is never dropped from `manual.json` |
| Seedless | never wakes or holds beefy: `../Movie-Downloads/README.md` "Can wait: downloads at night" (Seedless override) |
| Tests | `python3 tests/test_manual.py` (class `Override`); JS `manual.test.mjs` (Download anyway, states EN/DE) |
| Log | `sudo docker logs seerr-download-now \| grep 'manual override'` |

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
