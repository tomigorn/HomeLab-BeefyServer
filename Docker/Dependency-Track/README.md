# Dependency-Track

OWASP Dependency-Track 5.x: one CVE dashboard over the SBOMs of every running image and the host OS of fastpi and beefy (tower: Unraid/Slackware, not supported by Trivy, skipped). Fed by fastpi's scanner, nothing is scanned here.

| | |
|---|---|
| Compose | `docker-compose.v5.yaml` (project `dependency-track-v5`), the default via `.env` `COMPOSE_FILE` + `COMPOSE_PROFILES=app`; `docker-compose.yaml` = v4 4.14.6, down, kept for the rollback until 2026-11-11 (`docker-compose.v5.yaml` header) |
| Images | `ghcr.io/dependencytrack/apiserver:5.2.1`, `ghcr.io/dependencytrack/frontend:5.2.1` (pinned by digest), `postgres:18.6-alpine` |
| Containers | `dt5-frontend` (host port `8090` → 8080, nginx: UI + `/api` proxied to the apiserver), `dt5-apiserver` (no port, mem 3 GB), `dt5-db` (no port, mem 4 GB), bridge `dependency-track-v5` |
| Port | `8090`, reachable from fastpi only (DOCKER-USER rule) |
| Data | `/srv/appdata/dt5` (KEK keyset `.dependency-track/keys/secret-management-kek.json`, copy in `~/backups/dt5-kek.json`), `/srv/appdata/dt5-db` (Postgres); v4: `/srv/appdata/dependency-track(-db)`, kept until 2026-11-11 |
| Feed | `HomeLab-FastPi` `Server/4-Security-Monitoring/security_scan.py dt` (`security-dt.timer` 04:02), README there |
| URL | https://dt.holy-grail.ch (group `homelab-admins`, wakes beefy), see Access |

## Hardening

| Container | uid | read-only root | writable |
|---|---|---|---|
| dt5-apiserver | 65122 | yes | `/data` (bind), `/tmp` (tmpfs, `exec`: zstd native lib) |
| dt5-frontend | 65123 | yes | `/tmp`, `/etc/nginx/conf.d` (tmpfs); own `config/v5-frontend.conf.template` mounted `:ro`, it also serves `/static/config.json` from the env (`OIDC_*`) |
| dt5-db | 70 | no | PGDATA |

All: `cap_drop: ALL`, `no-new-privileges`, mem limits. Containers reach the internet (NVD, OSV, EPSS), not the LAN; only exception: bridge `172.28.14.0/24` (pinned) → fastpi `:443` for `sso.holy-grail.ch` (`extra_hosts`, `_global/scripts/docker-user-guard.sh`).

## Access

| Step | What |
|---|---|
| 1 | fastpi Traefik `dynamic/dt.yml`: forward auth (Authentik app `dt`, group `homelab-admins`), then the beefy wake gate (`?port=8090`) |
| 2 | DT login page: **Sign in with Holy Grail** (Authentik app `dt-login`, public client + PKCE, redirect `/static/oidc-callback.html`), silent on the existing session |
| 3 | User provisioned on first login (`preferred_username`), teams synced from the `groups` claim at every login: `homelab-admins` → `Administrators` (`scripts/oidc-team-mapping.sh`, idempotent) |

| Setting | Where |
|---|---|
| `DT_OIDC_ISSUER`, `DT_OIDC_CLIENT_ID` | `.env` (client id written by fastpi `Authentik/scripts/provision-dt-sso.py`) → apiserver `DT_OIDC_*`, frontend `OIDC_*` |
| Check | `curl -s http://127.0.0.1:8090/api/v1/oidc/available` → `true`; `curl -s http://127.0.0.1:8090/static/config.json` has issuer + client id |

Break-glass: local user `admin` (password in `secrets/dt_admin_password`), on the same login page or directly at `http://127.0.0.1:8090` on beefy (`ssh -L 8090:127.0.0.1:8090 beefy-direct`). Use only when Holy Grail is down.

## Secrets

| File | Owner / mode | Use |
|---|---|---|
| `secrets/dt5_db_password` | `70:65122 0440` | Postgres + apiserver (`DT_DATASOURCE_PASSWORD: $${file::…}`) |
| `secrets/dt_db_password` | `70:65122 0440` | v4 (rollback only) |
| `secrets/dt_admin_password` | `buntu 0400` | break-glass UI login `admin` (default `admin/admin` replaced at first start); `scripts/oidc-team-mapping.sh` |
| fastpi `/var/lib/security-scan/secrets/dt-api-key` | `pi 0600` | API key of team `fastpi-scanner` |

Team `fastpi-scanner`: `BOM_UPLOAD`, `PROJECT_CREATION_UPLOAD`, `VIEW_PORTFOLIO`, `VIEW_VULNERABILITY`, `PORTFOLIO_MANAGEMENT` (deletes projects of removed images).

## Analyzers

| Source | State |
|---|---|
| Internal (NVD feeds, OSV mirror) | on; OSV ecosystems (re-selected by hand after the v5 migration, Administration > Vulnerability Sources > OSV): Alpine, Debian, Ubuntu, Go, npm, PyPI, Maven, crates.io, NuGet, Packagist, RubyGems, Hex, Pub |
| OSS Index | off (needs a Sonatype account) |
| GitHub advisories, Trivy, Snyk, VulnDB | off |

## Setup

v5 came from the v4 database (`v4-migrator`, [docs/v5-cutover.md](docs/v5-cutover.md)): admin password, teams, API key and OIDC mapping were carried over. Fresh install: runbook section 0, then via the API: `POST /v1/user/forceChangePassword`, team `fastpi-scanner` + permissions above, `PUT /v1/team/<uuid>/key` → key to fastpi, OSV ecosystems, `scripts/oidc-team-mapping.sh`.

## Update

`notify` tier in `_global/auto-update.conf` (DB migrates one way). Both images are pinned by digest in `docker-compose.v5.yaml`. Before: `(umask 077; sudo docker exec dt5-db pg_dump -U dtrack -Fc dtrack > ~/backups/dt-$(date +%F).dump)` (owner-only: it holds the DB).

## Check

```bash
sudo docker compose ps                                                    # 3 healthy
curl -s http://127.0.0.1:8090/api/version                # 5.2.1
sudo docker logs dt5-apiserver 2>&1 | grep -E ' ERROR ' | tail
~/Projects/Docker/_global/scripts/post-update-check.sh Dependency-Track
```

UI: https://dt.holy-grail.ch (Holy Grail, `homelab-admins`).
