# Dependency-Track 4.14.6 → 5.2.1 cutover

Attended only (one-way DB change, SSO login). Rehearsed 2026-10-27 on a copy of the live DB: all checks below passed.

## Rehearsal results

| Check | Result |
|---|---|
| Migrator | `v4-migrator:5.2.1`: `bootstrap`, `run --metrics-retention-days=N`, `verify`, `cleanup` (`verify` takes only `--target-*`) |
| Passwords | `--*-pass` prompt reads only the first password from stdin: use a `PGPASSFILE` (below), never a password on the command line |
| Time | `pg_dump` 39 s, `pg_restore` 74 s, `run` 584 s, apiserver start 10 s: downtime about 20 min |
| Data | v4 = v5: 75 projects, 18184 components, 5262 findings, 1308 analyses (184 suppressed), 2947 comments, 850576 vulnerabilities, 5 teams, 1 API key, OIDC mapping `homelab-admins → Administrators` |
| Expected drops | `VULNERABLESOFTWARE` −172, `VULNERABLESOFTWARE_VULNERABILITIES` −62, `CONFIGPROPERTY` −36 (OSV ecosystem selection is gone) |
| Notification rules | none in v4, nothing to re-enable |
| API key | fastpi key works unchanged, team `fastpi-scanner` keeps its 5 permissions |
| SBOM upload | `PUT /api/v1/bom` with `parentUUID` + `isLatest`: processed, parent linked, findings created |
| VEX export | `security_scan.py` (paged `findings()`, commit `2a71d02`): 13 files / 189 statements, identical to v4 |
| Paging | `/v1/finding/project/…` is capped at 100 per page (also with `pageSize=2000`); `/v1/project?pageSize=500` is honoured |
| OIDC callback | unchanged: `https://dt.holy-grail.ch/static/oidc-callback.html`, **no Authentik change** |
| Read-only fs | frontend: works with our template (`/static/config.json` served by nginx). apiserver: works only with `/tmp` tmpfs `exec` (zstd native lib), else it dies at start |
| Healthcheck | image has no `curl`; `/health/ready` stays green while the app is dead: check `:9000/health` and `:8080/api/version` with `wget` |
| RAM | apiserver 320 MB idle (v4: 940 MB), Postgres 1.2 GB (`shared_buffers=1GB`), frontend 11 MB |
| KEK keyset | created at first start: `/srv/appdata/dt5/.dependency-track/keys/secret-management-kek.json`, back it up |

## Images (pinned)

| Image | Digest |
|---|---|
| `ghcr.io/dependencytrack/apiserver:5.2.1` | `sha256:a7ce7a42928c6c03e94e8255e2ee871f5268f55b638a69729cae7afc47ef43b8` |
| `ghcr.io/dependencytrack/frontend:5.2.1` | `sha256:1c0f63b083af8e89ff4ede5e7ed5cfc4e23ab871fa140ed101e8dc56f53eb51c` |
| `ghcr.io/dependencytrack/v4-migrator:5.2.1` | `sha256:48278e3b1dffe202e78bb964d82b3377c2e0db310624d06eb93dfb5a508e701b` |
| `postgres:18.6-alpine` | same as v4 |

## 0. Prepare (before the slot, v4 keeps running)

Write `docker-compose.v5.yaml` (project `dependency-track-v5`) next to the v4 file, differences to v4:

| Service | Setting |
|---|---|
| `dt5-db` | `postgres:18.6-alpine`, `user 70:70`, `mem_limit 4g`, `TZ: Etc/UTC`, secret `dt5_db_password`, `/srv/appdata/dt5-db:/var/lib/postgresql`, `ports: 127.0.0.1:15433:5432` (migration only), `command: postgres -c max_wal_size=8GB -c max_locks_per_transaction=256 -c maintenance_work_mem=1GB -c checkpoint_timeout=30min -c checkpoint_completion_target=0.9 -c shared_buffers=1GB` |
| `dt5-apiserver` | image above, `user 65122:65122`, `read_only`, `mem_limit 3g`, **no `TZ`**, `/srv/appdata/dt5:/data`, tmpfs `/tmp:uid=65122,gid=65122,mode=0700,size=512m,exec`, `extra_hosts sso.holy-grail.ch:192.168.1.2` |
| | env `DT_DATASOURCE_URL=jdbc:postgresql://dt5-db:5432/dtrack`, `DT_DATASOURCE_USERNAME=dtrack`, `DT_DATASOURCE_PASSWORD: $${file::/run/secrets/dt5_db_password}` (`$$` = literal for compose), `DT_DATASOURCE_POOL_MAX_SIZE=10` |
| | env `DT_OIDC_ENABLED=true`, `DT_OIDC_ISSUER`, `DT_OIDC_CLIENT_ID`, `DT_OIDC_USERNAME_CLAIM=preferred_username` (default is `name`), `DT_OIDC_USER_PROVISIONING=true`, `DT_OIDC_TEAMS_CLAIM=groups`, `DT_OIDC_TEAM_SYNCHRONIZATION=true` |
| | healthcheck `["CMD-SHELL", "wget -q -Y off -T 3 -O /dev/null http://127.0.0.1:9000/health && wget -q -Y off -T 3 -O /dev/null http://127.0.0.1:8080/api/version"]` |
| `dt5-frontend` | image above, rest as v4 `dt-frontend` (read-only, tmpfs, `${DT_PORT}:8080`, wget healthcheck), template `config/v5-frontend.conf.template` = v4 template with `proxy_pass http://dt5-apiserver:8080` |
| network | `dependency-track-v5`, subnet `172.28.14.0/24` (DOCKER-USER sso rule; free only once v4 is down) |

```bash
cd ~/Projects/Docker/Dependency-Track
sed 's#http://dt-apiserver:8080#http://dt5-apiserver:8080#' config/default.conf.template > config/v5-frontend.conf.template
(umask 077; openssl rand -hex 24 | tr -d '\n' > secrets/dt5_db_password)
sudo chown 70:65122 secrets/dt5_db_password && sudo chmod 0440 secrets/dt5_db_password
sudo docker compose -f docker-compose.v5.yaml config -q && sudo docker compose -f docker-compose.v5.yaml pull
```

Update in the same change: `post-update.d/Dependency-Track.sh` (`dt-db`/`dt-apiserver` → `dt5-*`, `curl` → `wget` in `dt.sso-reach`), `CUSTOMIZATIONS.md` (`ALPINE_OIDC_*` → `DT_OIDC_*`), `auto-update.conf` (data dir `dt5-db`), README.

## 1. Stop and back up (downtime starts)

```bash
# fastpi
sudo systemctl stop security-dt.timer
sudo cp -a /var/lib/security-scan/secrets/dt-api-key ~/dt-api-key.v4.bak
# beefy
cd ~/Projects/Docker/Dependency-Track && B=~/backups/dt4 && mkdir -p $B && D=$(date +%F)
sudo docker compose stop dt-frontend dt-apiserver
sudo docker exec dt-db pg_dump -U dtrack -Fc dtrack > $B/dt-final-$D.dump && chmod 600 $B/dt-final-$D.dump
sudo docker exec -i dt-db pg_restore -l < $B/dt-final-$D.dump | grep -c TABLE   # > 0
sudo docker compose stop dt-db
sudo tar czf $B/pgdata-$D.tgz -C /srv/appdata dependency-track-db
sudo cp -a .env docker-compose.yaml config $B/ && sudo cp -a secrets $B/secrets-$D
sudo docker compose down          # frees subnet 172.28.14.0/24 and port 8090; bind-mounted data stays
```

Rollback from here on, one command: `sudo docker compose -f docker-compose.v5.yaml --profile app down; sudo docker compose up -d`

## 2. Migrate

```bash
cd ~/Projects/Docker/Dependency-Track && B=~/backups/dt4 && D=$(date +%F)
sudo install -d -o 70 -g 70 -m 0700 /srv/appdata/dt5-db /srv/appdata/dt4-src
sudo install -d -o 65122 -g 65122 -m 0700 /srv/appdata/dt5
sudo docker compose -f docker-compose.v5.yaml up -d dt5-db
(umask 077; openssl rand -hex 24 | tr -d '\n' > secrets/dt4_src_password) && sudo chown 70:70 secrets/dt4_src_password
sudo docker run -d --name dt4-src --user 70:70 -e POSTGRES_USER=dtrack -e POSTGRES_DB=dtrack -e POSTGRES_PASSWORD_FILE=/run/pw \
  -v $PWD/secrets/dt4_src_password:/run/pw:ro -p 127.0.0.1:15434:5432 -v /srv/appdata/dt4-src:/var/lib/postgresql postgres:18.6-alpine
until sudo docker exec dt4-src pg_isready -U dtrack -d dtrack; do sleep 2; done
sudo docker exec -i dt4-src pg_restore -U dtrack -d dtrack --no-owner --no-acl < $B/dt-final-$D.dump
PGP=$(mktemp) && printf '127.0.0.1:15434:dtrack:dtrack:%s\n127.0.0.1:15433:dtrack:dtrack:%s\n' "$(sudo cat secrets/dt4_src_password)" "$(sudo cat secrets/dt5_db_password)" > $PGP
sudo chown 65534 $PGP && sudo chmod 0400 $PGP
M="sudo docker run --rm --network=host -e PGPASSFILE=/run/pgpass -v $PGP:/run/pgpass:ro ghcr.io/dependencytrack/v4-migrator:5.2.1"
T="--target-url=jdbc:postgresql://127.0.0.1:15433/dtrack --target-user=dtrack"
S="--source-url=jdbc:postgresql://127.0.0.1:15434/dtrack --source-user=dtrack"
$M bootstrap $T
$M run $S $T --metrics-retention-days=30 --dry-run      # "preflight passed"
$M run $S $T --metrics-retention-days=30 2>&1 | tee $B/migrate-$D.log | tail -3   # ~10 min, "Migration completed"
$M verify $T | tee $B/verify-$D.log                       # Source = v5 except the expected drops above
$M cleanup $T
sudo rm -f $PGP secrets/dt4_src_password && sudo docker rm -f dt4-src && sudo rm -rf /srv/appdata/dt4-src
```

`load` failed: not resumable. `sudo docker compose -f docker-compose.v5.yaml down`, `sudo rm -rf /srv/appdata/dt5-db/*`, start again at `up -d dt5-db`.

## 3. Start v5

```bash
# remove the "127.0.0.1:15433" ports line from dt5-db first
sudo docker compose -f docker-compose.v5.yaml --profile app up -d
until curl -sf http://127.0.0.1:8090/api/version; do sleep 3; done    # "version":"5.2.1"
sudo docker logs dt5-apiserver 2>&1 | grep -E ' ERROR ' | head
sudo cp -a /srv/appdata/dt5/.dependency-track/keys/secret-management-kek.json ~/backups/dt5-kek.json && sudo chmod 0400 ~/backups/dt5-kek.json
```

UI (Administration > Vulnerability Sources): enable OSV with the ecosystems in README "Analyzers"; NVD and EPSS are already on.

## 4. Functional verify (all must pass, else rollback)

| # | Check | Command / action | Expect |
|---|---|---|---|
| 1 | Containers | `sudo docker compose -f docker-compose.v5.yaml --profile app ps` | 3 healthy |
| 2 | OIDC | `curl -s http://127.0.0.1:8090/api/v1/oidc/available`; `curl -s http://127.0.0.1:8090/static/config.json` | `true`; issuer + client id |
| 3 | Login | https://dt.holy-grail.ch → Sign in with Holy Grail | logged in, team Administrators |
| 4 | Counts | `sudo docker exec dt5-db psql -U dtrack -tAc 'select (select count(*) from "PROJECT"),(select count(*) from "COMPONENTS_VULNERABILITIES"),(select count(*) from "ANALYSIS")'` | same as `verify` Source column |
| 5 | Upload + VEX (fastpi) | `sudo systemctl start security-dt.service; journalctl -u security-dt.service -n 30` | no error, projects "last BOM import" = now |
| 6 | VEX metrics | `grep -h security_dt_vex /var/lib/node-exporter/textfile/*.prom` | `export_failed 0`, statements as before |
| 7 | Smoke test | fastpi `Docker/_global/scripts/smoke-test.sh` | `dt -> sso` passes |
| 8 | Post-update check | beefy `~/Projects/Docker/_global/scripts/post-update-check.sh Dependency-Track` | all green (after updating `post-update.d`) |

Then `sudo systemctl start security-dt.timer` on fastpi.

## 5. Rollback

```bash
cd ~/Projects/Docker/Dependency-Track
sudo docker compose -f docker-compose.v5.yaml --profile app down && sudo docker compose up -d
curl -s http://127.0.0.1:8090/api/version     # 4.14.6
```

v4 data is never written by the migrator. Damaged anyway: `sudo docker compose stop dt-db`, restore `~/backups/dt4/pgdata-<date>.tgz` into `/srv/appdata`. API key is unchanged, nothing to restore on fastpi. Keep v4 data, dumps and images 2 weeks.

## API key rotation (v5)

| Action | Endpoint (admin) |
|---|---|
| New key for team | `PUT /api/v1/team/<uuid>/key` → write to fastpi `/var/lib/security-scan/secrets/dt-api-key` (pi 0600) |
| Regenerate | `POST /api/v1/team/key/<publicIdOrKey>` |
| Revoke old | `DELETE /api/v1/team/key/<publicIdOrKey>` |
