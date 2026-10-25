# Dependency-Track

OWASP Dependency-Track 4.x: one CVE dashboard over the SBOMs of every running image and the host OS of fastpi and beefy (tower: Unraid/Slackware, not supported by Trivy, skipped). Fed by fastpi's scanner, nothing is scanned here.

| | |
|---|---|
| Images | `dependencytrack/apiserver:4.14.5`, `dependencytrack/frontend:4.14.5`, `postgres:18.6-alpine` |
| Containers | `dt-frontend` (host port `8090` → 8080, nginx: UI + `/api` proxied to the apiserver), `dt-apiserver` (no port, heap 4 GB, mem 6 GB), `dt-db` (no port), bridge `dependency-track` |
| Port | `8090`, reachable from fastpi only (DOCKER-USER rule) |
| Data | `/srv/appdata/dependency-track` (NVD/OSV mirror, index), `/srv/appdata/dependency-track-db` (Postgres) |
| Feed | `HomeLab-FastPi` `Server/4-Security-Monitoring/security_scan.py dt` (`security-dt.timer` 04:02), README there |
| URL | none yet; stage 2 (dt.holy-grail.ch via fastpi's Traefik + waker + Holy Grail) is prepared, not applied |

## Hardening

| Container | uid | read-only root | writable |
|---|---|---|---|
| dt-apiserver | 65122 | yes | `/data` (bind), `/tmp` (tmpfs) |
| dt-frontend | 65123 | yes | `/tmp`, `/etc/nginx/conf.d` (tmpfs); own `config/default.conf.template` + `config/config.json` mounted `:ro` (the entrypoint cannot rewrite them) |
| dt-db | 70 | no | PGDATA |

All: `cap_drop: ALL`, `no-new-privileges`, mem limits. Containers reach the internet (NVD, OSV, EPSS), not the LAN.

## Secrets

| File | Owner / mode | Use |
|---|---|---|
| `secrets/dt_db_password` | `70:65122 0440` | Postgres + apiserver (`ALPINE_DATABASE_PASSWORD_FILE`) |
| `secrets/dt_admin_password` | `buntu 0400` | UI login `admin` (default `admin/admin` replaced at first start) |
| fastpi `/var/lib/security-scan/secrets/dt-api-key` | `pi 0600` | API key of team `fastpi-scanner` |

Team `fastpi-scanner`: `BOM_UPLOAD`, `PROJECT_CREATION_UPLOAD`, `VIEW_PORTFOLIO`, `VIEW_VULNERABILITY`, `PORTFOLIO_MANAGEMENT` (deletes projects of removed images).

## Analyzers

| Source | State |
|---|---|
| Internal (NVD feeds, OSV mirror) | on; OSV ecosystems: Alpine, Debian, Ubuntu, Go, npm, PyPI, Maven, crates.io, NuGet, Packagist, RubyGems, Hex, Pub |
| OSS Index | off (needs a Sonatype account) |
| GitHub advisories, Trivy, Snyk, VulnDB | off |

## Setup (first start)

```bash
cd ~/Projects/Docker/Dependency-Track && cp .env.example .env && chmod 600 .env
mkdir -p secrets && (umask 077; openssl rand -hex 24 | tr -d '\n' > secrets/dt_db_password)
sudo chown 70:65122 secrets/dt_db_password && sudo chmod 0440 secrets/dt_db_password
sudo install -d -o 65122 -g buntu -m 2750 /srv/appdata/dependency-track
sudo install -d -o 70 -g 70 -m 0700 /srv/appdata/dependency-track-db
sudo docker compose up -d
```

Then via the API (`http://127.0.0.1:8090/api`): `POST /v1/user/forceChangePassword` (admin, new password → `secrets/dt_admin_password`), team `fastpi-scanner` + permissions above, `PUT /v1/team/<uuid>/key` → key to fastpi, OSV ecosystems (`vuln-source` / `google.osv.enabled`), `scanner` / `ossindex.enabled=false`.

## Update

`notify` tier in `_global/auto-update.conf` (DB migrates one way). Both images share one tag (`DT_TAG`). Before: `sudo docker exec dt-db pg_dump -U dtrack -Fc dtrack > ~/backups/dt-$(date +%F).dump`. DT 5.x is a new architecture (migration guide), not a tag bump.

## Check

```bash
sudo docker compose ps                                   # 3 healthy
curl -s http://127.0.0.1:8090/api/version                # 4.14.5
sudo docker logs dt-apiserver 2>&1 | grep -E 'ERROR|Mirror' | tail
```

UI from a LAN device (stage 1): `ssh -L 8090:127.0.0.1:8090 beefy-direct` on fastpi, then http://localhost:8090 there.
