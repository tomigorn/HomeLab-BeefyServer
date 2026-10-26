# 9 - Night schedule: the maintenance window 03:58-04:45

beefy sleeps (S5) and its timers cannot wake it. Everything recurring runs in one window, or runs live only while beefy is awake anyway. Rule: nothing recurring 06:00-22:00 except the live pollers; nothing 02:00-03:00 (DST); all times `Europe/Zurich`.

| Part | What |
|---|---|
| Wake | fastpi `beefy-maintenance-wake` 03:55 (`Docker/Beefy-Waker`) |
| Hold | `beefy-maintenance-window.timer` 03:58 → `.service` takes `/run/beefy-keep-awake` (only when nobody holds it, releases only its own), sleeps 47 min (to 04:45), then waits until none of the window jobs runs (cap 45 min) and releases. Cost about 0.50 CHF/year |
| Install | `Server/9-Night-Schedule/install.sh` (idempotent; drop-ins `dropins/<unit>.d/night-window.conf`, user ones in `user-dropins/`). A timer restart may run its job once at install time |
| Test | `Server/9-Night-Schedule/test-beefy-maintenance-window` |
| Rule for new timers | fixed `OnCalendar=… Europe/Zurich` in the window, `Persistent=false`, no `OnBootSec` (a daytime boot must not run it) |

## Timeline (local time)

| Time | Job |
|---|---|
| 03:58 | window opens |
| 04:00 | beefy-mover (+≤5 min), Seerr radarr-scan |
| 04:01-04:06 | apt-daily, logrotate, dpkg-db-backup, Seerr download-sync-reset 04:03, fwupd-refresh 04:04, clamav-scan 04:05, Seerr sonarr-scan 04:05, sysstat-rotate/summary |
| 04:07-04:12 | Sun clamav-hostscan 04:07, Jellyfin IMDb 04:08, Jellyfin scan 04:10, auto-update 04:12 (+ Sun cutoff-search) |
| 04:14-04:20 | description-scout 04:14, Recyclarr 04:15, nfo-scout 04:16, language-missing 04:18, original-audio sweep 04:20 (+ Sun image-cleanup) |
| 04:22-04:29 | Seerr jellyfin-full-scan 04:22, Jellyfin trickplay 04:24, Sun fstrim 04:26, Sun xfs/e2 scrub 04:28/04:29 |
| 04:30-04:44 | anacron 04:30 (`cron.daily` 04:35: chkrootkit, rkhunter), Jellyfin chapters 04:30, Seerr availability-sync 04:30, image-cache 04:32, segments 04:33, apt-daily-upgrade 04:34, Sun blocklisted-tags 04:34, man-db 04:36, tmpfiles-clean 04:40, update-notifier 04:41, Jellyfin daily tasks 04:35-04:44 |
| 04:45 | window releases (after the listed jobs), idle watcher powers off ~15 min later |

fastpi pulls from beefy at 04:02-04:20 (never wakes it: `fetch_nowake`).

## Not a systemd timer

| What | How | Where |
|---|---|---|
| Seerr jobs | `Docker/Seerr/scripts/night-jobs.py` (API, persisted in Seerr's settings) | Seerr README |
| Jellyfin tasks | `Docker/Jellyfin/scripts/night-tasks.py` | Jellyfin README |
| original-audio | Radarr/Sonarr Connect script on import/upgrade + 04:20 sweep | Movie-Downloads README |
| snap refresh | `snap set system refresh.timer=04:30-04:44` (install.sh) | - |
| anacron | `anacron.service` disabled (boot start would run `cron.daily` at once); the timer runs it 04:30 | - |

## Disabled (telemetry, ads, desktop notifiers)

| Unit | Why |
|---|---|
| `motd-news.timer` (masked) | fetches ads for the login banner |
| `apport-autoreport.timer/.path` (masked) | crash report upload |
| `ua-timer.timer` (masked) | Ubuntu Pro, not attached |
| `ubuntu-insights-collect/upload.timer` (user buntu, masked) | usage telemetry |
| `snap.firmware-updater.firmware-notifier.timer` (user, masked) | desktop notifier |
| `pro config set apt_news=false` | ads in `apt upgrade` |

Kept on purpose: the live pollers (`request-tags`, `clear-data-wipe`, `indexer-usage`, `hdd-spinstate`, `queue-watchdog`, `sysstat-collect`, `jellyfin-user-defaults`) run only while beefy is awake and never wake it. Radarr/Sonarr internal tasks and Bazarr's scheduler have fixed intervals and stay.

Ryot is replaced by Floppy: `ryot-db-backup.timer` is disabled. Floppy snapshots its SQLite itself at 04:20 inside the container (no systemd timer).
