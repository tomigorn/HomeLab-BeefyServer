# Media stack — outstanding work

Last accurate: 2026-10-09 (post-reboot verification).
Both stacks are **deployed, live and verified in production**.

---

## ✅ Done and verified

**Infrastructure**
- Docker boot-order guard — proven in a real reboot: `srv-video.mount` at 08:54:43,
  `docker.service` at 08:54:50. Also proven to fail closed (masking the mount made
  Docker refuse to start).
- Pool tree, ownership `1000:1000`, setgid, `psmisc`+`attr`, 298G reclaimed from
  ext4's root reserve.
- **LAN exposure closed.** A `DOCKER-USER` DROP rule restricts ports
  7878/9696/6767/8080/8081/8096 to fastpi (192.168.1.2) only. **Port 8000 (Cup agent,
  added 2026-10-09) still needs adding to this list** - see `Docker/Cup-Agent/README.md`. ufw turned out to be
  the wrong tool — Docker bypasses it — and is now uninstalled. Rule persisted via
  `iptables-persistent`, survived a reboot, and **verified blocked from a Mac on the
  LAN** (not just assumed).

**Stacks**
- `Movie-Downloads` (gluetun + qBittorrent + Prowlarr in the VPN netns; SABnzbd +
  Radarr + Bazarr outside it) and `Jellyfin`. 8 containers, all restart cleanly.
- VPN verified: exit IP differs from WAN, port forwarding live, and `qbit-port.sh`
  applies new ports automatically (observed across both a reconnect and a reboot).
- **Hardlinks proven**: a file linked `torrents/`→`media/` shares one inode,
  link count 2, both on `ssd-hot`.

**SSO** — all six hostnames behind Traefik. Five arr UIs gated by Authentik forward
auth (single-application proxy providers → `streaming-admins`); Jellyfin
deliberately NOT gated (native clients cannot follow an auth redirect) and uses OIDC.

**Tiering** — `tier-report`, `tier-move` (41 tests), `beefy-mover` (18), promoter
engine (19). Mover ran overnight and correctly no-opped. Nightly wake from fastpi at
03:55 so the 04:00 window is honoured despite beefy sleeping.

**Alerting** — email + Telegram, both verified by sending. 10/10 blackbox probes.
Dead-man's switch every 28 days. Rules that fire when the alerting itself breaks.

---

## 🔴 Open — needs your accounts

- [ ] **Prowlarr indexers.** Your tracker logins. Prowlarr → Indexers → Add; they
      sync to Radarr automatically. **This is the one that makes search work** —
      without it Radarr can find nothing.
- [ ] **SABnzbd usenet provider.** Config → Servers. Torrents are unaffected.
- [ ] **Jellyfin setup wizard.** Admin account with a long random password (this is
      the break-glass credential), library `/media/movies`.
- [ ] **Jellyfin SSO plugin.** After the wizard. Repo
      `https://raw.githubusercontent.com/9p4/jellyfin-plugin-sso/manifest-release/manifest.json`,
      then configure with the Authentik Client ID/Secret saved on 2026-10-09.

## 🟡 Open — Jellyfin tuning, after the wizard

- [ ] **Verify QSV is genuinely active**, not silently software: `intel_gpu_top`'s
      Video Engine must move during a transcode. `RENDER_GID=990` is assigned
      dynamically — recheck after OS upgrades.
- [ ] Disable extract-on-play/scan for cold content; schedule trickplay/BIF,
      embedded-subtitle extraction and Intro-Skipper into 04:00–06:00, or run them
      on import while the file is still hot (§14.2-I).
- [ ] Turn off aggressive periodic library rescans — they `stat` cold files and wake
      the HDD (§13.6).

## ⚪ Optional improvements

- [ ] **Six LAN DNS overrides** (`<app>.holy-grail.ch` → 192.168.1.2 in the Zyxel) so
      local access stops round-tripping through Cloudflare and survives an internet
      outage. Still Authentik-gated, still a valid cert.
- [ ] **Gate `cup.holy-grail.ch` behind Authentik** — currently public with no auth
      at all. The middleware exists; needs an Authentik application.
- [ ] **Revoke the Telegram bot token** — it passed through a chat transcript.
      `/revoke` in BotFather, drop the new one in
      `grafana/alertmanager/secrets/telegram_bot_token`, restart alertmanager.
- [ ] **NVMe scratch dir for SABnzbd `incomplete/`** (§13.4) — unpack writes are
      heavy and don't belong on the media pool. Optional, not required.
- [ ] Wire the **promoter's trigger** once Jellyfin is running and its read
      behaviour can be measured rather than guessed.

## ⚪ Watch, no action yet

- [ ] **Keep the hot SSD above `minfreespace=50G`.** Below it, `moveonenospc=true`
      writes new files straight to the cold HDD and nothing moves them back. The
      mover handles this automatically now; check with `tier-report` if in doubt.
      Currently 7317G free, so no concern.
- [ ] Mover design note: the hot SSD uses `relatime`, so "recently watched" is
      **day-granular, not true LRU** (§14.2-J).

## ⚪ Later / out of scope

- [ ] Samba shares for `/srv/video` (never raw branches).
- [ ] Sonarr/TV — the §13.2 layout already reserves `tv/` paths.
- [ ] 4 pending security updates on beefy (`apt list --upgradable`).
- [ ] `Specs/specs.md` has an unrelated uncommitted local edit — not ours.

## ⚪ Carried over from the m4b work (2026-10-05)

- [ ] **Decide on the 2 audiobooks with damaged sources** (`Jack Ryan/01 - Patriot
      Games`, `Jack Ryan/04 - Cardinal Of The Kremlin`). Parked in
      `m4b-merge/runs/failures.json`, originals untouched. A re-download un-parks
      them automatically. Note that audio is **already unplayable in ABS today** —
      this predates the merge tooling.
- [ ] **Abort-safe shutdown for the merge orchestrator.** SIGTERM skips the `finally`
      that releases `/run/beefy-keep-awake`, so a `docker stop` mid-merge leaves
      beefy awake indefinitely. Not patched because trapping SIGTERM would block
      `docker stop` on the ThreadPoolExecutor — needs a way to abort an in-flight
      book, which is a design decision rather than a patch.
