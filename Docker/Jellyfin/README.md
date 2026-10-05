# Jellyfin — media playback on beefy

Serves the movie library that `../Movie-Downloads` fills, with **QuickSync
hardware transcoding** on the i5-11400's UHD 730. Public at
`https://jellyfin.holy-grail.ch`, and a request **wakes beefy** if it is asleep.

**Not deployed yet.** This folder is scaffolding; see the prerequisites below, and
note the Traefik half lives in the *other* repo.

Deliberately a separate compose project from the download stack: restarting the
downloaders has no business interrupting playback, and the library outlives any
particular acquisition tooling.

## Prerequisites (sudo)

```bash
# Same two steps as Movie-Downloads - skip whichever you have already done.
sudo usermod -aG docker buntu                       # log out/in afterwards
sudo groupadd -g 1100 media
sudo useradd -u 1101 -g 1100 -M -s /usr/sbin/nologin media

sudo mkdir -p /srv/video/media/movies /srv/appdata/jellyfin
sudo chown -R 1101:1100 /srv/video/media /srv/appdata/jellyfin

# Confirm the render gid still matches RENDER_GID in .env (it is assigned
# dynamically and can shift across OS upgrades).
getent group render                                  # expected: render:x:990:
```

## Deploy

```bash
cd ~/Projects/Docker/Jellyfin
docker compose up -d
docker compose logs -f jellyfin
```

Then verify hardware transcoding is genuinely active, rather than assuming:

```bash
# The container must be able to open the render node.
docker exec jellyfin ls -la /dev/dri/renderD128

# Start a transcode in the UI, then watch the GPU actually do work.
sudo intel_gpu_top          # apt install intel-gpu-tools
```

In the UI: **Dashboard → Playback → Transcoding → Hardware acceleration = Intel
QuickSync (QSV)**, and enable hardware decoding for H264/HEVC. If the Video Engine
row in `intel_gpu_top` stays at 0% while a transcode runs, it fell back to
software — check `RENDER_GID` first, that is almost always the cause.

Why this matters more than usual here: software transcoding pins the CPU, and on a
host that sleeps when idle, a pegged CPU is the difference between "sleeps shortly
after you stop watching" and "never sleeps again".

## Routing — the other half lives on fastpi

Traefik and the Cloudflare tunnel run on **fastpi**, so publishing this is a
change to the `HomeLab-FastPi` repo, not this one. Two pieces:

1. A dynamic config, e.g. `Traefik/.../dynamic/jellyfin.yml`, routing
   `jellyfin.holy-grail.ch` → `http://192.168.1.102:8096`.
2. The existing **`beefy-wake` forwardAuth middleware** attached to that router,
   so a request to a sleeping beefy returns the "waking up" page, fires the WoL
   packet, and succeeds on retry ~60–90 s later. This middleware has existed since
   June and has never been attached to anything — Jellyfin is its first real
   consumer. Use the `?port=8096` readiness override so the gate waits for
   Jellyfin itself rather than merely for SSH, otherwise a cold boot answers 502
   for the seconds between sshd and Jellyfin being ready.

Also add the Cloudflare DNS record and tunnel ingress for the hostname.

### Two deliberate decisions

**No Authentik forwardAuth on this route.** Jellyfin's native apps (TV, mobile)
cannot follow an interactive auth redirect, so putting Authentik in front breaks
every client that is not a browser. Protection is Jellyfin's own authentication
plus the standard public-route chain already applied to every other service:
CrowdSec enforcement, rate limiting, secure headers. Use a strong admin password
and disable Jellyfin's "allow remote connections without authentication".

**Cloudflare's TOS discourages proxying video** through the tunnel (§2.8,
non-HTML content). Plenty of homelabs do it and the realistic worst case is a
warning rather than a ban, but it is a known risk accepted knowingly. If it ever
bites, the fallback is to keep the tunnel for the UI and move the stream path to
a direct DNS record, or restrict the route to LAN + WireGuard — you already have
working VPN access from outside.

## Storage

| Mount | Path | Mode |
|---|---|---|
| Library | `/srv/video/media` → `/media` | **read-only** |
| Config, DB, artwork cache | `/srv/appdata/jellyfin` → `/config` | read-write |

The library is read-only on purpose: Radarr owns that tree, and `:ro` means no
metadata misconfiguration can rewrite or delete a movie. Config sits on the NVMe
rather than `/srv/video` because it is small, written constantly, and would
otherwise keep the media disks spinning.

## Related

- `../Movie-Downloads` — fills `/srv/video/media/movies`
- `../../Server/8-Idle-Watcher.md` — what keeps beefy awake
- `HomeLab-FastPi` → `Docker/Beefy-Waker/` — the wake gate and its README
