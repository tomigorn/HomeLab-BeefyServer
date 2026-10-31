# Floppy (Tracker)

Per-user tracker for films, series, books and games (ratings, history, want-lists). Dashboard tile **Tracker** (Media). Replaced Ryot on 2026-10-26, fresh start (no Ryot data moved). Upstream: `dannyvfilms/Floppy` (AGPL-3.0, fork of Yamtrack). Security review: owner's report `floppy-security-review.md` (2026-10-26).

| | |
|---|---|
| URL | https://tracker.holy-grail.ch (Holy Grail, group `streaming-users`); wakes beefy |
| Image | `ghcr.io/dannyvfilms/floppy:26.10.29@sha256:5e88307d…`, `redis:8.10.3-alpine@sha256:896f241f…` (`.env`) |
| Containers | `floppy` (host port `8012` → 8000), `floppy-redis` (no port) |
| Networks | `floppy` `172.28.16.0/24` (egress: internet, tcp 80/443 only), `floppy-redis` `172.28.17.0/24` (`internal`) |
| Data | `/srv/appdata/floppy/db` (SQLite), `/srv/appdata/floppy/backups` (snapshots, CSV exports), `/srv/appdata/floppy-redis` (AOF) |
| Traefik / Authentik | `HomeLab-FastPi` → `Traefik/traefik/dynamic/tracker.yml`; apps `tracker` (forward auth) and `tracker-login` (OIDC, hidden), `Authentik/scripts/provision-tracker-sso.py` |

## Security

Review `floppy-security-review.md` (2026-10-26), mitigated without code changes: edge (`HomeLab-FastPi` `Traefik/traefik/dynamic/tracker.yml`), firewall (`_global/scripts/docker-user-guard.sh`), env. Who can reach the app at all: only Holy Grail users in `streaming-users` (forward auth on every path except the outpost callback, the PWA favicon/manifest files and the 403 routers).

| # | Sev | Finding | Needs | Status | Mitigation |
|---|---|---|---|---|---|
| 1 | High | SSRF: integration URLs may point anywhere | signed-in friend | mitigated | Egress of the `floppy` subnet: tcp 80/443 to the internet only, LAN/Docker/host/CGNAT/link-local dropped, rest dropped + logged `FLOPPY-EGRESS drop`. Every integration that takes a server URL is 403 at the edge: `/import/{audiobookshelf,kavita,komga,radarr,sonarr,mylar,kapowarr,storyteller,koreader,plex,koito,gpodder}`, `/jellyfin/`, `/seerr/`, `/settings/integrations/jellyseerr/`; open: only GET `/import/audiobookshelf/cover/<signed>` (ABS covers, served from the copies abs-push stores). Integrations are wired on beefy (Integrations), none needs Floppy LAN egress |
| 2 | High | `User.token` in webhook/ICS/Stremio/RSS URLs = full API key | leaked URL | mitigated | 403 for `/webhook/`, `/calendar/download/`, `/stremio-addon/`, `/apis/`, `/rss/` and all of `/api/` (UI needs only `/api/cache-status/`); that router writes no access log. Port 8012 answers fastpi and the `jellyfin` subnet only (Jellyfin and Seerr webhooks, beefy-local; other containers `FLOPPY-INGRESS drop`) |
| 3 | Medium | Custom metadata / image override edit the shared item | signed-in friend | mitigated | `/details/metadata/`, `/details/image/` 403 (API blocked too); CSP `img-src` = Floppy + its approved artwork hosts, so a foreign image URL cannot track viewers |
| 4 | Medium | One user's TMDB proxy applies instance-wide | signed-in friend | mitigated | `/update_tmdb_proxy` 403; check in "Checks" stays `0` |
| 5 | Medium | Comic-issue synopsis rendered `\|safe` (XSS) | third-party data | mitigated | `COMICVINE_API=""` (no bundled ComicVine key), GCD has no login, credential pages and every comic URL 403 (`/…/(comic\|comicissue\|comicvine\|gcd)/`); CSP: `connect-src 'self'`, `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'` |
| 6 | Medium | Django cache uses pickle in Redis | network access to Redis | mitigated | Redis on `internal` net, `requirepass` from `secrets/`, read-only, no port |
| 7 | Medium | Insecure defaults | direct access to the port | mitigated | Env below; port answers fastpi only (DOCKER-USER); HSTS at Traefik (Cloudflare sets its own max-age); `REGISTRATION` gated by the group on both Authentik apps |
| 8 | Low | Container runs as root | code execution first | partial | `cap_drop: ALL` + 6 caps, `no-new-privileges`, no socket, `mem_limit`/`pids_limit`; root fs stays writable (entrypoint) |
| 9 | Low | Own OAuth device server, anonymous client registration | signed-in friend | mitigated | `/oauth/`, `/.well-known/` have no router (404) |
| 10 | Low | Local password / recovery leftovers | signed-in friend | mitigated | `SOCIALACCOUNT_ONLY`; POST `/accounts/login/`, `/accounts/signup/`, `/accounts/password/` 403; OIDC users have no usable password, so the change form on `/settings/account` cannot set one |
| 11 | Low | Info endpoints (`/health/full/`, `/api/docs`, …) | signed-in friend | mitigated | No router (404) or `/api/` 403 |
| 12 | Info | Dependency CVEs (oauthlib server side, not used) | — | monitored | Pinned by digest; nightly SBOM to Dependency-Track (fastpi `security-dt.timer` 04:02) |

Extra edge controls (`tracker.yml`): only GET/HEAD/POST/DELETE; 64 MB body limit; `/search` 3 req/s per client (burst 30); `Permissions-Policy` (camera for the barcode scanner only); `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Cross-Origin-Opener-Policy: same-origin`.

Stays public on purpose: friends use it from anywhere, and nothing left is admin-only (no superuser, `/admin` off, superuser pages refuse everyone).

### Edge allow-list

The catch-all router matches only `/`, `/serviceworker.js`, `/api/cache-status/` and these first path segments (Floppy 26.10.29; new and still closed: `covers`, `youtube-player`): everything else is 404 at Traefik, so new upstream features stay closed until added on purpose. After every update compare:

```bash
sudo docker exec -i -u abc -w /floppy floppy python manage.py shell < scripts/url-prefixes.py   # PREFIXES …
```

Left out on purpose: `api` (except cache-status), `apis`, `health`, `ping`, `oauth`, `.well-known`, `webhook`, `stremio-addon`, `rss`, `jellyfin`, `seerr`, `update_tmdb_proxy`, the Plex webhook toggles (`*plex*`), `update_jellyfin_webhook_events`.

New integrations: prefer a webhook into beefy:8012 (DOCKER-USER exception for the sender's subnet) or a public HTTPS URL; configure per user from a script here, not by opening the settings path at the edge.

### Residual risks (need code changes upstream)

- Friends can still do everything the UI allows inside their own account; a malicious friend is out of scope (invite only).
- CSP needs `'unsafe-inline'` and `'unsafe-eval'` (Alpine.js, inline handlers): it limits where a script can send data, not whether an injected script runs. Unknown XSS sinks elsewhere stay possible.
- SSRF to the container's own loopback (nginx :8000, gunicorn :8001) via existing integrations; none exist and new ones are blocked.
- `User.token` stays plaintext in the DB; rotate in Settings if a URL leaked.
- New sub-paths under an allowed prefix pass the allow-list (e.g. a new `/settings/…` page).
- Podcast/other artwork from hosts outside the approved list does not render (CSP).

## Login

- Holy Grail forward auth, then Floppy jumps to **Sign in with Holy Grail** (OIDC `tracker-login`, allauth provider id `authentik`, callback `/accounts/oidc/authentik/login/callback/`).
- `SOCIALACCOUNT_ONLY=True`: no passwords. `REGISTRATION=True`: an account is created on first login; only `streaming-users` get that far.
- No user is admin (`/admin` off). Login page shows one "Holy Grail" button: `REDIRECT_LOGIN_TO_SSO` is off because Floppy's auto-submit sent the login 2-3 times and the extra callbacks failed ("Third-Party Login Failure").

## Secrets

Files under `secrets/` (gitignored); `docker inspect` shows no value. How-to: `../_global/docs/secrets-as-files.md`.

| File | Owner / mode | Read by |
|---|---|---|
| `secrets/floppy_secret` | `65125:65125 0400` | Django `SECRET_FILE`. Keep it with the DB backup: it also encrypts saved integration credentials |
| `secrets/floppy_redis_password` | `65126:65125 0440` | redis (config on tmpfs), floppy builds `REDIS_URL` in its compose `entrypoint` |
| `secrets/floppy_oidc_providers` | `65125:65125 0400` | allauth `SOCIALACCOUNT_PROVIDERS_FILE`; written from fastpi by `provision-tracker-sso.py` |
| `secrets/floppy_seerr_webhook_secret` | `65125:65125 0400` | `SEERR_GLOBAL_WEBHOOK_SECRET_FILE`; Seerr gets it from `../Seerr/scripts/seerr-webhook.py apply` |
| `secrets/floppy-sync-abs-key` | `root:root 0600` | ABS API key of the service user `floppy-sync` (expires after 365 days, rotated automatically, see "Key rotation"), read only by `floppy-abs-push` (`LoadCredential`) and `floppy-abs-key-rotate`, never by Floppy; `sudo scripts/abs-push.py store-key` |

## Deploy

```bash
sudo mkdir -p /srv/appdata/floppy/db /srv/appdata/floppy/backups /srv/appdata/floppy-redis
sudo chown -R 65125:1000 /srv/appdata/floppy && sudo chown 65126:1000 /srv/appdata/floppy-redis
sudo chmod -R 2770 /srv/appdata/floppy /srv/appdata/floppy-redis
cd ~/Projects/Docker/Floppy && sudo docker compose up -d     # secrets/ filled first
sudo docker inspect -f '{{.State.Health.Status}}' floppy    # healthy (first start runs ~560 migrations, ~2 min)
```

## Checks

| What | Command / expected |
|---|---|
| Egress blocked | `sudo docker exec floppy python3 -c 'import socket; socket.create_connection(("192.168.1.2",443),3)'` → timeout |
| Egress internet | same with `("api.themoviedb.org",443)` → no error |
| Token URLs | `curl -s -o /dev/null -w '%{http_code}' -X POST https://tracker.holy-grail.ch/webhook/jellyfin/x` → `403` |
| Webhook path | `sudo docker exec jellyfin curl -s -o /dev/null -w '%{http_code}' http://192.168.1.102:8012/` → `302`; from another container → timeout |
| Egress port | same with `("portquiz.net",8080)` → timeout; `sudo journalctl -k \| grep FLOPPY-EGRESS` shows drops |
| Edge blocks | `curl -s -o /dev/null -w '%{http_code}' https://tracker.holy-grail.ch/api/v1/health/` → `403`; `/health/full/` (signed in) → `404` |
| TMDB proxy unused | `sudo docker exec -u abc -w /floppy floppy python manage.py shell -c "from users.models import User; print(User.objects.exclude(tmdb_proxy_url='').count())"` → `0` |
| Users | same with `print(list(User.objects.values_list('username', flat=True)))` |

## Backup / restore

- Floppy's own task writes a verified SQLite snapshot daily **04:20** (`DB_SNAPSHOT_HOUR/MINUTE`, inside the 04:00–06:00 maintenance wake) to `/srv/appdata/floppy/backups/database/`, keeps 7. A missed run (beefy off) is not caught up.
- Cold copy before every update: `update-project.sh` via the `notify` row in `_global/auto-update.conf` (tars `/srv/appdata/floppy`).
- Snapshot by hand: `sudo docker exec -u abc -w /floppy floppy python manage.py shell -c "from app.tasks_db_backup import write_database_snapshot; print(write_database_snapshot())"`
- Restore (same image version, same `floppy_secret`):

```bash
sudo docker compose stop floppy
sudo mv /srv/appdata/floppy/db/db.sqlite3{,.broken}; sudo rm -f /srv/appdata/floppy/db/db.sqlite3-{wal,shm}
sudo cp /srv/appdata/floppy/backups/database/db-<stamp>.sqlite3 /srv/appdata/floppy/db/db.sqlite3
sudo chown 65125:65125 /srv/appdata/floppy/db/db.sqlite3 && sudo docker compose start floppy
```

## Update

> **Update caution: the ABS push uses Floppy internals.** `scripts/abs-push-apply.py` calls Floppy's own code, not a public API: `integrations.imports.audiobookshelf.AudiobookshelfImporter` (`import_data`, `.client.get_me()` / `.client.get_library_item(id, expanded=)`), `decrypt_or_raise`, `AudiobookshelfAccount`, `app.image_cache.store_cover` / `MAX_IMAGE_BYTES` (cover key `abs-cover:<account>:<item>`), `helpers.periodic_task_user_kwargs`. A Floppy release that renames or changes any of them breaks the sync. Guard: auto-update tier `notify` (no unattended Floppy update); `update-project.sh` runs the post-update check, `floppy.abs-push-selftest` (`ok dry-run`) fails → automatic rollback. After an update also: `sudo systemctl start floppy-abs-push && journalctl -u floppy-abs-push -n 10 -o cat` → `ok <user> …` lines.

Monthly at most, by hand: read the release notes (migrations are one-way), then bump `FLOPPY_TAG` (tag + digest) in `.env` and `.env.example`:

```bash
../_global/scripts/update-project.sh ~/Projects/Docker/Floppy pull /srv/appdata/floppy
```

After the update: run `scripts/url-prefixes.py` (Security, "Edge allow-list") and decide on every new prefix before adding it to `tracker.yml`.

Digest of a tag: `docker buildx imagetools inspect ghcr.io/dannyvfilms/floppy:<tag> --format '{{json .Manifest.Digest}}'`.

## Integrations

Per Floppy user. Floppy opens no connection into the LAN for any of them.

| Integration | Direction / path | Credential | Owner (jane) |
|---|---|---|---|
| Jellyfin (playback, watched checkmark) | Jellyfin Webhooks plugin → `http://192.168.1.102:8012/webhook/jellyfin/<token>` (hairpin on beefy) | the user's Floppy token, bound to their Jellyfin user | on |
| Seerr (request → Planning) | Seerr webhook → `http://192.168.1.102:8012/webhook/seerr/global/`, routed by requester name | shared secret (Secrets) | on |
| Audiobookshelf (listening progress, covers) | `floppy-abs-push.timer` (beefy, every 15 min while awake): reads ABS, feeds Floppy's own ABS importer via `docker exec` | one key of the ABS service user `floppy-sync` (admin), on the host only (`secrets/floppy-sync-abs-key`); every ABS user with a Floppy account of the same username, automatically; Floppy's account rows hold a placeholder | on once the key is stored |

Not used: Floppy → ABS pull (key in Floppy + egress; ABS has no progress webhook), Floppy → Jellyfin pull/push (would need egress to Jellyfin), request from Floppy (needs Seerr's single admin API key), ItemAdded/ItemDeleted (every new library item in every member's collection).

### Link a member
The member has signed in to the Tracker once (creates the Floppy user); usernames = Holy Grail usernames.

| What | Command on beefy |
|---|---|
| Jellyfin | `cd ../Jellyfin/scripts && python3 tracker-webhook.py add <jellyfin-user> [<floppy-user>]` (also turns on MarkPlayed/MarkUnplayed) |
| Seerr | `sudo docker exec -u abc -w /floppy floppy python manage.py shell -c "from users.models import User; User.objects.filter(username='<floppy-user>').update(jellyseerr_enabled=True, jellyseerr_allowed_usernames='<seerr-name>')"` |
| ABS | nothing: synced from the next run (≤ 15 min) when the ABS username = the Floppy username |
| ABS off for one user | add the username to `/etc/floppy-abs-push/exclude` (root, one per line, `#` comments); already-imported entries stay in Floppy |

### Audiobookshelf push

ABS 2.37 sends no progress events (notifications: backups, podcast downloads, RSS only), and Floppy has no inbound ABS endpoint, so a host service pushes. Owner decision 2026-10-27: one ABS service key for all users instead of a key per user.

| | |
|---|---|
| Install | `sudo install -d -m 0755 /usr/local/lib/floppy-abs-push && sudo install -o root -g root -m 0644 scripts/abs-push.py scripts/abs-push-apply.py scripts/abs-key-rotate.py /usr/local/lib/floppy-abs-push/ && sudo install -m 0644 systemd/floppy-abs-push.* systemd/floppy-abs-key-rotate.* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now floppy-abs-push.timer floppy-abs-key-rotate.timer` |
| Root-owned copy | the unit runs `/usr/local/lib/floppy-abs-push/abs-push*.py` (root:root 644), not the buntu-writable repo files: after every change to `scripts/abs-push*.py` or `scripts/abs-key-rotate.py` repeat the first two `install` commands (post-update check `floppy.abs-push-root-copy`) |
| Key | ABS (as an admin) → Settings → Users → add `floppy-sync`, type Admin, random password; Settings → API Keys → Add, name `floppy-sync`, acting as `floppy-sync` (expiry: the rotation sets 365 days); then `ssh -t beefy 'sudo ~/Projects/Docker/Floppy/scripts/abs-push.py store-key'` (hidden prompt; checks `GET /api/me` = `floppy-sync`, admin, active, else stores nothing) |
| targets | root (`ExecStartPre=+`): active Floppy usernames minus `abr`, `audiobook-importer`, `floppy-sync`, `root` and `/etc/floppy-abs-push/exclude` → `targets.json` |
| fetch | `DynamicUser`, key via `LoadCredential`, HTTPS to the fixed ABS URL only, **GET only** (client refuses any other method, paths outside `/api/` and redirects; tested); `/api/me` (must be `floppy-sync`), `/api/users`, `/api/users/<id>` per matching active non-root user (mediaProgress), then (only when a `lastUpdate` changed or a cover is >20 h old) every item + cover → spool `/var/lib/floppy-abs-push/pending/` |
| apply | root (`docker exec` into floppy): lists the targets again itself and refuses a spool file whose name is no target or whose `payload.user` differs (the spool is written by the unprivileged fetch step); `scripts/abs-push-apply.py` runs Floppy's `AudiobookshelfImporter` on the spool, stores covers in Floppy's cover cache; spool → state only after `RESULT ok` |
| Logs | `journalctl -u floppy-abs-push -n 20 -o cat` (`targets: N`, then one line per user: rows, imported, covers, warnings) |
| Selftest | `sudo scripts/abs-push.py selftest` → `ok dry-run` (synthetic book, rolled back) |
| Tests | `cd scripts && python3 -m unittest test_abs_push test_abs_key_rotate` |

Why admin: ABS 2.37 returns another user's mediaProgress only from `GET /api/users/:id` (and the list from `GET /api/users`), both `isAdminOrUp`; a `user`/`guest` key gets 403. Admin (not root) is the minimum.

**Security (floppy-sync key is admin-capable):** ABS API keys have no read-only scope, so the key could change or delete anything in ABS and read every user (incl. their legacy tokens). Mitigations: file `root:root 0600` on beefy, only the sandboxed fetch gets it (`LoadCredential`, `DynamicUser`, no home, AF_INET only); the client sends GET only and follows no redirects; Floppy never holds it (edge 403 on `/import/audiobookshelf/` stays). Never put it anywhere else.

| | |
|---|---|
| Key rotation | ABS API keys can expire: the key expires after 365 days. Timer `floppy-abs-key-rotate` (Wed 04:27, maintenance window) runs `scripts/abs-key-rotate.py auto` (root-owned copy): 45 days before expiry (or when it never expires) it creates a new `floppy-sync` key with the old one, checks expiry ≈ 365 days and `/api/me` = `floppy-sync` admin, stores it, deletes the old key with the new one; a failed check deletes the new key, the old stays. Its ABS client allows only `/api/me` and `/api/api-keys` |
| Alert | Telegram straight from beefy (`_global/scripts/oob-alert.sh`): rotation failed, or < 30 days left. State `/var/lib/floppy-abs-key/status.json` (`key-id` = current key id); post-update check `floppy.abs-key-rotate` |
| Rotate now | `sudo python3 -I /usr/local/lib/floppy-abs-push/abs-key-rotate.py rotate` → `ok rotated=True days_left=365`. By hand without the script: ABS → Settings → API Keys → Add a new `floppy-sync` key (acting as `floppy-sync`, 365 days), `ssh -t beefy 'sudo ~/Projects/Docker/Floppy/scripts/abs-push.py store-key'`, delete the old key in ABS, `echo <new key id> \| sudo tee /var/lib/floppy-abs-key/key-id` |
| Revoke (leak) | ABS → Settings → API Keys → delete `floppy-sync` (or Users → `floppy-sync` → disable); `sudo rm Docker/Floppy/secrets/floppy-sync-abs-key`; `sudo systemctl disable --now floppy-abs-push.timer` |

Seerr name = Seerr display name (the Jellyfin username), case-insensitive. Test: mark a film played in Jellyfin → it shows as completed in the member's Tracker; `sudo docker logs floppy 2>&1 | grep -i -E "webhook_finished|Seerr accepted|Audiobookshelf" | tail`.
