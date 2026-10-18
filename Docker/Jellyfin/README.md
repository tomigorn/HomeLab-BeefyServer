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

**The Docker boot-order drop-in (storage doc §6 / §14.2-F) applies here too** — the
snippet is in `../Movie-Downloads/README.md`. Jellyfin only reads the library so it
cannot delete anything itself, but the guard must exist before *any* container
bind-mounts `/srv/video`. Skip whichever steps you have already done:

```bash
sudo usermod -aG docker buntu                       # log out/in afterwards

# Identity is 1000:1000 = the existing `buntu` user (storage doc §14.0). Nothing to
# create, and it is the same identity Radarr writes the library with.
sudo mkdir -p /srv/video/media/movies /srv/appdata/jellyfin
sudo chown -R 1000:1000 /srv/video/media /srv/appdata/jellyfin
sudo chmod -R 2775 /srv/video/media /srv/appdata/jellyfin

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

The full transcoding settings, verification commands and measured results are in
**Hardware transcoding** below. If the Video Engine
row in `intel_gpu_top` stays at 0% while a transcode runs, it fell back to
software — check `RENDER_GID` first, that is almost always the cause.

Why this matters more than usual here: software transcoding pins the CPU, and on a
host that sleeps when idle, a pegged CPU is the difference between "sleeps shortly
after you stop watching" and "never sleeps again".

## Hardware transcoding

**Status: on and verified (2026-10-18).** Intel QuickSync on the i5-11400's UHD 730
(Rocket Lake, Xe-LP gen12). Decodes H.264, HEVC 8/10/12-bit (incl. 4:2:2/4:4:4
RExt), VP9 8/10-bit, AV1, MPEG-2, VC-1; encodes H.264 and HEVC. **No AV1 encode.**

### What makes it work

| Piece | Where | Why |
|---|---|---|
| `/dev/dri` passed through + `group_add: ${RENDER_GID}` | `docker-compose.yaml` | Container can open `renderD128` (inside it shows as group `groupdma3` - that is just the name for gid 990, harmless) |
| `DOCKER_MODS=linuxserver/mods:jellyfin-opencl-intel` | `docker-compose.yaml` | The LSIO image ships **no Intel OpenCL** (only `nvidia.icd`). Still needed on 12.1/ls51; the mod installs the `legacy1` runtime, which is the one gen12 needs. Only used as the tone-mapping fallback for Dolby Vision / HLG - HDR10 goes through VPP |
| `i915.enable_guc=2` | `/etc/default/grub.d/i915-guc.cfg` (host, **not in this repo**) | Loads the HuC firmware, which the low-power (VDEnc) encoders need. Kernel 7.0 leaves RKL at `0` (HuC off). GuC *submission* stays off. Backup of the original: `/etc/default/grub.bak-2026-10-18-pre-guc` |

Verify the host side after a kernel/OS upgrade:

```bash
cat /proc/cmdline | grep -o 'i915.enable_guc=[0-9]'      # i915.enable_guc=2
sudo dmesg | grep -i huc                                  # "HuC: authenticated for all workloads"
getent group render                                       # still gid 990 = RENDER_GID?
docker exec jellyfin ls /etc/OpenCL/vendors/              # intel.icd intel_legacy1.icd (mod applied)
```

`dmesg` also says `Setting dangerous option enable_guc - tainting kernel`. That is
printed for *any* non-default value of the parameter and is expected.

To undo GuC/HuC: `sudo rm /etc/default/grub.d/i915-guc.cfg && sudo update-grub &&
sudo systemctl reboot`, then untick the two low-power boxes below (LP encode
without HuC fails).

### Settings (Dashboard -> Playback -> Transcoding, stored in `/config/encoding.xml`)

- Hardware acceleration: **Intel QuickSync (QSV)**, device left default (`/dev/dri/renderD128`)
- Hardware decoding: **H264, HEVC, MPEG2, VC1, VP9, AV1** + HEVC 10-bit, VP9 10-bit,
  HEVC RExt 8/10-bit, HEVC RExt 12-bit
- Prefer OS native DXVA/VA-API decoders: on (default)
- **Enable hardware encoding**: on. **Intel low-power H.264 / HEVC encoder**: on (needs HuC, above)
- **Allow encoding in HEVC**: on. AV1 encoding: off (no hardware for it)
- **Enable VPP tone mapping**: on (brightness 16, contrast 1 - defaults).
  **Enable tone mapping** (OpenCL): on, as fallback for what VPP cannot do
- **Throttle transcodes**: on (180 s). **Delete segments**: on (720 s)
- Transcode path: default `/config/cache/transcodes` = `/srv/appdata/jellyfin/cache/transcodes`
  on the **NVMe** - transcoding never touches the HDD pool, so it cannot keep disks spinning

Scripted changes go through the API (`GET`, edit, `POST` the *whole* object to
`/System/Configuration/encoding`). The header must be
`Authorization: MediaBrowser Token="<key>"` - `X-Emby-Token` is rejected. An API key
named "automation (hw-transcode setup)" lives in `secrets/api-key` (gitignored, 600);
delete it in Dashboard -> API Keys if you no longer want it.

### Pipeline Jellyfin picks (4K HDR10 HEVC -> 1080p SDR H.264)

From `/config/log/FFmpeg.Transcode-*.log` - VA-API decode, VPP scale + VPP tone map,
QSV low-power encode, all on the GPU with no frame ever copied to system RAM:

```
-init_hw_device vaapi=va:,vendor_id=0x8086,driver=iHD -init_hw_device qsv=qs@va -init_hw_device opencl=ocl@va
-hwaccel vaapi -hwaccel_output_format vaapi
-vf setparams=...bt2020/smpte2084...,scale_vaapi=w=1920:h=1080,procamp_vaapi=b=16,
    tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709,hwmap=derive_device=qsv,format=qsv
-codec:v:0 h264_qsv -low_power 1 -preset veryfast
```

### Measured (2026-10-18)

Test file: synthetic 90 s 3840x2160 HEVC Main10 HDR10 (BT.2020/PQ), 40 Mbps, with
noise so it is not trivially compressible - the library was still empty. It is kept
at `/srv/appdata/jellyfin/hwtest/` (450 MB, NVMe) for re-testing; it is not in any
library. Transcodes were started through the real HLS API (`/Videos/<id>/master.m3u8`,
8 Mbps, max 1920x1080, H.264 + AAC) so Jellyfin built the command itself.

| Test | fps per stream | Realtime | Host CPU | GPU (intel_gpu_top) |
|---|---|---|---|---|
| 1 stream, LP off | 113 | 4.7x | ~13 % | Video 80 %, VideoEnhance 70 %, Render 72 % |
| 1 stream, LP on | 117 | 4.9x | ~13 % | Video 95 %, VideoEnhance 77 %, Render 55 % |
| **2 streams, LP on** | **57 + 57** | **2.4x each** | ~11 % | Video 99 %, VideoEnhance 80 %, Render 66 % |
| 1 stream, HEVC out, LP on | 124 | 5.2x | - | - |

Target was 2 concurrent 4K HDR -> 1080p SDR: met with ~2.4x headroom. The Video
engine (decode of a 40 Mbps 10-bit stream + encode) is the bottleneck, so LP mode
barely changes throughput but takes ~25 % load off the Render/EU engine. Host CPU
is mostly AAC audio encoding and Jellyfin itself. No errors in any transcode log.
Real films have far less noise than the test clip, so expect equal or better.

### Re-test

```bash
# Watch the GPU (any engine at 0 % during a transcode = software fallback):
sudo intel_gpu_top

# Jellyfin's exact pipeline on the test clip, to /dev/null - expect ~115 fps:
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -loglevel error -stats \
  -init_hw_device vaapi=va:,vendor_id=0x8086,driver=iHD -init_hw_device qsv=qs@va -filter_hw_device qs \
  -hwaccel vaapi -hwaccel_output_format vaapi -i "/config/hwtest/HW Test 4K HDR10 (2026).mkv" -an \
  -vf "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc,scale_vaapi=w=1920:h=1080:extra_hw_frames=24,procamp_vaapi=b=16,tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709:extra_hw_frames=32,hwmap=derive_device=qsv,format=qsv" \
  -c:v h264_qsv -low_power 1 -preset veryfast -b:v 8M -maxrate 8M -bufsize 16M -f null -

# After a real playback: which pipeline ran, and did anything fail?
L=$(ls -t /srv/appdata/jellyfin/log/FFmpeg.Transcode-*.log | head -1)
grep -m1 '^/usr/lib/jellyfin-ffmpeg/ffmpeg' "$L" | grep -o -- '-codec:v:0 [a-z0-9_]*\|tonemap_[a-z]*'
grep -ci error "$L"
```

### Gotchas

- **Silent software fallback** is the failure mode: wrong `RENDER_GID` after an OS
  upgrade, or the OpenCL mod failing to download. Check `intel_gpu_top`, not the UI.
- The OpenCL mod is installed on container **create** (pulled from ghcr). A
  `--force-recreate` while ghcr is unreachable gives a container without OpenCL -
  HDR10 still works via VPP, Dolby Vision tone mapping would not.
- LP boxes ticked but HuC not loaded (kernel param lost) = transcodes **fail**, not
  fall back. Re-check `dmesg | grep -i huc` after kernel changes.
- Enabling HEVC encoding lets HEVC-capable clients receive HEVC transcodes; if a
  specific client misbehaves, untick "Allow encoding in HEVC".
- `X-Emby-Token` header is rejected by this Jellyfin version; use `Authorization: MediaBrowser Token=...`.

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

## Network, previews and subtitle font (2026-10-18)

Set via the API (they live in /config, not in this repo):

- **Networking:** Known proxies `192.168.1.2` (Traefik on fastpi), LAN networks
  `192.168.1.0/24` + `172.24.0.5/32` (the wg-easy VPN NAT address). Without this every
  client looked like fastpi, so internet viewers counted as "local". No remote bitrate
  limit on purpose: most viewers are remote.
- **Trickplay (seek previews):** hardware decode + hardware MJPEG encode, key frames only,
  generated **during library scans** (non-blocking). The default nightly 03:00 task would
  never run because beefy sleeps at night.
- **Subtitle fallback font:** `/config/fonts/NotoSansJP-Regular.otf` (Noto Sans JP subset,
  SIL OFL) for ASS/SSA subtitles with CJK glyphs; `EnableFallbackFont` on.
