# Ryot (Tracker)

Per-user watch history & ratings that outlive Jellyfin's files. Dashboard tile **Tracker** (Media). Moved here from fastpi on 2026-10-20 (fastpi stays light); same database, same integration slugs.

| | |
|---|---|
| URL | https://tracker.holy-grail.ch (Holy Grail, group `streaming-users`); wakes beefy, ~45 s |
| Image | `ghcr.io/ignisda/ryot:v10.5.3`, `postgres:18.6-alpine` |
| Containers | `ryot` (host port `8010` → 8000), `ryot-go` (host port `8011` → 8080, built from `go/`), `ryot-db` (no port), bridge `ryot` |
| Ports | `8010`, `8011`, reachable from fastpi only (DOCKER-USER rule, `/etc/iptables/rules.v4` + `rules.v6`) |
| Database | `/srv/appdata/ryot-db` (NVMe bind mount) |
| Traefik / Authentik | `HomeLab-FastPi` → `Traefik/traefik/dynamic/tracker.yml`; apps `tracker` (forward auth) and `tracker-login` (OIDC, hidden), `Authentik/scripts/provision-tracker-sso.py` |

## Routes (on fastpi's Traefik)

| Path | Gate | Wakes beefy |
|---|---|---|
| `/outpost.goauthentik.io/` | none (Authentik callback) | no |
| `/_i/<slug>` | `local-networks@file` only; the slug is the secret, tunnel = 403 | **no**: a webhook only fires while Jellyfin plays |
| `/go/tmdb/<movie\|show>/<id>?title=` | same as everything else → `ryot-go` | yes, after sign-in |
| everything else | `authentik@file`, `tracker-wake` (`/gate?port=8010`), rate limit, headers | yes, after sign-in |

## ryot-go (Jellyfin "Rate in Tracker" link)

Ryot has no URL by TMDB id, so `ryot-go` looks it up: `metadata` row (`source=tmdb`, `lot`, `identifier`) → 302 `/media/item/<met_id>`; not in Ryot yet → `/media/search/<kind>?query=<title>&source=TMDB`; no title → `/`; bad kind or non-numeric id → 400. The link itself: `Docker/Jellyfin/README.md`, "Rate in Tracker".

| | |
|---|---|
| Code | `go/app.py` (stdlib + psycopg 3), tests `cd go && python3 -m unittest -v` |
| DB | role `ryot_go`, `SELECT` on `metadata` only, password `RYOT_GO_DB_PASSWORD` |
| Check | `curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' 'http://127.0.0.1:8011/go/tmdb/movie/603?title=The%20Matrix'` → `302 …` |

### Setup (once, and after a restore into a new database cluster: roles are not in the dump)

```bash
cd ~/Projects/Docker/Ryot && PW=$(sed -n 's/^RYOT_GO_DB_PASSWORD=//p' .env)
sudo docker exec -i -e PW="$PW" ryot-db sh -c 'psql -v ON_ERROR_STOP=1 -v pw="$PW" -U ryot -d ryot' <<'SQL'
CREATE ROLE ryot_go LOGIN PASSWORD :'pw';
GRANT CONNECT ON DATABASE ryot TO ryot_go;
GRANT USAGE ON SCHEMA public TO ryot_go;
GRANT SELECT ON metadata TO ryot_go;
SQL
sudo docker exec ryot-db psql -U ryot_go -d ryot -c 'select count(*) from "user"'   # permission denied
sudo docker exec ryot-db psql -U ryot_go -d ryot -c 'select count(*) from metadata'  # a number
```

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
cd ~/Projects/Docker/Ryot && sudo docker compose up -d --build
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

`docker stats` 2026-10-20, idle after the move: `ryot` 216 MiB, `ryot-db` 50 MiB; `ryot-go` 23 MiB.

## Update

Bump `RYOT_TAG` in `.env` and `.env.example`, then:

```bash
scripts/ryot-db-backup.sh
sudo docker compose pull && sudo docker compose up -d --build
```

**Rebuild** (`ryot-go:local`, FROM `python:3.13-alpine`): `sudo docker compose up -d --build --pull ryot-go` when Cup shows an update for the base or the security scan lists a fixable base CVE, otherwise monthly.
