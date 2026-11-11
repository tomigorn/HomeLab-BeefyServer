# Activity-Gate — read-only data for fastpi's activity page

nginx allow-list that fastpi's `activity` service (`HomeLab-FastPi` `Docker/Activity`, README "Gate path contract") polls every 30 s, **only while fastpi's Waker reports beefy up**. It never wakes beefy (no WoL) and never keeps it awake (port in the idle watcher's `EXCLUDE_PORTS`). fastpi holds no key: the gate injects them.

| | |
|---|---|
| Port | `8095` on beefy (`GATE_PORT`); 8097/8098 belong to `jellyfin-popular` |
| Clients | fastpi `192.168.1.2` only: DOCKER-USER drops every other LAN client (`../_global/docs/container-hardening.md`, Firewall), nginx `allow`/`deny` + `geo` answers anyone else 403 (also beefy itself and other containers) |
| Networks | `jellyfin`, `movie-downloads` (external): upstreams by name (`jellyfin:8096`, `radarr:7878`, `sonarr:8989`), never via `192.168.1.102` (a flow to a published port counts as use in the idle watcher) |
| Files | `gate/nginx.conf`, `gate/start.sh` (keys → `/tmp/keys.conf` in the tmpfs, then nginx) |
| Idle watcher | `EXCLUDE_PORTS` in `/etc/beefy-idle.conf` contains `8095` (`Server/8-Idle-Watcher.md`) |
| Hardening | uid 65131, read_only, cap_drop ALL, no-new-privileges, 32m RAM, 32 pids, image pinned by digest |

## Paths

Exact request line (path and query string), GET only. Anything else: 404; another method: 405.

| Path | Upstream |
|---|---|
| `/jellyfin/Sessions?ActiveWithinSeconds=60` | Jellyfin `/Sessions` |
| `/radarr/api/v3/queue?pageSize=200&includeMovie=true` | Radarr |
| `/sonarr/api/v3/queue?pageSize=500&includeSeries=true&includeEpisode=true` | Sonarr |
| `/sonarr/api/v3/series` | Sonarr |
| `/seerr/request-state.json`, `/seerr/night-waiting.json` | files in `/srv/appdata/indexer-bar` (Seerr README "Indexer bar"), mounted read-only |
| `/health` | `ok` (healthcheck) |

A new path: add it in fastpi's `rust/src/beefy.rs` and here, plus `gate.allowlist` in `../_global/post-update.d/Activity-Gate.sh`.

Logs: access log off; nginx errors only (no keys: they travel in headers).

## Secrets

| File | Source | Owner, mode |
|---|---|---|
| `secrets/jellyfin.apikey` | copy of `../Jellyfin/secrets/api-key` | buntu:65131 0440 |
| `secrets/radarr.apikey` | `<ApiKey>` of `/srv/appdata/radarr/config.xml` | buntu:65131 0440 |
| `secrets/sonarr.apikey` | `<ApiKey>` of `/srv/appdata/sonarr/config.xml` | buntu:65131 0440 |

Create or rotate (never echoes a key), then restart:

```bash
cd ~/Projects/Docker/Activity-Gate && umask 077 && mkdir -p secrets
printf %s "$(tr -d '\r\n' < ../Jellyfin/secrets/api-key)" > secrets/jellyfin.apikey
for a in radarr sonarr; do printf %s "$(grep -oP '(?<=<ApiKey>)[^<]+' /srv/appdata/$a/config.xml)" > secrets/$a.apikey; done
sudo chown buntu:65131 secrets secrets/*.apikey && chmod 0750 secrets && chmod 0440 secrets/*.apikey
sudo docker restart activity-gate
```

## Operations

| What | Command |
|---|---|
| Deploy / recreate | `../_global/scripts/update-project.sh ~/Projects/Docker/Activity-Gate recreate` |
| Update the image | new digest (same `nginxinc/nginx-unprivileged` tag family as `seerr-proxy`) into compose, then the line above |
| Checks | `../_global/scripts/post-update-check.sh Activity-Gate` |
| From fastpi | `docker exec traefik wget -qO- 'http://192.168.1.102:8095/jellyfin/Sessions?ActiveWithinSeconds=60' \| head -c 100` |
| Remove | `sudo docker compose down` here; `EXCLUDE_PORTS` entry may stay |
