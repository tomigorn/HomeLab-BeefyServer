# Jellyfin — media playback on beefy

Serves the movie library that `../Movie-Downloads` fills, with **QuickSync
hardware transcoding** on the i5-11400's UHD 730. Public at
`https://jellyfin.holy-grail.ch`, and a request **wakes beefy** if it is asleep.

Live since 2026-10-09; the Traefik half lives in the *other* repo (HomeLab-FastPi).

Deliberately a separate compose project from the download stack: restarting the
downloaders has no business interrupting playback, and the library outlives any
particular acquisition tooling.

## Prerequisites (sudo)

**The Docker boot-order drop-in (storage doc §6 / §14.2-F) applies here too** — the
snippet is in `../Movie-Downloads/README.md`. Jellyfin only reads the library so it
cannot delete anything itself, but the guard must exist before *any* container
bind-mounts `/srv/video`. Skip whichever steps you have already done:

```bash
# Jellyfin runs as its own uid 65108 (PUID) and reads the library through its
# other-read bits; the library itself belongs to group media (Movie-Downloads README).
sudo mkdir -p /srv/appdata/jellyfin
sudo chown -R 65108:1000 /srv/appdata/jellyfin
sudo chmod 2770 /srv/appdata/jellyfin

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
| `Dockerfile` (OpenCL debs from the `jellyfin-opencl-intel` mod image, pinned by digest) | `Dockerfile` | The LSIO image ships **no Intel OpenCL** (only `nvidia.icd`). Docker Mods do not run in linuxserver non-root mode, so the mod's `legacy1` + current runtime debs are installed at build time. Only used as the tone-mapping fallback for Dolby Vision / HLG - HDR10 goes through VPP. Bump: new digest of `lscr.io/linuxserver/mods:jellyfin-opencl-intel`, then `docker compose up -d --build` |
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

| Library | Path | Filled by |
|---|---|---|
| Movies | `/media/movies` | Radarr (`../Movie-Downloads`) |
| Shows (added 2026-10-19 via `POST /Library/VirtualFolders`, same options as Movies: real-time monitor, trickplay during scan, `en`/`CH` metadata) | `/media/tv` | Sonarr |

The library is read-only on purpose: Radarr and Sonarr own that tree, and `:ro` means no
metadata misconfiguration can rewrite or delete a file. A new, empty library is not
watched until its first full scan (see the gotcha in `../Movie-Downloads/README.md`). Config sits on the NVMe
rather than `/srv/video` because it is small, written constantly, and would
otherwise keep the media disks spinning.

## Related

- `../Movie-Downloads` — fills `/srv/video/media/movies` and `/srv/video/media/tv`
- `../Seerr` — requests, reads availability from this server
- `../../Server/8-Idle-Watcher.md` — what keeps beefy awake
- `HomeLab-FastPi` → `Docker/Beefy-Waker/` — the wake gate and its README

## Network, previews and subtitle font (2026-10-18)

Set via the API (they live in /config, not in this repo):

- **Networking:** Known proxies `192.168.1.2` (Traefik on fastpi), LAN networks
  `192.168.1.0/24` + `172.24.255.5/32` (the wg-easy VPN NAT address, moved from 172.24.0.5 on 2026-10-20). Without this every
  client looked like fastpi, so internet viewers counted as "local". No remote bitrate
  limit on purpose: most viewers are remote.
- **Trickplay (seek previews):** hardware decode + hardware MJPEG encode, key frames only,
  generated **during library scans** (non-blocking). The default nightly 03:00 task would
  never run because beefy sleeps at night.
- **Scheduled tasks moved into beefy's maintenance window 03:58–04:45 (2026-10-26, was 04:00–06:00 on 2026-10-19)** so the cold
  HDD wakes once a night and no task waits for a wake that never reaches its interval. `scripts/night-tasks.py` sets them (idempotent, `--dry-run`; tests `python3 -m unittest test_night_tasks`):
  IMDb ratings 04:08, Scan Media Library 04:10, Trickplay 04:24, Chapter Images 04:30, Media Segment Scan 04:33,
  subtitles 04:35, audio normalization 04:36, lyrics 04:37, channels 04:38, guide 04:39, cache/log/transcode clean 04:40-04:42, Update Plugins 04:43, Optimize database 04:44, Sunday: Refresh People 04:31, upcoming episodes 04:32.
  The startup triggers of Clean Transcode and Update Plugins are gone (they ran on every day wake); JavaScript Injector Startup keeps its own. New movies do not wait for these: Radarr
  Connect triggers a refresh of the new folder on import (see
  `../Movie-Downloads/README.md` "Download → library pipeline"), and real-time
  monitoring stays on.
- **Subtitle fallback font:** `/config/fonts/NotoSansJP-Regular.otf` (Noto Sans JP subset,
  SIL OFL) for ASS/SSA subtitles with CJK glyphs; `EnableFallbackFont` on.

## SSO only: "Sign In with Holy Grail" (2026-10-18)

Jellyfin has **no password login for anyone**, like every other homelab app. Users
sign in through Authentik; TV/phone apps use **Quick Connect**. There is deliberately
**no break-glass login over the web**, not even from the LAN or VPN.

Plugin: **OIDC Authentication 0.3.4.0** (`edmogeor/jellyfin-plugin-oidc`, repo
`https://raw.githubusercontent.com/edmogeor/jellyfin-plugin-oidc/manifest-release/manifest.json`),
config in `/config/data/plugins/configurations/Jellyfin.Plugin.Oidc.xml` (holds the
client secret; not in this repo). Authentik app `jellyfin`, groups `streaming-users`
(sign in) and `streaming-admins` (Jellyfin administrator). The plugin sets or removes
administrator at every OIDC sign-in (`OidcUserProvisioner.cs`, v0.3.4.0); it reads only
direct groups from the token, so the groups are independent (HomeLab-FastPi
`Authentik/scripts/provision-media-groups.py`). Losing every allowed group blocks new
sign-ins only; running sessions stay until revoked in Dashboard -> Devices.

| Setting (Dashboard -> Plugins -> OIDC Authentication) | Value | Why |
|---|---|---|
| Provider / Provider name | **Other** / **Holy Grail** | Button reads "Sign In with Holy Grail", matching the Authentik brand and Audiobookshelf. A named provider (Authentik) forces its own name and ignores Provider name |
| Local password sign-in | **Disable for all users** | Server-side: every user is switched to `Jellyfin.Plugin.Oidc.Identity.OidcPasswordDisabledProvider`, so even a correct password is refused. The plugin also hides the password form, user tiles, "Manual login" and "Forgot Password"; "Use Quick Connect" stays |
| Redirect sign-in page to provider | off | Keeps the Jellyfin sign-in page so "Use Quick Connect" stays reachable |
| Automatic linking | Match email to Jellyfin username | New SSO users are provisioned on first sign-in |

No Branding custom CSS is needed: in "Disable for all users" mode the plugin does the
hiding itself (it is the plugin's documented SSO-only mode).

**Second layer, in Traefik on fastpi** (`HomeLab-FastPi` ->
`Docker/Traefik/traefik/dynamic/jellyfin.yml`, router `jellyfin-password-login-block`):
`/Users/AuthenticateByName`, `/Users/{id}/Authenticate` and `/Users/ForgotPassword*`
answer **403 for every client** (any case, `/emby` prefix, trailing slash). Quick
Connect (`/QuickConnect/*`, `/Users/AuthenticateWithQuickConnect`) is not matched.

Users at the switch: only `admin` (local, administrator, last used 2026-10-14) - it
is now password-disabled too. The first Authentik sign-in by a `streaming-admins`
member creates that person's own Jellyfin administrator account.

Automation still works: the API key in `secrets/api-key` is a token, not a password
login, and is unaffected.

### Recovery when Authentik is down (server shell only)

Playback with existing sessions and Quick Connect approvals from an already
signed-in browser keep working; only *new* sign-ins need Authentik. If you really
need a password login, use the plugin's documented emergency recovery on beefy and
reach Jellyfin **directly, not through Traefik** (Traefik refuses password logins):

```bash
cd ~/Projects/Docker/Jellyfin && docker compose stop jellyfin
C=/srv/appdata/jellyfin/data
sudo cp -a $C/plugins/configurations/Jellyfin.Plugin.Oidc.xml{,.bak-recovery}
sudo sed -i 's#<Enabled>true</Enabled>#<Enabled>false</Enabled>#' $C/plugins/configurations/Jellyfin.Plugin.Oidc.xml
sudo sqlite3 $C/data/jellyfin.db ".backup $C/data/jellyfin.db.bak-recovery"
sudo sqlite3 $C/data/jellyfin.db "UPDATE Users SET AuthenticationProviderId='Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider' WHERE Name='tomigorn' AND AuthenticationProviderId='Jellyfin.Plugin.Oidc.Identity.OidcPasswordDisabledProvider';"
docker compose start jellyfin
```

Then, from your PC: `ssh -L 8096:127.0.0.1:8096 beefy` and open
`http://localhost:8096` (the LAN address is firewalled to fastpi only). The local `admin` user was deleted on
2026-10-19 (Holy Grail is the only login), so first give `tomigorn` a password:
`sudo sqlite3 $C/data/jellyfin.db "UPDATE Users SET Password=NULL WHERE Name='tomigorn';"`
(empty password, set a new one right after signing in). Sign in as `tomigorn`. Fix
the problem, then put it back: Plugins -> OIDC Authentication -> enable, "Local
password sign-in" = **Disable for all users**, save (the plugin switches the user
back to the disabled provider). Check with
`GET /Users` that every user shows `OidcPasswordDisabledProvider`.

Backups taken at the switch: `Jellyfin.Plugin.Oidc.xml.bak-2026-10-18-pre-oidc-only`
and `data/data/jellyfin.db.bak-2026-10-18-pre-oidc-only`.

### Direct port 8096

Published on `0.0.0.0:8096` because Traefik on fastpi connects over the LAN. The firewall (`../_global/docs/container-hardening.md`, Firewall) lets only fastpi reach it over IPv4 and nothing over IPv6. VPN clients reach it through fastpi (NAT to 192.168.1.2).
Checked 2026-10-20 from a second LAN address: every app port times out, from fastpi they answer.

## Audio and subtitle language per user (2026-10-20)
Server-side per user, so every client that takes the server's track choice follows it.

| Setting (web: profile -> Settings) | Page | Default for new users |
|---|---|---|
| Preferred audio language | Playback | **Original language** |
| Play default audio track regardless of language | Playback | **off** (on = the track flagged default wins over the language, often a dub) |
| Remember audio selections | Playback | on |
| Preferred subtitle language | Subtitles | empty: pick yours |
| Subtitle mode | Subtitles | **Smart**: subtitles when the audio is not in your subtitle language, otherwise forced only |
| Remember subtitle selections | Playback | on |

- "Remember": a track you pick is stored per user **per film/episode** and used next time; the next episode in a queue keeps your pick (web/phone). A new episode otherwise starts with your language settings.
- Jellyfin has no default user settings: `scripts/user-defaults.py` (user timer `systemd/jellyfin-user-defaults.timer`, every minute) gives each NEW user the column above once and never touches them again. Users present at install were only switched to "remember" (tomigorn already was: audio preference empty, play default track on, subtitles empty, mode Default).
- Tell new members: set **Preferred subtitle language**, otherwise Smart shows forced subtitles only.

| Client | Uses these settings | Where to change them |
|---|---|---|
| Web, Jellyfin Android app, Jellyfin iOS app (wrap the web client) | yes | in the app: Settings -> Playback / Subtitles |
| Android TV app (0.19.10) | yes, server's default tracks; inside one play queue it keeps the audio language you picked | no settings page: change them in web or phone |
| Swiftfin (iOS, 1.6.1) | yes, server's default tracks | no settings page in 1.6.1: web or phone |

Sources: jellyfin v12.1 `MediaSourceManager.cs` / `MediaStreamSelector.cs`, jellyfin-androidtv v0.19.10 `PlaybackController.java`, jellyfin-android `JellyfinMediaSource.kt`, Swiftfin 1.6.1 `MediaPlayerItem.swift`.

| Task | Command (in `scripts/`) |
|---|---|
| Install timer | `cp ../systemd/jellyfin-user-defaults.* ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now jellyfin-user-defaults.timer` |
| What it would do | `./user-defaults.py --dry-run` |
| Handled users | `cat ~/.local/state/jellyfin-user-defaults.json` (delete = re-adopt everyone, changes nothing) |
| Log | `journalctl --user -u jellyfin-user-defaults.service --since today` |
| Tests | `python3 -m unittest -v test_user_defaults` |

### Pick the audio track by hand (e.g. Swiss German vs High German)
A Swiss German track is often tagged `ger` like High German, so "Preferred audio language" cannot tell them apart: pick it by its title (e.g. "Schweizerdeutsch").

| Client | Before playing | While playing |
|---|---|---|
| Web, Android app, iOS app (web wrapper) | film page: **Audio** dropdown under the Play button (active with 2+ tracks) | bottom bar: **Audio** button (note icon, left of volume/gear) -> pick the track |
| Android TV | – | playback overlay: **Select audio track** button -> list; optional: Settings -> remote "Audio track button" |

Sources: jellyfin-web v10.10.7 `controllers/itemDetails/index.html` (`selectAudio`), `controllers/playback/video/index.html` (`btnAudio`), jellyfin-androidtv `SelectAudioAction.kt`, `strings.xml`.

## Playback
Server side is done (Dashboard -> Playback): QSV decode + low-power encode, VPP tone mapping, HEVC encoding allowed, throttling + segment deletion, fallback font, trickplay, no remote bitrate limit, known proxy fastpi so LAN/VPN vs internet is judged right. No per-film playback profile exists in Jellyfin; per film only the remembered tracks (above) and, for films with several versions, the version menu.

Per device (stored on the device, not on the server):

| Client | Recommended |
|---|---|
| Web | Settings -> Playback: max streaming bitrate **Auto** (kept separately for home and away), "Prefer fMP4-HLS" **on** (default), audio channels **Auto**, DTS/TrueHD **off** (browsers cannot pass them through) |
| Android TV | Settings -> Playback: max bitrate default 100 Mbps; raise it at home for 4K remuxes, lower it away if it buffers. Audio: **Direct stream** with an AV receiver/soundbar, **Downmix to stereo** on TV speakers; AC3 bitstream on (default); player ExoPlayer (default) |
| Phone apps | max bitrate **Auto** |

Direct play (no transcode) is always best; the server transcodes only what the device cannot play.

## Tracker (Floppy): watch history per member (2026-10-27)
Jellyfin posts each member's playback to their own Floppy account (`../Floppy/`, README "Integrations"). One webhook per Jellyfin user, bound by `UserId`, so one member's playback never lands in another member's Tracker.

| Plugin | Repo | Version | Config |
|---|---|---|---|
| Webhooks (shemanaev) | `https://raw.githubusercontent.com/shemanaev/jellyfin-plugin-repo/master/manifest.json` | 3.10.0.120000 (Jellyfin shows 3.10.0.0) | `/config/data/plugins/configurations/Jellyfin.Webhooks.xml`, holds the members' Floppy tokens (full API keys), not in git |
| JavaScript Injector (n00bcodr) | `https://raw.githubusercontent.com/n00bcodr/jellyfin-plugins/main/12/manifest.json` | 4.0.0.0 | `/config/data/plugins/configurations/Jellyfin.Plugin.JavaScriptInjector.xml`, entry "Rate in Tracker" written by `tracker-link.py` |

- Not the official "Webhook" plugin: its payload has only the episode's own ids, and 12.1 episodes have no TMDB id. shemanaev's "Default" format nests the series with its TMDB id, so episodes land on their show.
- Route: `http://192.168.1.102:8012/webhook/jellyfin/<token>`, beefy's own published Floppy port (hairpin), never Traefik: `/webhook/` is 403 there. DOCKER-USER lets only the `jellyfin` subnet and fastpi reach that port.
- Hook per member: name `Tracker <user>`, format Default, events Play, Stop, MarkPlayed, MarkUnplayed.

### Add a household member
1. Member is in Holy Grail (`streaming-users`, automatic on invite) and has signed in to Jellyfin and to the Tracker tile once (creates both users).
2. Owner on beefy (reads the member's token from Floppy itself, nothing to copy):
   ```bash
   cd ~/Projects/Docker/Jellyfin/scripts && python3 tracker-webhook.py add <jellyfin-user> [<floppy-user>]
   ```
3. Test: member marks a film played (or watches to the end) → it shows as completed in their Tracker.

| Task | Command (in `scripts/`) |
|---|---|
| List hooks (URLs masked) | `python3 tracker-webhook.py list` |
| New token (rotated in Floppy) | `python3 tracker-webhook.py add <user>` again, replaces the hook |
| Remove a member | `python3 tracker-webhook.py remove <user>` |
| Tests | `python3 -m unittest -v` |
| Not arriving | `sudo docker logs jellyfin 2>&1 \| grep -i webhook \| tail` here, `sudo docker logs floppy 2>&1 \| grep webhook_finished \| tail` |

### "Rate in Tracker" link
Film, series, season and episode pages in Jellyfin web get a "Rate in Tracker" link next to the TMDB/IMDb links. It opens `https://tracker.holy-grail.ch/go/tmdb/<movie|show>/<id>?title=…`; Traefik redirects it to Floppy's TMDB details page (HomeLab-FastPi `Traefik/traefik/dynamic/tracker.yml`, router `tracker-go`). Episodes and seasons link to their show. No TMDB id = no link. Web only, apps and TV have none.

- Script: `tracker-link/rate-in-tracker.js`, served by JavaScript Injector from `private.js` (signed-in users only).
- Not johnpc's Custom JavaScript: deprecated, and it rewrites `index.html` on disk (root-owned in the LSIO image). JavaScript Injector rewrites the response.

| Task | Command (in `Docker/Jellyfin/`) |
|---|---|
| Tests (JS) | `sudo docker run --rm -v "$PWD":/w -w /w node:24-alpine node --test "tracker-link/*.test.mjs"` |
| Tests (upsert) | `cd scripts && python3 -m unittest -v test_tracker_link` |
| Push a changed script | `python3 scripts/tracker-link.py` → `upserted Rate in Tracker`, then reload the browser |
| Check it is served | `curl -s localhost:8096/web/index.html \| grep -c -i injector` → ≥ 1 |

### Rating on TV and phone
Jellyfin's TV and phone apps have only the heart (favourite) and "played"; no app has stars (jellyfin-androidtv `ItemMutationRepository.kt`), and Floppy has no native app.

- Phone: open `https://tracker.holy-grail.ch`, browser menu -> **Install app** / **Add to Home screen**. Floppy is a PWA; its manifest and icons are reachable without sign-in for that (HomeLab-FastPi `Traefik/traefik/dynamic/tracker.yml`, router `tracker-pwa`).
- TV: rate later on the phone; the TV app has no rating.
- Not built: hearts -> Tracker. The webhook events carry no favourite change.

## IMDb link and rating (2026-10-25)
- Film and series pages (web) show an **IMDb** and **TMDB** link row (`ExternalUrls`, built from `ProviderIds`); all 9 items have both IDs, nothing to fix. Apps show no link row.
- IDs come from the TMDb provider on library scan / refresh. New item without IMDb link: Refresh metadata (not "Replace all") on the item.
- Rating shown is the IMDb rating: plugin **IMDb Ratings NG 6.1.0** (repo `https://verybadsoldier.github.io/jellyfin-plugin-imdbratings-ng/manifest.json`, targetAbi 12.0.0.0). It reads IMDb's public datasets (`datasets.imdbws.com`, personal non-commercial use), no account or key, and writes CommunityRating.
- Enabled as metadata downloader on Movies (Dashboard -> Libraries -> Movies); Shows use the defaults, which include it. Scheduled task "Update IMDb Ratings" runs daily at 03:00; ratings also apply on Refresh metadata.
- Verified: Ikiru 8.314 -> 8.3, The Secret in Their Eyes 7.965 -> 8.2 (= IMDb dataset).
- Rollback: stop jellyfin, remove `/srv/appdata/jellyfin/data/plugins/IMDb Ratings NG_*`, start. Pre-change backup: `/srv/appdata/jellyfin-pre-imdb-2026-10-25.tgz` (root, mode 600).

**Rebuild** (`jellyfin-local`, FROM `lscr.io/linuxserver/jellyfin` + the OpenCL mods image by digest): bump the tag in `Dockerfile` when Cup shows a newer linuxserver tag, then `sudo docker compose up -d --build --pull`; otherwise monthly.
