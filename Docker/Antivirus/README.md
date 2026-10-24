# Antivirus

ClamAV on beefy: a completion scan of every SABnzbd/qBittorrent download and a nightly scan of new files. `clamav/scan.sh`, `clamav/hostscan.sh` and `tests/test_scan.sh` are verbatim copies from the fastpi repo (`Docker/antivirus/`); change them there, then copy.

## What runs when

| What | When | How |
|---|---|---|
| `freshclam` | always, 24 checks a day | container `clamav` (entrypoint) |
| `clamd` | always, `clamav:3310` on the `movie-downloads` bridge | container `clamav`; reloaded by freshclam |
| Completion scan | a download finishes | `hook/clamav-hook.py`, run by SABnzbd and qBittorrent, see below |
| `scan.sh new` | 04:05 daily | `clamav-scan.timer` → `clamav-scan.service` → `docker exec clamav /scan.sh new` |
| `scan.sh all` of host folders | Sunday 04:07 | `clamav-hostscan.timer` → `clamav-hostscan.service` → `docker compose run --rm clamav-hostscan` (container without network, ~11 min) |
| `scan.sh <path>…`, `scan.sh restore <file>` | by hand | see below |

- 04:05: fastpi wakes beefy at 03:55 and it powers off about 04:25 when idle, so a later time never fires. A night asleep is skipped: the marker stays, the next run catches up.
- Install / reinstall the units (both timers) and host folders: `Docker/Antivirus/install.sh`. Start: `sudo docker build --network host -t clamav-local:latest ./clamav && sudo docker compose up -d` (Movie-Downloads first: its network must exist).

## Nightly and manual scans

| Command on beefy | Scans | Infected files |
|---|---|---|
| `sudo docker exec clamav /scan.sh new` | files under `/scan/ssd-hot` changed since the last good run | moved to quarantine |
| `sudo docker compose run --rm clamav-hostscan` (weekly timer) | the host folders in `SCAN_ROOTS` of the compose service, as `/host/...` | report only |
| `sudo docker exec clamav /scan.sh /scan/ssd-hot/<path>` | the given files/folders | moved under `/scan/...` |

- `/scan/ssd-hot` = `/srv/.disks/ssd-hot` (the SSD branch, where every download and import lands). `/srv/appdata` (app configs, owner-only since 2026-10-22) is not scanned: no outside files land there. The `/srv/video` union and the cold HDD are never scanned, so it stays spun down.
- Exit codes `0` clean, `1` infected, `2` errors (marker kept, next run retries). Files over 2 GiB are skipped by ClamAV (`clamav_files_skipped_large`). Details: fastpi repo `Docker/antivirus/README.md`.
- Logs: `journalctl -u clamav-scan`, `/var/lib/clamav-scan/last-<mode>.log`.

## Two containers, why

| | `clamav` (always on) | `clamav-hostscan` (weekly, exits) |
|---|---|---|
| Network | yes (freshclam, clamd for the hook) | `none` |
| Host root | not mounted | `/:/host:ro` |
| Virus DB | `clamav-db` rw (freshclam) | same volume, read-only |
| User / caps | 65112, no caps | root, `DAC_READ_SEARCH` + `DAC_OVERRIDE` + `CHOWN`, read-only root fs, tmpfs `/tmp` |
| Writes | quarantine, state, textfile | state, textfile only |

- A container with internet egress and attacker-supplied downloads must not see the host: the part that reads host files has no network. Root because a non-root uid gets no effective caps in Docker.
- Scope: `/host/home /root /etc /opt /usr/local /tmp /var/tmp /srv/appdata`, not all of `/` (~630k files incl. Docker layers: a scan would outlast the 04:00 to 04:25 window). Change `SCAN_ROOTS` in `docker-compose.yaml`; the run takes ~11 min, the unit is stopped after 18 min.
- `hostscan.sh` hands state back to `STATE_OWNER` and `clamav.prom` to `TEXTFILE_OWNER` (env in compose), leaving the textfile dir (hook files) alone. Writes `mode="all"` into the same `clamav.prom`.
- Smaller run: `sudo docker compose run --rm -e SCAN_ROOTS=/host/etc clamav-hostscan`.

## Completion scan (hook)

| Client | Setting | Call |
|---|---|---|
| SABnzbd | script folder `/clamav-hook`, default category script `clamav-hook.py`, "User script can flag job as failed" on | `clamav-hook.py <final dir> …` |
| qBittorrent | Run external program on torrent finished | `/clamav-hook/clamav-hook.py --qbit "%I" "%F"` |

- Set by `../Movie-Downloads/scripts/clamav-hooks.sh` (idempotent). The hook is mounted read-only from `hook/`.
- Streams each file to clamd (`INSTREAM`, Python stdlib, no clamdscan or docker socket in the clients). Files over 2 GiB are skipped; the nightly scan has the same limit.
- Hit: file moved to `/data/quarantine` (name clash: `.001` suffix, as clamscan does), line in `index.txt`, exit 1. SABnzbd marks the job failed, so Radarr/Sonarr never import it and handle it as a failed download. qBittorrent: the torrent is stopped and tagged `infected`.
- clamd unreachable (it waits 2 min, e.g. right after boot): exit 0, job goes through, `clamav_scan_errors{mode="hook"}` > 0; the nightly scan covers the files.
- qBittorrent ignores the exit code and Radarr may import a small torrent before the scan ends; the nightly scan sees the hardlinked library file (new ctime) and quarantines it.

## Quarantine and restore

| Where (host) | What |
|---|---|
| `/srv/.disks/ssd-hot/quarantine/` (`/srv/video/quarantine` in the union, `/data/quarantine` in the apps) | quarantined files, group `media`, 2770; no library points at the union root |
| `.../quarantine/index.txt` | date TAB original path as the `clamav` container sees it (`/scan/ssd-hot/...` = `/srv/.disks/ssd-hot/...`; the hook maps `/data` to it) TAB signature |

False positive: `sudo docker exec clamav /scan.sh restore '<file name in quarantine>'` moves it back to its original path from `index.txt`, marks the line `restored <date>` and adds the path to `/var/lib/clamav-scan/allow.txt`, which every scan.sh mode skips.

## Metrics

| File (`/var/lib/clamav-scan/textfile/`) | Written by | Labels |
|---|---|---|
| `clamav.prom` | `scan.sh` | `host="beefy"`, `mode` = `new`/`all`/`path`; the same metrics as fastpi |
| `clamav-hook.prom` | the hook | `host="beefy",mode="hook"`: `clamav_files_scanned`, `clamav_files_skipped_large`, `clamav_infected_last_run`, `clamav_infected_total` (counter), `clamav_last_run_timestamp_seconds`, `clamav_scan_errors` |

beefy has no node-exporter: fastpi pulls both files over ssh.

## Tests

```sh
Docker/Antivirus/tests/test_scan.sh
python3 Docker/Antivirus/tests/test_hook.py
sudo docker run --rm --entrypoint sh -v "$PWD/Docker/Antivirus:/src:ro" clamav-local:latest /src/tests/test_scan.sh   # busybox
```

Changing `clamav/` needs a rebuild: `sudo docker build --network host -t clamav-local:latest ./clamav && sudo docker compose up -d clamav`. `--network host` because the DOCKER-USER guard drops a build container's DNS to the LAN (compose builds can't get the host network without a BuildKit entitlement).

**Rebuild** (`clamav-local`, FROM `alpine:latest`): `sudo docker build --network host --pull -t clamav-local:latest ./clamav && sudo docker compose up -d clamav` when the security scan lists a fixable Alpine CVE, otherwise monthly.
