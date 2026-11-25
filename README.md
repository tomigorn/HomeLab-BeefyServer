# MediaServer
This git folder is located in ~/Projects

## Backup

Nightly 04:26 to fastpi's append-only restic server (beefy cannot delete snapshots), monthly restore test on fastpi: `Server/10-Backup.md`.

## Job heartbeat

Every enabled timer of beefy (system and `buntu` user scope) gets a last-success metric, so a job that stops or fails silently pages. beefy has no node-exporter (it sleeps, nothing scrapes it): `buntu`'s user timer `job-heartbeat.timer` (every 5 min, while up) runs `Docker/_global/scripts/job_heartbeat.py` (verbatim copy of fastpi's, single source: HomeLab-FastPi `Docker/grafana/job-heartbeat/`, with its test; change it there, then copy) and writes `~/.local/state/job-heartbeat/job_heartbeat.prom`. fastpi's `beefy-idle-pull` copies it every 5 min while beefy is up (removed while it sleeps). The last success is kept in `~/.local/state/job-heartbeat/state.json` (systemd forgets it at poweroff).

| Metric (`host="beefy"`, `scope`, `unit`) | Meaning |
|---|---|
| `systemd_job_last_success_timestamp_seconds`, `systemd_job_last_result` | last successful finish of the timer's service, 1 if the last run failed |
| `systemd_job_max_age_seconds` | allowed age from the timer's schedule (longest gap + grace) |
| `systemd_unit_failed` | one series per failed unit |

| Alert (fastpi, Telegram, muted 22:00-07:00) | Fires when |
|---|---|
| `SystemdUnitFailed` | a unit is failed for 5 min |
| `JobHeartbeatStale` | last success older than its allowed age for 10 min, only once beefy has been up longer than that age (night jobs: judged in the 03:58-04:45 window and at 22:05) |

```bash
systemctl --user list-timers   # install: copy Docker/_global/systemd/job-heartbeat.* to ~/.config/systemd/user, daemon-reload, enable --now job-heartbeat.timer
python3 Docker/_global/scripts/test_job_heartbeat.py
```

## Image auto-update

Nightly, by tier, with a functional check and automatic rollback. `Docker/_global/scripts/{auto_update.py,test_auto_update.py,update-project.sh,test_update_project.sh}` are verbatim copies of fastpi's (single source: HomeLab-FastPi `Docker/_global/scripts/`, doc `Server/4-Security-Monitoring/auto-updates.md` "beefy"): change them there, then copy.

| Piece | What |
|---|---|
| `Docker/_global/auto-update.conf` | tiers, services, backups, functional check per stack |
| `Docker/_global/systemd/auto-update.{service,timer}` | 04:12 inside the 03:55 wake; nothing starts after 04:16 (rest `deferred`), max 2 projects a night, hard stop after 50 min (2 failed updates with rollback), state and report written after each project; holds `beefy-keep-awake on auto-update` only while running (other holds stay) |
| `Docker/_global/install-auto-update.sh` | installs and enables the timer |
| Result | `/var/lib/auto-update/textfile/auto_update.prom`, pulled by fastpi at 04:20 → Telegram `Nightly auto-update beefy` |
| Failed rollback | paged at once from beefy itself: `Docker/_global/scripts/oob-alert.sh` (verbatim copy of fastpi's, direct Telegram via DoH), secrets `Docker/_global/secrets/oob-alert.env` + `telegram_bot_token` (600, gitignored, same bot as fastpi's Alertmanager) |
| Data backups | `update-project.sh <dir> pull <paths…>` (the notify command): a missing path or failed tar aborts before `up -d` (exit 2); a rollback after an image change restores those backups first, the failed data stays as `<path>.failed-update-<time>`; restore fails = services stay stopped, exit 3 (fastpi `UPDATE-RUNBOOK.md`) |
| Drift | a container on another ref than compose declares (or a digest pin on another image id) is `drift` (🔁, recreate command in the summary), never an update; auto rows recreate it via `update-project.sh <dir> recreate` (`recreated` ♻️), notify rows only report (fastpi `Docker/_global/scripts/README.md` "Drift") |
| Shared tags | a container left on the old image of a tag another stack pulled (`python:3.15.2-alpine3.24`: vpn-status, download-now, popular) is recreated at the end of the run via `update-project.sh <dir> recreate` when its row is auto, else logged; `container_image_stale.prom` (also `auto_update.py --stale-only`) → fastpi alert `ContainerImageStale` (fastpi `Docker/_global/scripts/README.md` "Shared tags") |
| Image cleanup | `image-cleanup.sh` (Sun 04:20, verbatim copy of fastpi's): unused images after 14 days; old tags of a repo in use go even when compose names `repo:${VAR}`; newest 3 rollback tags per repo and project; `--dry-run` lists what goes and when |
| Customizations | `Docker/CUSTOMIZATIONS.md`: every customization an update must keep, checked by `Docker/_global/scripts/post-update-check.sh` (`_global/post-update.d/<Project>.sh`) after each update; one missing = rolled back |

| Tier | Stacks | Why |
|---|---|---|
| auto | Movie-Downloads flaresolverr, recyclarr, vpn-status; Cup-Agent cup-agent, socket-proxy-unix; Seerr seerr-proxy, download-now | stateless; pinned version tags (socat floats): a new digest is a rebuild. 24 h cool-down, never across a major, rolled back when the check fails (Seerr sidecars: page with injected bar, download-now health) |
| notify | Cup-Agent socket-proxy-ro, Movie-Downloads autoheal + socket-proxy-heal | docker.sock behind them |
| notify | gluetun, qBittorrent, Prowlarr, SABnzbd, Radarr, Sonarr, Bazarr, Floppy, Dependency-Track, Jellyfin, Antivirus, Portainer-Agent | state / DB migrations; gluetun holds the VPN namespace; agent must match fastpi's Portainer |
| local build | Jellyfin, clamav, LibreTranslate | reported as `local_build`; rebuilt weekly by `rebuild-local` 04:27 (Mon clamav, Fri LibreTranslate unless a translation runs, Sat Jellyfin only while nobody plays), tests + rollback, list `Docker/_global/rebuild-local.conf` |

```bash
sudo systemctl start auto-update.service; journalctl -u auto-update -n 40   # real run now
AUTO_UPDATE_STATE=/tmp/au AUTO_UPDATE_TEXTFILE=/tmp/au python3 Docker/_global/scripts/auto_update.py --dry-run   # dry run (sudo docker by itself)
SERVICES='cup-agent socket-proxy-unix' TEST_FORCE_FAIL=1 CHECK='curl -sf http://127.0.0.1:8000/api/v3/json' Docker/_global/scripts/update-project.sh Docker/Cup-Agent recreate   # rollback drill: ROLLED BACK, exit 1
sudo systemctl disable --now auto-update.timer   # pause
```

## CI

GitHub Actions, `.github/workflows/ci.yml`, on every push and pull request (`permissions: contents: read`, actions pinned by commit SHA). Same setup as HomeLab-FastPi (change the scripts there, then copy). Linters come from `.github/scripts/install-tools.sh` (pinned versions, checksums verified).

| Job | What it checks | Local command |
|---|---|---|
| `tests` | every `test_*` / `test-*` script with `CI=true`; tests that need Docker or the live host are listed in `.github/ci-skip.txt` | `bash .github/scripts/run-tests.sh [PATH_PREFIX]` |
| `shellcheck` | tracked shell scripts, warnings and errors, minus `.github/lint-baseline.toml` | `python3 .github/scripts/lint.py shellcheck` |
| `hadolint` | every Dockerfile, warnings and errors, minus the baseline | `python3 .github/scripts/lint.py hadolint` |
| `compose config and policy` | `docker compose config` of every project (dummy `.env`, `.github/scripts/ci-compose-env.sh`) and the container policy without pulling images | `bash .github/scripts/ci-compose-env.sh && python3 Docker/_global/scripts/policy_check.py --offline` (the script writes `.env` files: only on a clean clone) |
| `gitleaks` | full history, `.gitleaks.toml` | `gitleaks git --config .gitleaks.toml .` |
| `actionlint` | the workflow files | `actionlint` |

| Change | Where |
|---|---|
| A test CI cannot run | line in `.github/ci-skip.txt`: `glob \| reason` |
| A lint finding you do not fix | entry in `.github/lint-baseline.toml` (file, code, count, reason); a new finding or a stale entry fails the job |
| A Python package a test needs | `.github/requirements-ci.txt` |
| An optional variable that must stay blank in the dummy `.env` | `.github/ci-env-blank.txt` |
