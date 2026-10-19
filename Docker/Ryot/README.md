# Ryot (Tracker)

Per-user watch history & ratings that outlive Jellyfin's files. Dashboard tile **Tracker** (Media). Moved here from fastpi on 2026-10-20 (fastpi stays light); same database, same integration slugs.

| | |
|---|---|
| URL | https://tracker.holy-grail.ch (Holy Grail, group `streaming-users`); wakes beefy, ~45 s |
| Image | `ghcr.io/ignisda/ryot:v10.5.3`, `postgres:18.6-alpine` |
| Containers | `ryot` (host port `8010` → 8000), `ryot-db` (no port), bridge `ryot` |
| Port | `8010`, reachable from fastpi only (DOCKER-USER rule, `/etc/iptables/rules.v4` + `rules.v6`) |
| Database | `/srv/appdata/ryot-db` (NVMe bind mount) |
| Traefik / Authentik | `HomeLab-FastPi` → `Traefik/traefik/dynamic/tracker.yml`; apps `tracker` (forward auth) and `tracker-login` (OIDC, hidden), `Authentik/scripts/provision-tracker-sso.py` |

## Routes (on fastpi's Traefik)

| Path | Gate | Wakes beefy |
|---|---|---|
| `/outpost.goauthentik.io/` | none (Authentik callback) | no |
| `/_i/<slug>` | `local-networks@file` only; the slug is the secret, tunnel = 403 | **no**: a webhook only fires while Jellyfin plays |
| everything else | `authentik@file`, `tracker-wake` (`/gate?port=8010`), rate limit, headers | yes, after sign-in |

## Login

- Holy Grail forward auth, then Ryot **Sign in with Holy Grail** (OIDC `tracker-login`, redirect `/api/auth`).
- `USERS_DISABLE_LOCAL_AUTH=true`: no passwords. `USERS_ALLOW_REGISTRATION=true`: OIDC users are only created while it is on.
- First user = Ryot admin (the owner, done).

## Env (`.env`, secrets blank in `.env.example`)

| Key | Value |
|---|---|
| `RYOT_DB_PASSWORD` | secret, also builds `DATABASE_URL` in compose |
| `SERVER_ADMIN_ACCESS_TOKEN` | secret |
| `SERVER_OIDC_CLIENT_ID`, `SERVER_OIDC_CLIENT_SECRET` | secret, written from fastpi by `provision-tracker-sso.py` (`HomeLab-FastPi` `Authentik/README.md`) |
| `MOVIES_AND_SHOWS_TMDB_ACCESS_TOKEN` | secret, TMDB "API Read Access Token" |

`ryot` has `extra_hosts: sso.holy-grail.ch:192.168.1.2`: OIDC discovery goes straight to fastpi's Traefik.

## Deploy

```bash
sudo mkdir -p /srv/appdata/ryot-db
cd ~/Projects/Docker/Ryot && sudo docker compose up -d
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/health    # 200
```

## Backup / restore

- `scripts/ryot-db-backup.sh`: `pg_dump -Fc` to `~/backups/ryot/ryot-YYYY-MM-DD.dump`, keeps 14 days.
- User timer daily **04:10**: inside the nightly maintenance wake (fastpi wakes beefy at 03:55 for the 04:00–06:00 mover window). `Persistent=true`: a missed run (beefy off) runs at the next boot.
- Install: `cp systemd/* ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now ryot-db-backup.timer`
- Restore (Ryot stopped):

```bash
sudo docker compose stop ryot
sudo docker exec -i -u postgres ryot-db pg_restore -U ryot -d ryot --clean --if-exists < ~/backups/ryot/ryot-YYYY-MM-DD.dump
sudo docker compose start ryot
```

## Resources

`docker stats` 2026-10-20, idle after the move: `ryot` 216 MiB, `ryot-db` 50 MiB.

## Update

Bump `RYOT_TAG` in `.env` and `.env.example`, then:

```bash
scripts/ryot-db-backup.sh
sudo docker compose pull && sudo docker compose up -d
```
