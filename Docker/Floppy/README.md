# Floppy (Tracker)

Per-user tracker for films, series, books and games (ratings, history, want-lists). Dashboard tile **Tracker** (Media). Replaced Ryot on 2026-10-26, fresh start (no Ryot data moved). Upstream: `dannyvfilms/Floppy` (AGPL-3.0, fork of Yamtrack). Security review: owner's report `floppy-security-review.md` (2026-10-26).

| | |
|---|---|
| URL | https://tracker.holy-grail.ch (Holy Grail, group `streaming-users`); wakes beefy |
| Image | `ghcr.io/dannyvfilms/floppy:26.10.22@sha256:c4959b1f…`, `redis:8.10.3-alpine@sha256:896f241f…` (`.env`) |
| Containers | `floppy` (host port `8012` → 8000), `floppy-redis` (no port) |
| Networks | `floppy` `172.28.16.0/24` (egress: internet only), `floppy-redis` `172.28.17.0/24` (`internal`) |
| Data | `/srv/appdata/floppy/db` (SQLite), `/srv/appdata/floppy/backups` (snapshots, CSV exports), `/srv/appdata/floppy-redis` (AOF) |
| Traefik / Authentik | `HomeLab-FastPi` → `Traefik/traefik/dynamic/tracker.yml`; apps `tracker` (forward auth) and `tracker-login` (OIDC, hidden), `Authentik/scripts/provision-tracker-sso.py` |

## Security controls

| Risk (review) | Control |
|---|---|
| SSRF: users enter integration URLs, the server fetches them (LAN allowed upstream) | `docker-user-guard.sh` `FLOPPY_NET`: drops 10/8, 172.16/12, 192.168/16, 100.64/10, 169.254/16 from the `floppy` subnet; the host is `BEEFY-INPUT`. Only the internet is reachable (sso via Cloudflare) |
| Webhook/calendar/Stremio/ListenBrainz URLs carry the user token = full API key | No webhooks in use. Traefik router `tracker-token-urls` answers 403 for `/webhook/`, `/calendar/download/`, `/stremio-addon/`, `/apis/listenbrainz/` |
| Pickle cache in Redis | Redis only on the internal net, `requirepass` from `secrets/`, read-only fs |
| Insecure defaults | `ALLOWED_HOSTS`, `URLS`, secure cookies, `USE_X_FORWARDED_PROTO` (port answers fastpi only), `SOCIALACCOUNT_ONLY`, admin/demo off |
| Root container (no non-root mode) | `cap_drop: ALL` + CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, KILL; `no-new-privileges`; app processes run as `abc` = 65125. Root fs writable (the entrypoint runs `usermod`) |
| Fast-moving, mostly AI-written upstream | Pinned by digest, `notify` tier only, update by hand after reading the release notes |
| Shared metadata edits / TMDB proxy per user (no fix) | Only trusted members; check `tmdb_proxy_url` stays empty (below) |
| Comics synopsis `\|safe` (XSS) | Don't configure ComicVine/GCD |

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

Monthly at most, by hand: read the release notes (migrations are one-way), then bump `FLOPPY_TAG` (tag + digest) in `.env` and `.env.example`:

```bash
../_global/scripts/update-project.sh ~/Projects/Docker/Floppy pull /srv/appdata/floppy
```

Digest of a tag: `docker buildx imagetools inspect ghcr.io/dannyvfilms/floppy:<tag> --format '{{json .Manifest.Digest}}'`.

## Not set up yet

- Jellyfin playback sync: Floppy's Jellyfin webhook (token URL) would need Jellyfin → Floppy inside beefy (a shared internal network), not through Traefik. The old Ryot hooks in Jellyfin still point at `/_i/…`, which now ends at the forward auth.
- Audiobookshelf sync: ABS runs on fastpi (LAN), which the egress rules block; needs a narrow exception (fastpi ABS port only) or the public URL with a forward-auth bypass for its API.
- Seerr, *arr: same egress question; Seerr is on beefy (`jellyfin` net).
