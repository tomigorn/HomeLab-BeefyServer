# Antivirus

ClamAV on beefy: a completion scan of every SABnzbd/qBittorrent download and a nightly scan of new files. `clamav/scan.sh`, `clamav/hostscan.sh` and `tests/test_scan.sh` are verbatim copies from the fastpi repo (`Docker/antivirus/`); change them there, then copy.

## What runs when

| What | When | How |
|---|---|---|
| `freshclam` | always, 24 checks a day | container `clamav` (entrypoint) |
| `clamd` | always, `clamav:3310` on the `movie-downloads` bridge | container `clamav`; reloaded by freshclam |
| Completion scan | a download finishes | `hook/clamav-hook.py`, run by SABnzbd and qBittorrent, see below |
| Background media scan | always, one file at a time | container `clamav-queue` (`clamav-hook.py --worker`), see below |
| `scan.sh new` | 04:05 daily | `clamav-scan.timer` → `clamav-scan.service` → `docker exec clamav /scan.sh new` |
| `scan.sh all` of host folders | Sunday 04:07 | `clamav-hostscan.timer` → `clamav-hostscan.service` → `docker compose run --rm clamav-hostscan` (container without network, private `clamd` multiscan on all cores) |
| `scan.sh <path>…`, `scan.sh restore <file>` | by hand | see below |

- 04:05: fastpi wakes beefy at 03:55 and the maintenance window (`Server/9-Night-Schedule.md`) holds it up until 04:45, so a later time never fires. A night asleep is skipped: the marker stays, the next run catches up.
- Install / reinstall the units (both timers) and host folders: `Docker/Antivirus/install.sh`. Start: `sudo docker build --network host -t clamav-local:latest ./clamav && sudo docker compose up -d` (Movie-Downloads first: its network must exist).

## Nightly and manual scans

| Command on beefy | Scans | Infected files |
|---|---|---|
| `sudo docker exec clamav /scan.sh new` | files under `/scan/ssd-hot` and `/scan/ssd-hot2` changed since the last good run | moved to quarantine |
| `sudo docker compose run --rm clamav-hostscan` (weekly timer) | the host folders in `SCAN_ROOTS` of the compose service, as `/host/...` | report only |
| `sudo docker exec clamav /scan.sh /scan/ssd-hot/<path>` (or `/scan/ssd-hot2/...`) | the given files/folders | moved under `/scan/...` |

- `/scan/ssd-hot` = `/srv/.disks/ssd-hot`, `/scan/ssd-hot2` = `/srv/.disks/ssd-hot2`: the two hot SSD branches of the `/srv/video` union, where every download and import lands (`ssd-hot` first, `ssd-hot2` once `ssd-hot` has less than its `minfreespace` free). `/srv/appdata` (app configs, owner-only since 2026-10-22) is not scanned: no outside files land there. The `/srv/video` union and the cold HDD are never scanned by the nightly job, so it stays spun down: a file that only exists on `hdd-cold` (moved there by the mover) was scanned when it arrived and is not scanned again. The hook and queue stream any file to clamd from the union path, whatever branch it is on.
- Quarantine: `scan.sh` has one `QUARANTINE` (`/scan/ssd-hot/quarantine`). A nightly hit on `ssd-hot2` is moved there by `clamscan --move`, which copies across filesystems when rename is not possible. Hook and queue hits are renamed inside the union into `/data/quarantine`, so the file stays on its own branch (`install.sh` makes `quarantine` and `.clamav-queue` on both SSDs, same owner and mode); `ls /srv/.disks/ssd-hot*/quarantine`. Restore a file in the `ssd-hot2` quarantine with the full path: `scan.sh restore /scan/ssd-hot2/quarantine/<name>` (`index.txt` lives in `ssd-hot/quarantine`).
- Weekly host scan engine: `USE_CLAMD=1` in the compose service (`clamdscan --multiscan`, `mem_limit: 3g`); `USE_CLAMD=0` is the old single-thread `clamscan -r`. Details: fastpi repo `Docker/antivirus/README.md`.
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

- The two units only run the docker client (root, for the socket) and are sandboxed: `ProtectSystem=strict`, no network but the socket, no caps (hostscan: `CAP_DAC_READ_SEARCH` to read the project in `~buntu`), syscall filter; `systemd-analyze security` 0.9 / 1.1. The scanning runs in the containers above.
- A container with internet egress and attacker-supplied downloads must not see the host: the part that reads host files has no network. Root because a non-root uid gets no effective caps in Docker.
- Scope: `/host/home /root /etc /opt /usr/local /tmp /var/tmp /srv/appdata`, not all of `/` (~630k files incl. Docker layers: a scan would outlast the 04:00 to 04:25 window). Change `SCAN_ROOTS` in `docker-compose.yaml`; the run takes ~11 min, the unit is stopped after 18 min.
- `hostscan.sh` hands state back to `STATE_OWNER` and `clamav.prom` to `TEXTFILE_OWNER` (env in compose), leaving the textfile dir (hook files) alone. Writes `mode="all"` into the same `clamav.prom`.
- Smaller run: `sudo docker compose run --rm -e SCAN_ROOTS=/host/etc clamav-hostscan`.

## Completion scan (hook)

| Client | Setting | Call |
|---|---|---|
| SABnzbd | script folder `/clamav-hook`, default category script `download-integrity.py` (checks the data on disk, then runs `clamav-hook.py`), "User script can flag job as failed" on | `download-integrity.py <final dir> …` |
| qBittorrent | Run external program on torrent finished | `/clamav-hook/download-integrity.py --qbit "%I" "%F"` (then `clamav-hook.py`) |

- Set by `../Movie-Downloads/scripts/clamav-hooks.sh` (idempotent). The hook is mounted read-only from `hook/`: a change is live at once.
- Streams each file to clamd (`INSTREAM`, Python stdlib, no clamdscan or docker socket in the clients). Files over 2 GiB are skipped; the nightly scan has the same limit.

| File in the job | What the hook does |
|---|---|
| SABnzbd job already failed (7th argument / `SAB_PP_STATUS` ≠ 0) | nothing, exit 0 at once: SABnzbd fails it, Sonarr/Radarr search again |
| Media: `mkv mp4 m4v avi mov wmv ts m2ts webm m4a m4b mp3 flac opus ogg aac srt ass ssa sub idx sup nfo txt jpg jpeg png`, content not an executable/archive (`MZ`, ELF, zip, rar, 7z, cab, OLE, `#!`) | hardlinked into `/data/.clamav-queue/<job>/`, exit 0 at once; scanned in the background |
| Everything else (exe, scripts, archives, multi-part, unknown or no extension) | scanned synchronously, as before |
| Queue missing or link fails | scanned synchronously |

- Hit (synchronous): file moved to `/data/quarantine` (name clash: `.001` suffix, as clamscan does), line in `index.txt`, exit 1. SABnzbd marks the job failed, so Radarr/Sonarr never import it and handle it as a failed download. qBittorrent: the torrent is stopped and tagged `infected`.
- clamd unreachable (it waits 2 min, e.g. right after boot): exit 0, job goes through, `clamav_scan_errors{mode="hook"}` > 0; the nightly scan covers the files.
- qBittorrent ignores the exit code and Radarr may import a small torrent before a synchronous scan ends; the nightly scan sees the hardlinked library file (new ctime) and quarantines it.

### Background queue (`clamav-queue`)

| | |
|---|---|
| Queue | `/srv/.disks/ssd-hot/.clamav-queue` and `/srv/.disks/ssd-hot2/.clamav-queue` (`/data/.clamav-queue` in the apps), 65112:media 2770, made by `install.sh`. One folder per job: hardlinks `0001…` + `job.json` (title, torrent hash, original paths); written as `<job>.tmp`, renamed when complete |
| Worker | container `clamav-queue`: `clamav-local` image, `/srv/video` union as `/data`, one file at a time, oldest job first, polls every 5 s |
| Why hardlinks | Sonarr/Radarr import (move or hardlink) right after the hook returns. The queue link keeps the same inode, so the worker always scans the very file that was imported, wherever it went; nothing to look up by path |
| Clean | link removed; empty job folder removed |
| Hit | the worker finds the other links of the inode (library, seeding copy) under `/data`, moves the file to the quarantine under its library name, removes the other links, writes `index.txt` and `clamav_infected_last_run{mode="queue"}`, stops + tags the torrent (qBit whitelists `172.28.10.204`); log line `INFECTED <title>: <path> (<signature>)` |
| clamd down | the job stays, retried every 30 s; no verdict (connection lost) 3 times: dropped, `clamav_scan_errors{mode="queue"}`. A link it cannot remove is an error too (log `ERROR`) |
| Restart | jobs are on disk; a link leaves only with a verdict, a half-written `.tmp` job is taken over after 1 h. Re-scanning after a crash is harmless |
| Log | `sudo docker logs clamav-queue` (one line per job) |
| Test | EICAR as `x.mkv` in a test job: `docker exec -e QUEUE=/data/.clamav-queue-test movie-sabnzbd /clamav-hook/clamav-hook.py <dir> x.nzb Title "" tv "" 0`, then `docker exec -e QUEUE=/data/.clamav-queue-test -e HEARTBEAT=/tmp/t clamav-queue python3 /clamav-hook/clamav-hook.py --worker --once` (test queue dir 65112:media 2770 on ssd-hot) |
| Health | healthcheck = the worker touched `/tmp/clamav-queue-alive` within 15 min (before every file) |

- Window: an infected media file can be in the library until its turn in the queue (Jellyfin may read it). Executables and archives never are: they are scanned before the job finishes.
- A hit makes Sonarr/Radarr see a missing file; they do not re-grab on their own.
- The hit search walks the union and may spin up the cold HDD; only on a hit.
- `scan.sh restore` puts the file back to the library path; other removed links (seeding copy) are not restored.

## Quarantine and restore

| Where (host) | What |
|---|---|
| `/srv/.disks/ssd-hot/quarantine/` (`/srv/video/quarantine` in the union, `/data/quarantine` in the apps) | quarantined files, group `media`, 2770; no library points at the union root |
| `.../quarantine/index.txt` | date TAB original path as the `clamav` container sees it (`/scan/ssd-hot/...` = `/srv/.disks/ssd-hot/...`, `/scan/ssd-hot2/...` likewise; the hook maps `/data` to the branch that holds the file: mergerfs xattr `user.mergerfs.basepath`, else the `PATH_MAP` target where it exists, else the first; `PATH_MAP=/data=/scan/ssd-hot:/scan/ssd-hot2`) TAB signature |
| `/var/lib/clamav-scan/textfile/clamav-hits.tsv` | hook and queue hits only: date TAB mode (`hook`/`queue`) TAB job title TAB the same path TAB signature (same date as the index line); fastpi reads it for the `MalwareQuarantined` mode/title. `index.txt` keeps 3 fields: `scan.sh restore` matches `NF == 3` |

False positive: `sudo docker exec clamav /scan.sh restore '<file name in quarantine>'` moves it back to its original path from `index.txt`, marks the line `restored <date>` and adds the path to `/var/lib/clamav-scan/allow.txt`, which every scan.sh mode skips.

## Metrics

| File (`/var/lib/clamav-scan/textfile/`) | Written by | Labels |
|---|---|---|
| `clamav.prom` | `scan.sh` | `host="beefy"`, `mode` = `new`/`all`/`path`; the same metrics as fastpi |
| `clamav-hook.prom` | the hook (`mode="hook"`, per job) and `clamav-queue` (`mode="queue"`, per job) | `host="beefy"`: `clamav_files_scanned`, `clamav_files_skipped_large`, `clamav_infected_last_run`, `clamav_infected_total` (counter), `clamav_last_run_timestamp_seconds`, `clamav_scan_errors`; hook only `clamav_files_queued`; queue only `clamav_queue_jobs` (jobs left) |

beefy has no node-exporter: fastpi pulls both files (and `clamav-hits.tsv`, `index.txt`) over ssh.

## Tests

```sh
Docker/Antivirus/tests/test_scan.sh
python3 Docker/Antivirus/tests/test_hook.py
python3 Docker/Antivirus/tests/test_integrity.py   # download-integrity.py, Movie-Downloads README "Download integrity"
sudo docker run --rm --entrypoint sh -v "$PWD/Docker/Antivirus:/src:ro" clamav-local:latest /src/tests/test_scan.sh   # busybox
```

Changing `clamav/` needs a rebuild: `sudo docker build --network host -t clamav-local:latest ./clamav && sudo docker compose up -d clamav clamav-queue`. Changing `hook/`: live at once for the hook, `sudo docker restart clamav-queue` for the worker. `--network host` because the DOCKER-USER guard drops a build container's DNS to the LAN (compose builds can't get the host network without a BuildKit entitlement).

**Rebuild** (`clamav-local`, FROM `alpine:latest`): `sudo docker build --network host --pull -t clamav-local:latest ./clamav && sudo docker compose up -d clamav clamav-queue` when the security scan lists a fixable Alpine CVE, otherwise monthly.
