# Antivirus

ClamAV on beefy: a completion scan of every SABnzbd/qBittorrent download and a nightly scan of new files. `clamav/scan.sh` and `tests/test_scan.sh` are verbatim copies from the fastpi repo (`Docker/antivirus/`); change them there, then copy.

## What runs when

| What | When | How |
|---|---|---|
| `freshclam` | always, 24 checks a day | container `clamav` (entrypoint) |
| `clamd` | always, `clamav:3310` on the `movie-downloads` bridge | container `clamav`; reloaded by freshclam |
| Completion scan | a download finishes | `hook/clamav-hook.py`, run by SABnzbd and qBittorrent, see below |
| `scan.sh new` | 04:05 daily | `clamav-scan.timer` → `clamav-scan.service` → `docker exec clamav /scan.sh new` |
| `scan.sh all`, `scan.sh <path>…` | by hand | see below |

- 04:05: fastpi wakes beefy at 03:55 and it powers off about 04:25 when idle, so a later time never fires. A night asleep is skipped: the marker stays, the next run catches up.
- Install / reinstall the units and host folders: `Docker/Antivirus/install.sh`. Start: `sudo docker compose up -d --build` (Movie-Downloads first: its network must exist).

## Nightly and manual scans

| Command on beefy | Scans | Infected files |
|---|---|---|
| `sudo docker exec clamav /scan.sh new` | files under `/scan/ssd-hot`, `/scan/appdata` changed since the last good run | moved to quarantine |
| `sudo docker exec clamav /scan.sh all` | both and the host root (`/host`, read-only, one filesystem) | report only |
| `sudo docker exec clamav /scan.sh /scan/ssd-hot/<path>` | the given files/folders | moved under `/scan/...`, report only for `/host/...` |

- `/scan/ssd-hot` = `/srv/.disks/ssd-hot` (the SSD branch, where every download and import lands), `/scan/appdata` = `/srv/appdata`. The `/srv/video` union and the cold HDD are never scanned, so it stays spun down.
- Exit codes `0` clean, `1` infected, `2` errors (marker kept, next run retries). Files over 2 GiB are skipped by ClamAV (`clamav_files_skipped_large`). Details: fastpi repo `Docker/antivirus/README.md`.
- Logs: `journalctl -u clamav-scan`, `/var/lib/clamav-scan/last-<mode>.log`.

## Completion scan (hook)

| Client | Setting | Call |
|---|---|---|
| SABnzbd | script folder `/clamav-hook`, default category script `clamav-hook.py`, "User script can flag job as failed" on | `clamav-hook.py <final dir> …` |
| qBittorrent | Run external program on torrent finished | `/clamav-hook/clamav-hook.py --qbit "%I" "%F"` |

- Set by `../Movie-Downloads/scripts/clamav-hooks.sh` (idempotent). The hook is mounted read-only from `hook/`.
- Streams each file to clamd (`INSTREAM`, Python stdlib, no clamdscan or docker socket in the clients). Files over 2 GiB are skipped; the nightly scan has the same limit.
- Hit: file moved to `/data/quarantine`, line in `index.txt`, exit 1. SABnzbd marks the job failed, so Radarr/Sonarr never import it and handle it as a failed download. qBittorrent: the torrent is stopped and tagged `infected`.
- clamd unreachable (it waits 2 min, e.g. right after boot): exit 0, job goes through, `clamav_scan_errors{mode="hook"}` > 0; the nightly scan covers the files.
- qBittorrent ignores the exit code and Radarr may import a small torrent before the scan ends; the nightly scan sees the hardlinked library file (new ctime) and quarantines it.

## Quarantine and restore

| Where (host) | What |
|---|---|
| `/srv/.disks/ssd-hot/quarantine/` (`/srv/video/quarantine` in the union, `/data/quarantine` in the apps) | quarantined files, `buntu`, 0750; no library points at the union root |
| `.../quarantine/index.txt` | date TAB original path TAB signature; hook paths are `/data/...` (= `/srv/video/...`), nightly paths `/scan/ssd-hot/...` (= `/srv/.disks/ssd-hot/...`) |

```sh
grep '<file name>' /srv/.disks/ssd-hot/quarantine/index.txt
mv /srv/.disks/ssd-hot/quarantine/<file> /srv/.disks/ssd-hot/<original path below /data or /scan/ssd-hot>
```

The move changes the ctime: the next nightly run scans it again.

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

Changing `clamav/` needs a rebuild: `sudo docker compose up -d --build`.
