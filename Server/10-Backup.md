# 10 - Nightly backup beefy → fastpi

restic, encrypted, deduplicated, to fastpi's **append-only** REST server: beefy can add snapshots but never delete or overwrite them; retention and prune run on fastpi. Server side: HomeLab-FastPi `Server/5-Backup/README.md`.

| What | Detail |
|---|---|
| Job | `beefy-backup.sh` as root via `beefy-backup.timer` 04:26 (after original-audio 04:20, own lock `/run/lock/beefy-backup.lock`, not the media lock); never wakes beefy; the maintenance window waits for it; hard stop 18 min |
| Sandbox | `beefy-backup.service`: ProtectSystem=strict, home read-only, writes only staging, `/var/cache/restic`, `/run/lock`, `/srv/appdata` (SQLite `-shm`); a second run exits on the lock before it can touch the running one's staging |
| Repo | `rest:https://192.168.1.2:8009/beefy/` (fastpi NVMe `/var/lib/beefy-backup/beefy`); fastpi accepts only beefy's IP, TLS cert pinned (`RESTIC_CACERT`), own login |
| Credentials | `/root/.config/restic/` (700): `beefy.pass` (repo key), `fastpi-rest.pass` (REST login), `fastpi-rest.crt`; written by fastpi `setup-beefy-repo.sh`, never printed |
| Retention | on fastpi 05:07: 7 daily, 4 weekly, 6 monthly; prune Sunday, check 10 % Wednesday |
| Failure | Telegram at once from beefy (root-owned copy `/usr/local/lib/beefy-oob/scripts/oob-alert.sh` of `Docker/_global/scripts/oob-alert.sh`, installed by `install.sh`); fastpi alerts `BeefyBackupStale` (> 36 h) from the repo itself |
| Restore test | `beefy-restore-test.sh` on beefy, Wednesday 04:02 (`beefy-restore-test.timer`, the window waits for it): restores the latest nightly snapshot into a scratch dir (`/var/lib/beefy-restore-test`, removed after), `integrity_check` + key table rows per SQLite DB, DT5 `pg_restore` into a throwaway postgres (same image as `dt5-db`, `--network none`, 4 GB RAM limit, stopped afterwards) + row counts. Metrics `db_restore_test_ok{host="beefy",db,engine}` and `..._last_success_timestamp_seconds` in `/var/lib/beefy-restore-test/restore-test.prom`, pulled by fastpi `beefy-idle-pull` (alerts `DbRestoreTest*`, fastpi `Docker/grafana/prometheus/rules/restore-tests.yml`); Telegram on failure. fastpi's weekly test also checks this repo (check 5 %, `quick_check`, DT5 `pg_restore -l`, KEK). Tests: `./test_beefy_restore_test.sh` |
| Install | `Server/10-Backup/install.sh` (restic 0.19.1 by sha256, scripts to `/usr/local/sbin/beefy-backup` and `beefy-restore-test`, timers). Tests: `./test_beefy_backup.sh` |

## Contents (`beefy-backup.sh --list`)
- Consistent dumps in `/var/lib/beefy-backup-staging`: SQLite `.backup` of Seerr, Jellyfin (`jellyfin.db`, Intro Skipper), Radarr, Sonarr, Prowlarr, Bazarr, SABnzbd history, Floppy, Lingarr (`local.db`, since 2026-11-25); DT5 `pg_dump -Fc -Z0` (uncompressed so restic dedups). Live DB files excluded.
- `/srv/appdata` (Jellyfin config, users, plugin configs, library + studio images; DT5 KEK `dt5/.dependency-track/keys`), `~/Projects` (incl. gitignored `secrets/`, `.env`), `~/.ssh`, `~/.config/systemd`, Claude rules + memory, `/etc`, `/var/lib/beefy-mover`, crontabs
- Left out (re-creatable): DT5 raw DB dir (dump instead), DT v4 dirs (retired), `*.tgz` in appdata, Jellyfin cache/log/transcodes/trickplay (1.4 GB)/People images (0.5 GB)/IMDb ratings db, *arr logs + MediaCover, SAB `Downloads`, Floppy snapshots, floppy-redis, flaresolverr, recyclarr resources, `~/backups` (holds fastpi's own repo)
- *arr built-in zips: daily, 14 kept (`Docker/Movie-Downloads/scripts/arr-backup-schedule.sh`), included with appdata

## Database inventory (2026-11-25)
| DB | Service | Engine | In backup | Restore-tested |
|---|---|---|---|---|
| dt5 | Dependency-Track 5 (`dt5-db`) | Postgres | yes (`pg_dump -Fc -Z0`) | yes (full `pg_restore`, PROJECT, COMPONENT, VULNERABILITY rows) |
| seerr, jellyfin, jellyfin-intro-skipper, radarr, sonarr, prowlarr, bazarr, sabnzbd-history, floppy | same names | SQLite | yes (`.backup`) | yes |
| lingarr | Lingarr | SQLite | yes (since 2026-11-25; before a raw file copy) | yes |
| `Hangfire.db` | Lingarr job queue | SQLite | raw file copy only | no (queue state) |
| jellyfin-beta `jellyfin.db`, intro skipper | Jellyfin beta | SQLite | raw file copy only | no |
| `jellyfin-popular-state/state.db` | Jellyfin popular job | SQLite | raw file copy only | no |
| `imdbratings.db` | Jellyfin IMDb plugin | SQLite | no (re-creatable) | no |
| floppy-redis | Floppy | Redis | no (cache) | no |

Size 2026-10-29: snapshot 2.7 GiB processed (DT5 dump 2.0 GiB), repo 0.94 GiB, first run 22 s.

## Restore (on fastpi, beefy not needed)
```bash
sudo -i
export RESTIC_REPOSITORY=/var/lib/beefy-backup/beefy RESTIC_PASSWORD_FILE=/root/.config/restic/beefy-repo.pass
restic snapshots
restic restore latest --target /restore --include /srv/appdata/seerr --include /var/lib/beefy-backup-staging
# copy /restore to beefy (rsync -a ... beefy:/tmp/restore), then on beefy:
```
- SQLite app: stop the container, copy `/var/lib/beefy-backup-staging/sqlite/<original path>` over the original, delete its `-wal`/`-shm`, start
- DT5: `docker exec -i dt5-db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < dt5.pgdump` (list first with `pg_restore -l`); the KEK file must match the dump
- From beefy itself (read-only listing works too): `sudo bash -c 'export RESTIC_REPOSITORY=rest:https://192.168.1.2:8009/beefy/ RESTIC_REST_USERNAME=beefy RESTIC_CACERT=/root/.config/restic/fastpi-rest.crt RESTIC_PASSWORD_FILE=/root/.config/restic/beefy.pass RESTIC_REST_PASSWORD=$(cat /root/.config/restic/fastpi-rest.pass); restic snapshots'`

Run now: `sudo systemctl start beefy-backup && journalctl -u beefy-backup -n 5`.
