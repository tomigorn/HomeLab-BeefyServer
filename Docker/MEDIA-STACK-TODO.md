# Media stack — outstanding work

Tracking for the Jellyfin + Movie-Downloads build on beefy. Started 2026-10-05.
Scaffolding is committed; **nothing is deployed**.

Legend: 🔴 blocker · 🟡 needed before real use · ⚪ later / optional

---

## 🔴 Blockers — must be done BEFORE the first arr container ever starts

- [ ] **Docker boot-order drop-in** (storage doc §6 / §14.2-F). If Docker starts
      before mergerfs is mounted, a container bind-mounts an **empty** `/srv/video`
      and an arr can mark the whole library missing and **delete it**. The doc calls
      this "a hard prerequisite, not optional".
      ```ini
      # /etc/systemd/system/docker.service.d/10-require-srv-video.conf
      [Unit]
      RequiresMountsFor=/srv/video
      After=srv-video.mount
      Requires=srv-video.mount
      ```
      `sudo systemctl daemon-reload && sudo systemctl restart docker`
      (Drop the `/srv/audio` references from the doc's snippet — that tier was
      retired 2026-07-03.) Do **not** add `nofail`; failing closed is correct.

- [ ] **Reconcile container identity.** Storage doc §14.0 says *"use these verbatim:
      `PUID=1000`/`PGID=1000` (buntu), `UMASK=002`"*. The scaffolded `.env` files
      currently say `PUID=1101`/`PGID=1100` and have **no UMASK**. The doc was
      written with beefy in mind and `/srv/audio` was already chowned `1000:1000`
      under it. Decide, then make both `.env` files and the README prerequisites
      agree. See "Known conflict" below.

- [ ] **`sudo usermod -aG docker buntu`** — the `docker` group has no members, so
      `buntu` can currently reach no daemon at all. Log out/in afterwards.

- [ ] **Pool ownership + umask** once identity is settled:
      `sudo chown -R <uid>:<gid> /srv/video` (and `chmod -R 2775` for setgid).

- [ ] **Align the on-pool directory layout to storage doc §13.2** (see conflict below).

---

## 🔴 Known conflicts between the scaffolding and the storage doc

These exist because the scaffolding was authored while beefy was asleep and the
storage doc could not be read. Resolve before deploying.

| Thing | Storage doc §13.2/§14.0 | Scaffolded | Action |
|---|---|---|---|
| Identity | `1000:1000` + `UMASK=002` | `1101:1100`, no UMASK | decide |
| Torrent path | `/srv/video/torrents/movies` | `/srv/video/downloads/torrents/...` | decide |
| Usenet path | `/srv/video/usenet/{incomplete,complete/movies}` | `/srv/video/downloads/usenet/...` | decide |
| Library path | `/srv/video/media/movies` | same ✅ | none |

The doc's layout is referenced by §13.3–§13.10 for every app, so diverging means
those sections no longer describe reality.

---

## 🟡 Secrets and app configuration

- [ ] **Second ProtonVPN WireGuard key** for beefy (NOT fastpi's — two tunnels on
      one key fight over the session and both drop). Must be a **port-forward
      capable** server. Goes in `Movie-Downloads/.env` → `PROTONVPN_PRIVATE_KEY`.
- [ ] **Usenet provider credentials** in SABnzbd (host/port/user/pass, SSL).
- [ ] **qBittorrent → Options → WebUI → "Bypass authentication for clients on
      localhost"** — without it `scripts/qbit-port.sh` gets a 403 and the forwarded
      port is silently never applied, which looks exactly like a dead tracker.
- [ ] **qBittorrent seed limits: ratio 2.0 OR 30 days, then stop** (§14.0). This is
      what makes a torrent demotable and keeps the hot SSD from filling.
- [ ] **Per-category disk-space limits** in qBittorrent and SABnzbd (§14.2-B) so a
      large pack cannot start without room and spill to the cold HDD.
- [ ] **arr Recycle Bin → `/srv/video/.recyclebin`, 7-day cleanup** (§14.2-D). Keeps
      deletes and upgrades off the HDD. Never point it at a cold-branch path.
- [ ] **Radarr: import-and-keep** — do not hard-delete a download on upgrade while it
      may still be seeding (§14.2-C).
- [ ] Prowlarr → Radarr indexer sync; Radarr download clients `gluetun:8080` (qBit)
      and `sabnzbd:8080`.

## 🟡 Jellyfin

- [ ] Verify QSV is genuinely active, not silently software: `intel_gpu_top` Video
      Engine must move during a transcode. `RENDER_GID` (currently 990) is assigned
      dynamically — recheck after any OS upgrade.
- [ ] Disable extract-on-play/scan for cold content; schedule trickplay/BIF,
      embedded-subtitle extraction and Intro-Skipper into the **04:00–06:00** window
      or run them on import while the file is still on SSD (§14.2-I).
- [ ] Strong admin password; disable remote connections without authentication.

## 🟡 fastpi side (HomeLab-FastPi repo, not this one)

- [ ] Traefik dynamic config: `jellyfin.holy-grail.ch` → `http://192.168.1.102:8096`.
- [ ] Attach the existing **`beefy-wake` forwardAuth** with **`?port=8096`** so the
      gate waits for Jellyfin rather than merely for sshd (otherwise a cold boot
      answers 502 in the gap). This middleware has existed since June 2026 and has
      never been attached to any router — Jellyfin is its first consumer.
- [ ] Cloudflare DNS record + tunnel ingress for the hostname.
- [ ] **Decided: no Authentik forwardAuth** on this route (breaks native TV/mobile
      clients). CrowdSec + rate limiting + Jellyfin's own auth instead.
- [ ] Accepted risk on record: Cloudflare TOS discourages proxying video (§2.8).

---

## ⚪ Storage tiering — the largest unbuilt piece

The mover (§5) and promoter (§7) are **designed but not built**. Until they exist:

- [ ] **Interim: keep the hot SSD manually below `minfreespace=50G`.** At that point
      `moveonenospc=true` starts putting new writes **directly on the cold HDD** and
      nothing moves them back. The doc's warning: *"Don't run the download stack
      unattended at scale before the mover exists."*
- [ ] **Install `attr`** (`getfattr`) and optionally `mergerfs-tools` — needed to ask
      "which tier is this file on" without guessing.
- [ ] Build the **mover** (§5): demote cold video SSD→HDD nightly in the 04:00–06:00
      window. Copy→fsync→verify→delete (never rename: cross-branch rename is
      `EXDEV`). Skip open/seeding files, never demote sidecars, honour a pin list.
- [ ] Build the **promoter** (§7): pre-promote on Jellyfin detail-view so playback
      never binds to the HDD.
- [ ] Consider an **NVMe scratch dir for SABnzbd `incomplete/`** (§13.4) — unpack
      writes are heavy and do not belong on the media pool.

## ⚪ Later / open

- [ ] Push the two beefy scaffolding commits (`3a540cb`, `5d26dcb`) — local only.
- [ ] Samba shares for `/srv/video` (never raw branches) — none defined yet.
- [ ] Sonarr/TV: out of scope for now; layout already reserves `tv/` paths.
- [ ] `Specs/specs.md` has an unrelated uncommitted local edit — not ours.

## ⚪ Carried over from the m4b work (2026-10-05)

- [ ] Decide on the 2 audiobooks with damaged sources (`Jack Ryan/01 - Patriot
      Games`, `Jack Ryan/04 - Cardinal Of The Kremlin`). Parked in
      `m4b-merge/runs/failures.json`; originals untouched. Re-download un-parks them
      automatically.
- [ ] Design abort-safe shutdown for the merge orchestrator: SIGTERM currently skips
      the `finally` that releases `/run/beefy-keep-awake`, so a `docker stop`
      mid-merge leaves beefy awake forever. Needs a way to cancel in-flight books.
