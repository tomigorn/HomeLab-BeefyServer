# Antivirus

ClamAV on beefy: a completion scan of every SABnzbd/qBittorrent download and a nightly scan of new files. `clamav/scan.sh`, `clamav/hostscan.sh` and `tests/test_scan.sh` are verbatim copies from the fastpi repo (`Docker/antivirus/`); change them there, then copy.

## What runs when

| What | When | How |
|---|---|---|
| `freshclam` | always, 24 checks a day | container `clamav` (entrypoint) |
| `clamd` | always, `clamav:3310` on the internal `clamd` net (only clamav, clamav-queue and gluetun's namespace; S-8), freshclam egress on `antivirus_egress` | container `clamav`; reloaded by freshclam |
| Completion scan | a download finishes | `hook/clamav-hook.py`, run by SABnzbd and qBittorrent, see below |
| Background media scan | always, one file at a time | container `clamav-queue` (`clamav-hook.py --worker`), see below |
| `scan.sh new` | 04:05 daily | `clamav-scan.timer` → `clamav-scan.service` → `docker exec clamav /scan.sh new` |
| `scan.sh all` of the host root | 04:07 every night, scans until 04:40, resumes the next night; one pass every 6 days | `clamav-hostscan.timer` → `clamav-hostscan.service` → `docker compose run --rm clamav-hostscan` (container without network, private `clamd` multiscan on all cores, lowest CPU/IO priority) |
| `scan.sh <path>…`, `scan.sh restore <file>` | by hand | see below |

- 04:05: fastpi wakes beefy at 03:55 and the maintenance window (`Server/9-Night-Schedule.md`) holds it up until 04:45, so a later time never fires. A night asleep is skipped: the marker stays, the next run catches up.
- Engine (`NEW_CLAMD=1`, 2026-10-29): `new` runs 4 `clamdscan` clients against the container's own clamd (local socket, `MaxThreads 6`, 2 left for the completion queue), one path per hardlinked inode (an infected inode is quarantined under every path), `.recyclebin` and `.clamav-queue` skipped, files over 2047M not sent. Measured on 12 files / 12 GB: 848 s single client, 220 s with 4 (3.9×); the 2026-10-29 night (95 files, 113 GB, single clamscan) took 2 h 19 min, about 35 min now. clamd down = the old `clamscan`.
- Install / reinstall the units (both timers) and host folders: `Docker/Antivirus/install.sh`. Start: `sudo docker build --network host -t clamav-local:latest ./clamav && sudo docker compose up -d` (Movie-Downloads first: its networks `movie-downloads` and `clamd` must exist).

## Nightly and manual scans

| Command on beefy | Scans | Infected files |
|---|---|---|
| `sudo docker exec clamav /scan.sh new` | files under `/scan/ssd-hot` and `/scan/ssd-hot2` changed since the last good run | moved to quarantine |
| `sudo docker compose run --rm clamav-hostscan` (nightly timer) | the host root, one filesystem, as `/host`, minus `SCAN_EXCLUDE_DIRS`, in resumable chunks | report only |
| `sudo docker exec clamav /scan.sh /scan/ssd-hot/<path>` (or `/scan/ssd-hot2/...`) | the given files/folders | moved under `/scan/...` |

- `/scan/ssd-hot` = `/srv/.disks/ssd-hot`, `/scan/ssd-hot2` = `/srv/.disks/ssd-hot2`: the two hot SSD branches of the `/srv/video` union, where every download and import lands (`ssd-hot` first, `ssd-hot2` once `ssd-hot` has less than its `minfreespace` free). `/srv/appdata` (app configs, owner-only since 2026-10-22) is not scanned: no outside files land there. The `/srv/video` union and the cold HDD are never scanned by the nightly job, so it stays spun down: a file that only exists on `hdd-cold` (moved there by the mover) was scanned when it arrived and is not scanned again. The hook and queue stream any file to clamd from the union path, whatever branch it is on.
- Quarantine: `scan.sh` has one `QUARANTINE` (`/scan/ssd-hot/quarantine`). A nightly hit on `ssd-hot2` is moved there by `clamscan --move`, which copies across filesystems when rename is not possible. Hook and queue hits are renamed inside the union into `/data/quarantine`, so the file stays on its own branch (`install.sh` makes `quarantine` and `.clamav-queue` on both SSDs, same owner and mode); `ls /srv/.disks/ssd-hot*/quarantine`. Restore a file in the `ssd-hot2` quarantine with the full path: `scan.sh restore /scan/ssd-hot2/quarantine/<name>` (`index.txt` lives in `ssd-hot/quarantine`).
- Host scan engine: `USE_CLAMD=1` in the compose service (`clamdscan --multiscan`, `mem_limit: 6g`, tmpfs `/tmp` 3g where clamd unpacks archives; 512m ran full on 1 GB tarballs); `USE_CLAMD=0` is the old single-thread `clamscan -r`. Resume (`SCAN_RESUME=1`, chunks, `all.done`, `PASS_DAYS`): fastpi repo `Docker/antivirus/README.md` "Weekly host scan".
- Exit codes `0` clean, `1` infected, `2` errors (marker kept, next run retries). Files over 2 GiB are skipped by ClamAV (`clamav_files_skipped_large`). Details: fastpi repo `Docker/antivirus/README.md`.
- Logs: `journalctl -u clamav-scan`, `/var/lib/clamav-scan/last-<mode>.log`.

## Two containers, why

| | `clamav` (always on) | `clamav-hostscan` (nightly, exits) |
|---|---|---|
| Network | yes (freshclam, clamd for the hook) | `none` |
| Host root | not mounted | `/:/host:ro` |
| Virus DB | `clamav-db` rw (freshclam) | same volume, read-only |
| User / caps | 65112, no caps | root, `DAC_READ_SEARCH` + `DAC_OVERRIDE` + `CHOWN`, read-only root fs, tmpfs `/tmp` |
| Writes | quarantine, state, textfile | state, textfile only |

- The two units only run the docker client (root, for the socket) and are sandboxed: `ProtectSystem=strict`, no network but the socket, no caps (hostscan: `CAP_DAC_READ_SEARCH` to read the project in `~buntu`), syscall filter; `systemd-analyze security` 0.9 / 1.1. The scanning runs in the containers above.
- A container with internet egress and attacker-supplied downloads must not see the host: the part that reads host files has no network. Root because a non-root uid gets no effective caps in Docker.
- Scope (2026-11-01): all of `/` (one filesystem: `/srv/video` and the data disks are never touched) minus `SCAN_EXCLUDE_DIRS`, about 230k files. The old fixed folder list outgrew its 18 min run (timed out 2026-11-01).

| Excluded (under `/host`) | Why |
|---|---|
| `/var/lib/containerd`, `/var/lib/docker/{overlay2,containerd,image,buildkit,tmp}` | image layers (~370k files), rebuilt from images |
| `/var/lib/snapd/{snaps,seed}` | signed snap images |
| `/home/buntu/backups` | our own config/DB tarballs (17 GB), made on this host |
| `/home/buntu/.cache`, `/var/cache/apt` | re-downloadable caches |
| `/srv/appdata/dependency-track/.dependency-track` | public vulnerability feeds (3 GB) |

- Window: `SCAN_WINDOW=04:07-04:40`, `SCAN_SUNDAY=0`: scans only inside the maintenance window, a chunk still running at 04:40 is killed and repeated the next night; the unit stops after 40 min. Progress: `sudo cat /var/lib/clamav-scan/all.done; sudo wc -l < /var/lib/clamav-scan/all.list`.
- `hostscan.sh` hands state back to `STATE_OWNER` and `clamav.prom` to `TEXTFILE_OWNER` (env in compose), leaving the textfile dir (hook files) alone. Writes `mode="all"` into the same `clamav.prom`.
- Smaller run: `sudo docker compose run --rm -e SCAN_ROOTS=/host/etc clamav-hostscan`.

## Completion scan (hook)

| Client | Setting | Call |
|---|---|---|
| SABnzbd | script folder `/clamav-hook`, default category script `download-integrity.py` (checks the data on disk, then runs `clamav-hook.py`), "User script can flag job as failed" on | `download-integrity.py <final dir> …` |
| qBittorrent | Run external program on torrent finished | `/clamav-hook/download-integrity.py --qbit "%I" "%F"` (then `clamav-hook.py`) |
| qBittorrent API (stop, tag) | no localhost bypass (B-02) | `hook/qbit_session.py`: on 403 one login as `admin`, password `/run/secrets/qbit-webui.password` (`QBIT_PASS_FILE`); the queue worker (.204) is whitelisted and never logs in |

- Set by `../Movie-Downloads/scripts/clamav-hooks.sh` (idempotent). The hook is mounted read-only from `hook/`: a change is live at once.
- Streams each file to clamd (`INSTREAM`, Python stdlib, no clamdscan or docker socket in the clients). Files over 2 GiB are skipped; the nightly scan has the same limit.

| File in the job | What the hook does |
|---|---|
| SABnzbd job already failed (7th argument / `SAB_PP_STATUS` ≠ 0) | nothing, exit 0 at once: SABnzbd fails it, Sonarr/Radarr search again |
| Media: `mkv mp4 m4v avi mov wmv ts m2ts webm m4a m4b mp3 flac opus ogg aac srt ass ssa sub idx sup nfo txt jpg jpeg png`, content not an executable/archive (`MZ`, ELF, zip, rar, 7z, cab, OLE, `#!`) | hardlinked into `/data/.clamav-queue/<job>/`, exit 0 at once; scanned in the background |
| Everything else (exe, scripts, archives, multi-part, unknown or no extension) | scanned synchronously, as before |
| Queue missing or link fails | scanned synchronously |

- Too big for clamd (over 2 GiB, videos only: `mkv mp4 m4v avi mov wmv ts m2ts webm`, no executable/archive magic): queued with `parts`; the worker opens the file with ffprobe (`-protocol_whitelist file`, `-format_whitelist matroska,webm,mov,mp4,m4a,3gp,3g2,mj2,avi,mpegts`; it must have a video stream), dumps each attachment (fonts, files) and copies subtitles, attachments, data streams, chapters and tags into one small Matroska file under `/tmp/parts`, and streams those to clamd. Verdict `parts-clean` (with `minutes`, `parts`), `parts-infected` (quarantined like any hit), `parts-error` (`why`; e.g. no video stream). **Not covered:** the video and audio bitstreams themselves; MP4 boxes other than its subtitle/data tracks. Seerr's Manual interaction lets everyone import a `parts-clean` file whose running time fits (±25 %), else only admins. Videos over the limit without a queue: `skipped` as before. One file by hand: `sudo docker exec clamav-queue python3 /clamav-hook/clamav-hook.py --parts <path>`.
- Results log: `/var/lib/clamav-scan/textfile/clamav-results.jsonl` (`/clamav` in the clients, `TEXTFILE` in the worker), one JSON line per file and verdict: `at`, `path` (as the apps see it), `size`, `verdict` (`clean`, `infected`, `queued`, `skipped` = over 2 GiB, `error`), `signature`, `mode` (`hook`/`queue`), `sha256` when the file was read whole ([VirusTotal fingerprint check](#virustotal-fingerprint-check)); over 5 MiB it moves to `.1` (`RESULTS_MAX_BYTES`). Seerr's Manual interaction reads it read-only (malware never imported, unscanned files only by admins); a write failure never stops a scan.
- Hit (synchronous): file moved to `/data/quarantine` (name clash: `.001` suffix, as clamscan does), line in `index.txt`, exit 1. SABnzbd marks the job failed, so Radarr/Sonarr never import it and handle it as a failed download. qBittorrent: the torrent is stopped and tagged `infected`.
- clamd unreachable (it waits 2 min, e.g. right after boot): exit 0, job goes through, `clamav_scan_errors{mode="hook"}` > 0; the nightly scan covers the files.
- qBittorrent ignores the exit code and Radarr may import a small torrent before a synchronous scan ends; the nightly scan sees the hardlinked library file (new ctime) and quarantines it.

### Background queue (`clamav-queue`)

| | |
|---|---|
| Queue | `/srv/.disks/ssd-hot/.clamav-queue` and `/srv/.disks/ssd-hot2/.clamav-queue` (`/data/.clamav-queue` in the apps), 65112:media 2770, made by `install.sh`. One folder per job: hardlinks `0001…` + `job.json` (title, torrent hash, original paths); written as `<job>.tmp`, renamed when complete |
| Worker | container `clamav-queue`: `clamav-local` image, `/srv/video` union as `/data`, one file at a time, oldest job first, polls every 5 s |
| Why hardlinks | Sonarr/Radarr import (move or hardlink) right after the hook returns. The queue link keeps the same inode, so the worker always scans the very file that was imported, wherever it went; nothing to look up by path |
| Clean | a video gets the quality check ("Quality check" below), then the link is removed; empty job folder removed |
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

### Quality check

Owner-approved 2026-11-18 (fastpi `reports/specs/2026-11-18-media-quality-check-design.md`, part 1). After a **clean** verdict the worker checks every video (`VIDEO_EXT`) once more with ffprobe, demux only (no decode, `nice 19`, through the idle-class `/srv/dlpool`, ~0.5 GB/s): `hook/quality_check.py`.

| Finding | Effect |
|---|---|
| video timestamp jumps back > 1 s (segments out of order, the 2026-11-17 VisionQuest S01E08 case) | `broken`: blocked |
| no video stream (cover art does not count) / no audio stream / container not readable (`Invalid data found`) | `broken`: blocked |
| read error in the container (e.g. `File ended prematurely`) | note only, until the library scan (spec part 4) shows it never hits healthy files |
| ffprobe missing, crashed, killed after its time limit | `error`: nothing done |

- Result: one line per video in `/var/lib/clamav-scan/textfile/quality-results.jsonl` (`path` as the apps see it, `title`, `hash`, `verdict`, `reasons`, `notes`, `duration`); rotated like `clamav-results.jsonl`. The worker never acts: Movie-Downloads' `scripts/quality-block.py` does (Movie-Downloads README "Quality check").
- The check runs in its own `try` after the scan verdict: it never changes or delays the virus verdict. Off: `QUALITY_CHECK=0` in the compose file, `sudo docker compose up -d clamav-queue`.
- Jellyfin's keyframe warning is no signal (VisionQuest S01E05 has it and is clean).
- Tests: `python3 tests/test_quality.py` (fake ffprobe), real samples built from ffmpeg's own test pattern in the worker image: `sudo docker run --rm --network none --user 65112 -v "$PWD/Docker/Antivirus:/src:ro" --entrypoint python3 clamav-local:latest /src/tests/test_quality.py`.

## VirusTotal fingerprint check

Owner-approved 2026-11-15: every downloaded file the hook or the queue scans is looked up on VirusTotal **by its SHA-256 only**. The file is never uploaded (`hook/virustotal.py` only knows `GET /api/v3/files/<sha256>`; `tests/test_virustotal.py` checks there is no upload path).

| | |
|---|---|
| Hash | taken from the bytes streamed to clamd (one read); files over 2 GiB (parts scan, `skipped`) are read once more for it. Infected files are not looked up. Field `sha256` in `clamav-results.jsonl` |
| Queue + cache | `/var/lib/clamav-scan/textfile/virustotal-results.jsonl` (`/clamav` in the containers), one JSON line per file and state: `at`, `path`, `size`, `sha256`, `hashed` (when hashed), `vt`, for verdicts `malicious`, `suspicious`, `engines`, `checked`, for errors `why`; last line per path wins; over 5 MiB it moves to `.1`. A hash with a verdict is never looked up again |
| Lookups | thread in `clamav-queue` (egress via `movie-downloads`), oldest first, every 15 s; free API: 4 a minute, 500 a UTC day, kept in `.virustotal-limits.json` across restarts. A failed lookup (`error`) is retried after 15 min; a 429 pauses 1 min. Never blocks or fails a scan |
| Key | `secrets/vt_api_key` (gitignored, 65112 0400), read-only at `/run/secrets/vt_api_key` (`VT_API_KEY_FILE`), sent only in the `x-apikey` header; never in argv, env or logs. Missing: hashes stay `queued` |
| Metrics | `clamav-hook.prom`, `mode="virustotal"`: `virustotal_malware`, `_flagged`, `_clean`, `_unknown`, `_queued`, `_errors` (files by last state), `_lookups_today` |

| `vt` | Means | Seerr Manual interaction |
|---|---|---|
| `malware` | ≥ 3 engines say malicious | ❌ known malware (n of N scanners): never imported, admins included |
| `flagged` | 1–2 engines | ⚠️ flagged by n scanners: only admins may import (owner's call: 1–2 hits are often false alarms) |
| `clean` | known, 0 engines | ✅ known file, clean |
| `unknown` | 404, nobody submitted it (normal for fresh videos) | ⚪ no effect |
| `error` | lookup failed or quota, retried | ⚪ not checked yet, no effect |
| `queued` | hashed, waiting | ⏳ fingerprint check queued |

```sh
sudo docker exec clamav-queue python3 /clamav-hook/clamav-hook.py --vt /data/<path>...   # hash older files by hand, the worker looks them up
sudo docker logs clamav-queue 2>&1 | grep VirusTotal
```

Replace the key (VirusTotal → profile → API key), from a file, never on the command line:

```sh
sudo install -o 65112 -g 65112 -m 0400 /path/to/new-key-file ~/Projects/Docker/Antivirus/secrets/vt_api_key
sudo docker restart clamav-queue
```

First setup: the key is the fastpi one (`HomeLab-FastPi` `Docker/antivirus/secrets/vt_api_key`), copied over ssh stdin; `secrets/` is `buntu` 0700.

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
python3 Docker/Antivirus/tests/test_quality.py   # fake ffprobe; real samples: README "Quality check"
python3 Docker/Antivirus/tests/test_virustotal.py   # fake clock and API, no network
python3 Docker/Antivirus/tests/test_integrity.py   # download-integrity.py, Movie-Downloads README "Download integrity"
sudo docker run --rm --entrypoint sh -v "$PWD/Docker/Antivirus:/src:ro" clamav-local:latest /src/tests/test_scan.sh   # busybox
```

Weekly: `rebuild-local` (Mon 04:27, `Docker/_global/rebuild-local.conf`) rebuilds, runs `tests/test_scan.sh` in the new image, recreates only when packages changed, rolls back on a failed check. Changing `clamav/` needs a rebuild: `sudo docker build --network host -t clamav-local:latest ./clamav && sudo docker compose up -d clamav clamav-queue`. Changing `hook/`: live at once for the hook, `sudo docker restart clamav-queue` for the worker. `--network host` because the DOCKER-USER guard drops a build container's DNS to the LAN (compose builds can't get the host network without a BuildKit entitlement).

**Rebuild** (`clamav-local`, FROM `alpine:latest`): `sudo docker build --network host --pull -t clamav-local:latest ./clamav && sudo docker compose up -d clamav clamav-queue` when the security scan lists a fixable Alpine CVE, otherwise monthly.
