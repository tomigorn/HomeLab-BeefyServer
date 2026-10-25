# MediaServer
This git folder is located in ~/Projects

## Image auto-update

Nightly, by tier, with a functional check and automatic rollback. `Docker/_global/scripts/{auto_update.py,test_auto_update.py,update-project.sh}` are verbatim copies of fastpi's (single source: HomeLab-FastPi `Docker/_global/scripts/`, doc `Server/4-Security-Monitoring/auto-updates.md` "beefy"): change them there, then copy.

| Piece | What |
|---|---|
| `Docker/_global/auto-update.conf` | tiers, services, backups, functional check per stack |
| `Docker/_global/systemd/auto-update.{service,timer}` | 04:12 inside the 03:55 wake; nothing starts after 04:16 (rest `deferred`), max 2 projects a night, hard stop after 10 min; holds `beefy-keep-awake` only while running (an existing hold stays) |
| `Docker/_global/install-auto-update.sh` | installs and enables the timer |
| Result | `/var/lib/auto-update/textfile/auto_update.prom`, pulled by fastpi at 04:20 → Telegram `Nightly auto-update beefy` |
| Failed rollback | paged at once from beefy itself: `Docker/_global/scripts/oob-alert.sh` (verbatim copy of fastpi's, direct Telegram via DoH), secrets `Docker/_global/secrets/oob-alert.env` + `telegram_bot_token` (600, gitignored, same bot as fastpi's Alertmanager) |

| Tier | Stacks | Why |
|---|---|---|
| auto | Movie-Downloads flaresolverr, recyclarr, vpn-status; Cup-Agent cup-agent, socket-proxy-unix | stateless; pinned version tags (socat floats): a new digest is a rebuild. 24 h cool-down, never across a major, rolled back when the check fails |
| notify | Cup-Agent socket-proxy-ro, Movie-Downloads autoheal + socket-proxy-heal | docker.sock behind them |
| notify | gluetun, qBittorrent, Prowlarr, SABnzbd, Radarr, Sonarr, Bazarr, Seerr, Ryot + ryot-db, Portainer-Agent | state / DB migrations; gluetun holds the VPN namespace; Seerr pinned by digest; agent must match fastpi's Portainer |
| local build | Jellyfin, clamav, ryot-go | reported as `local_build`; rebuild by hand (`docker build --network host`) |

```bash
sudo systemctl start auto-update.service; journalctl -u auto-update -n 40   # real run now
AUTO_UPDATE_STATE=/tmp/au AUTO_UPDATE_TEXTFILE=/tmp/au python3 Docker/_global/scripts/auto_update.py --dry-run   # dry run (sudo docker by itself)
SERVICES='cup-agent socket-proxy-unix' TEST_FORCE_FAIL=1 CHECK='curl -sf http://127.0.0.1:8000/api/v3/json' Docker/_global/scripts/update-project.sh Docker/Cup-Agent recreate   # rollback drill: ROLLED BACK, exit 1
sudo systemctl disable --now auto-update.timer   # pause
```
