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
delete it in Dashboard -> API Keys if you no longer want it. A second key, "avatar-sync", lives on fastpi (`HomeLab-FastPi` `Avatar-Picker/secrets/jellyfin_api_key`) and sets user images after an avatar pick.

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
  IMDb ratings 04:08, Scan Media Library 04:10, Intro Skipper analysis 04:13 (and 22:05), Trickplay 04:24, Chapter Images 04:30, Media Segment Scan 04:33,
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
| Preferred audio language | Playback | **empty = original language** (the track flagged default; `original-audio.py` flags the original track) |
| Play default audio track regardless of language | Playback | **on** (the track flagged default is played) |
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
| JavaScript Injector (n00bcodr) | `https://raw.githubusercontent.com/n00bcodr/jellyfin-plugins/main/12/manifest.json` | 4.1.0.0 | `/config/data/plugins/configurations/Jellyfin.Plugin.JavaScriptInjector.xml`, entry "Rate in Tracker" written by `tracker-link.py` |

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

## Home screen sections (2026-10-27)
A Netflix-like home screen for every user. The plugin replaces the home screen wholesale, so the vanilla rows are re-created by it.

| Plugin (IAmParadox27) | Version | Why |
|---|---|---|
| Home Screen Sections | 3.0.3.0 (targetAbi 12.2) | the sections |
| File Transformation | 3.0.2.0 | required: rewrites the served web client in memory, no file on disk changes |
| Plugin Pages | 3.0.2.0 | required: the user's "Modular Home" settings page |

- Repo: `https://www.iamparadox.dev/jellyfin/plugins/manifest.json` ("IAmParadox27"). The nightly "Update Plugins" task (04:43) updates them like the other plugins.
- Config: `/config/data/plugins/configurations/Jellyfin.Plugin.HomeScreenSections.xml` (600). It holds the **Sonarr and Radarr API keys** (full keys: the arrs have no read-only key), not in git. Admins see them in Dashboard -> Plugins -> Home Screen Sections.
- Set by `scripts/home-sections.py` (idempotent, `--dry-run`, never prints keys; tests `python3 -m unittest test_home_sections`). Rerun after changing the order or rotating an arr key. `--check` → `all N users ok` (what every user actually gets).

Groups (owner 2026-10-29): the wide-card rows and the My List band divide the home into groups. Order = `home-sections.py` `ORDER` (plugin rows) plus `home-layout.js` `LAYOUT` (where our rows go).

| # | Row | Shows |
|---|---|---|
| 1 | Continue Watching (`ContinueWatching`) | as vanilla |
| 2 | Next Up (`NextUp`) | as vanilla |
| 3 | Coming soon / Demnächst (injector, `HssComingSoon`) | Upcoming Shows + Upcoming Movies in one row: one card per series (its next episode, `S03 · Wed 11 Nov`), films `Movie · <date>`; soonest week first, most popular (TMDB) first within a week |
| 4 | My List ♥ (`MyList`) | the user's favourites (♥) as a tinted full-width band with compact posters (divider) |
| 5 | Recently Added Movies (`RecentlyAddedMovies`) | vanilla "Latest" for Movies |
| 6 | Recently Added Shows (`RecentlyAddedShows`) | vanilla "Latest" for Shows |
| 7 | Studios / Studios (injector) | divider; big studios (`MAJOR_TIER` in `studio-logos.py`) always, the other `MAJOR_STUDIOS` only with 2+ titles the user can see; each film under one studio, most films first, max 20; white logo on a dark card (name without logo), film count in the corner |
| 8 | Recently Watched Movies / Kürzlich angesehene Filme (injector) | films played to the end in the last 60 days, newest first, max 20 |
| 9 | Recently Watched TV Series & Collections / Kürzlich angesehene Serien & Filmreihen (injector) | series watched completely (every regular episode with a file; specials and unaired ignored), last episode in the last 60 days; film collections with every film played, last one in the last 60 days; newest first, max 20 |
| 10 | Networks / Sender (injector) | divider; well-known networks only (`NETWORKS` in `studio-logos.py`: global streamers, major US/UK/DE/CH broadcasters, Crunchyroll), each series under one network, most series first, max 20 |
| 11 | Because you watched X / Weil du X geschaut hast (injector, `HssBecauseYouWatched`) | unwatched films and series similar to X (`/Items/X/Similar`, the user's library); X = a new random pick on every home load |
| 12 | Hidden Gems (`HiddenGems`) | rated 7.5+ (IMDb rating), not watched yet |
| 13 | Movie Genres / Film-Genres (injector) | divider; film genres, most films first, max 20; name over a duotone backdrop (best rated film of the genre) |
| 14 | Finish These Collections (`FinishTheseCollections`) | started collections, closest to done first |
| 15 | Collections (`CollectionsSection`) | film collections |
| 16 | Series Genres / Serien-Genres (injector) | same as Movie Genres for series; closes the page |

- Layout script `home-placeholders/home-layout.js`, JavaScript Injector entry "Home layout": hooks into `empty-sections.js` (`window.HssHomeExt`: layout, names, hints, skeleton state, row sync with its card markup), so placeholders, skeletons and slots work the same for its rows. Styles: My List band (Jellyfin blue to purple tint, uppercase heading, pink heart, posters about 70 % size; phone 27vw; `MyList` view mode Portrait, set by `home-sections.py`), a hairline above each wide-card row.
- Because you watched: seed pool = played films, completely watched series and series with a played episode (3 requests, cached 10 min); weight liked x4 (favourite, thumbs up or rating 7+) and watched in the last 30 days x2; never the previous seed again (`localStorage` `hss-byw:<user id>`); a seed with fewer than 3 unwatched similar titles is replaced (3 tries). Replaces the plugin row: films only, the last 15 played, no preference for liked titles.
- Coming soon: the plugin's two Upcoming results (`/HomeScreen/Section/UpcomingShows` and `UpcomingMovies`, the user's session; they answer while the sections are switched off), cached per user in the browser (shown up to 6 h, refreshed after 10 min). Popularity: `scripts/upcoming-popularity.py` reads the Sonarr/Radarr calendars (next 100 days), asks Seerr for each title's TMDB popularity and writes the injector entry "Home upcoming" (`window.HssUpcomingPop`, no keys in the browser); runs after the studio logos (`jellyfin-studio-logos.service`, 04:38; `--dry-run`). Date as before: `Wed 11 Nov` / `Mi., 11. Nov.`, year only when not this year. Cards open nothing (as the plugin's). The plugin returns at most 16 upcoming episodes: a series dropping more than that in a month pushes later series out.
- Public path: Cloudflare caches `/web/*` files. File Transformation rewrites them but keeps the original ETag, so a cached copy revalidated to 304 forever and public users kept a pre-plugin bundle ("Modular Home" errored, home vanilla; 2026-10-27). Fix in HomeLab-FastPi `Traefik/traefik/dynamic/jellyfin.yml` (router `jellyfin-web` drops If-None-Match/If-Modified-Since). Still stale: Cloudflare → Caching → Custom Purge the `/web/` URL.
- Off on purpose: My Media (library tiles; removed 2026-10-28, owner: the nav buttons above do the same), Watch Again (removed 2026-10-27, owner), Starring and Directed by (removed 2026-10-28, owner), By Studio (2026-10-29, owner: duplicates Studios), the plugin's Because You Watched and Upcoming Shows/Movies (2026-10-29, replaced by our rows; the arrs stay configured), Jellyseerr Discover / Trending / My Requests, Genre, Decade, and every other section.
- Empty sections stay visible: heading plus a grey one-line hint (English, German if the UI language is German). HSS itself hides empty sections.
  - Script `home-placeholders/empty-sections.js`, JavaScript Injector entry "Empty home sections" (signed-in users). It reads the user's enabled sections (`/ModularHomeViews/UserSettings`, in config order: `home-sections.py` stores them sorted) and the names (`/ModularHomeViews/Sections`), adds a placeholder where a section is missing or empty, and removes it when the section fills. No HSS markup = does nothing.
  - Recently Watched Movies + TV Series & Collections: same script, built in the browser with the user's own session (`/Users/<id>/Items`), Jellyfin's own card markup, right after Recently Added Shows, where Watch Again used to sit (`RECENT_AFTER`, films first), locked like the rest (no user setting). Not via HSS `RegisterSection`: the plugin would POST user ids, unauthenticated, to a path on the public URL. Shows a finished title on the next home load (refreshed in the background after 60 s).
    - Queries: played films and played episodes (`IsPlayed`, `SortBy=DatePlayed`), all collections; then one `Ids=` request for the candidate series (their `UnplayedItemCount`), only if one has unwatched episodes the unwatched specials (`ParentIndexNumber=0`) and unaired ones, and one `Limit=1` request per fully watched collection (the API has no multi-parent filter). 3 requests with nothing played.
  - Studios, Networks, Movie Genres, Series Genres (2026-10-28, like Seerr's Discover): same script, user's own session, 4 requests (`UserViews`, films and series with `Fields=Studios,Genres`, one `Ids=` request for the logo tags). Genre click = Jellyfin's own list (`#/list?genreId=…`, `parentId` = the Movies or Shows library). Studio/network click = our own full-screen list of exactly that card's titles (`openList`, one `Ids=` request with the user's session, sorted by name; Escape / TV back / × closes, picking a title opens it; TV: focus stays inside). Jellyfin's studio page is not used: it would also list the titles counted under a bigger studio. Only what the user can see, sorted by title count.
    - One card per title (owner 2026-10-28): `MAJOR_STUDIOS` / `NETWORKS` are in precedence order (bigger first), aliases grouped into one card (Warner Bros. Pictures + Entertainment + Animation = "Warner Bros."). A title counts only for its first listed studio/network (Harry Potter: Warner Bros., not 1492 Pictures or Heyday; a Pixar film with Walt Disney Pictures: Pixar; Marvel Studios and Lucasfilm also come before Walt Disney). Titles without a listed one are not in the row. Matched by TMDB id; the TMDB name only for a title whose TMDB lookup failed (e.g. the Harry Potter Collection item, a TMDB collection id). Placed by `home-layout.js` (`LAYOUT`) as dividers between the groups.
    - Logos: `scripts/studio-logos.py` (user timer `jellyfin-studio-logos.timer`, 04:38 in the maintenance window, after the library scan). It asks Seerr (its built-in TMDB access, no own key, nothing in the browser) for each new title's companies and networks, downloads TMDB's white logo (`w500_filter(duotone,ffffff,bababa)`, like Seerr) once and stores it as the studio's **Logo** image in Jellyfin (same origin, scaled and cached by Jellyfin). Logos without a transparent background are skipped (they would be a white box). It also writes the injector entry "Home networks" (`window.HssNetworks`: Jellyfin keeps networks and production companies in the same Studios list). The entry also holds `window.HssStudios`; both are the cards with their Jellyfin studio ids in precedence order. Lists: `MAJOR_STUDIOS` and `NETWORKS` at the top of the script; small studios and stations (e.g. 30 Japanese local TV stations of one anime) stay out. Add one: id from themoviedb.org/search/company (or /network), rerun the script. Every film and series is looked up once (cached). A changed list reloads the browser-cached rows on the next home load. Lookups cached in `~/.cache/jellyfin-studio-logos.json` (delete it to look everything up again). `--dry-run` shows what it would do; tests `python3 -m unittest test_studio_logos`.
    - Genre colours: fixed per genre (`GENRE_COLORS`, German names mapped), unknown genres get a stable spare colour. Duotone in CSS (greyscale backdrop × light colour, lightened with the dark colour).
  - Speed and no jumping:
    - Every enabled row has its slot from the first paint: a section that has not answered yet shows a skeleton row of its card shape (portrait/landscape from the catalogue) instead of the hint, so rows fill in place instead of popping in. The script sees a plugin section answer through the browser's resource timing (nothing patched); after 4 s a missing section shows its hint. Once the plugin has listed its sections, a section it left out shows its hint at once (no skeleton, no late jump).
    - No spinner at the bottom: with pagination on (fresh page hash, see below) the plugin asks for page 2 whenever the page is scrolled to the very bottom, shows a spinner under the last row and pins the scroll until the empty answer arrives (the reported flicker). Page 1 already holds every section (14 < 30), so the script marks the plugin's paging finished (`HssPageMeta.Finished`) as soon as page 1 holds fewer sections than a page: no page-2 request, no spinner.
    - Coming soon has a skeleton slot until its data is there (or paints at once from the browser cache); the plugin's Upcoming rows are off, so no raw episode cards flash.
    - Our rows (recently watched, studios, networks, genres) and the section list are cached per user in the browser (`localStorage` `hss-home:<user id>`): the home paints at once from the last load and reloads in the background (section list and watched rows after 1 min, shown from cache up to 10 min; studios/networks/genres after 24 h), repainting only what changed. Not a nightly server-side preload: that would store everyone's watch data on the server and need an admin key; the browser cache keeps it per user and fresh.
    - Measured 2026-10-28 (LAN, owner): before, plugin rows appeared one by one 4.0-5.1 s after navigation and our rows at 6.6 s (page jumped as each arrived); after, a reload paints every slot at 1.1 s and the slow plugin rows fill their skeleton by 1.4-1.6 s; first-ever load: all slots at 4.7 s.
    - Remaining delay: 5 of the 15 plugin requests per home load wait about 1.1 s on the public path (direct to Jellyfin they take < 0.3 s). Cause: the fastpi Beefy-Waker gate (Traefik `forwardAuth` on every Jellyfin request) runs Python's `ThreadingHTTPServer` with the default listen backlog of 5, so a burst of parallel requests gets SYNs dropped and retried after 1 s. Fix belongs in HomeLab-FastPi (`Docker/Beefy-Waker/app/waker.py`: `request_queue_size` 64).
  - Leaving the home (fixed 2026-10-29): Jellyfin calls `pause()` on every `.itemsContainer` in the home tab, unguarded. One plain div of ours there (Top 10 rows, skeletons) threw `t.value.pause is not a function`: React error panel, Favourites tab never loaded, logo/Home dead, TV home button gone (`noHomeButtonHeader` stuck). Rule: our rows use no plain `.itemsContainer` (Top 10: `hssTopScroll`); skeletons get no-op `pause`/`resume`, `pausable()` stubs any other on every pass. The placeholder logic counts a `hssTopScroll` with cards as filled (2026-10-29 03:30 to 04:05 each Top 10 row got a second "Nothing here yet" row and the rows after Top 10 Movies jumped to the top of the home).
  - DOM changes are handled once per frame (`requestAnimationFrame`), not once per change: a home visit fired ~800 observer calls, ~400 ms of script; now ~120 ms.
  - TV layout (Samsung app): its clock changes the page every second, so the script runs at the latest 2.5 s after the first change instead of waiting for silence.
  - Push a changed script: `python3 scripts/home-placeholders.py` → `upserted Empty home sections` + `upserted Home layout`, then reload (browser Ctrl+Shift+R, Samsung app: close fully). No restart.
  - Tests: in `home-placeholders/` `sudo docker run --rm --network host -u $(id -u):$(id -g) -e npm_config_cache=/tmp/npm -v "$PWD":/w -w /w node:24-alpine sh -c "npm ci && node --test"`; upsert, order, popularity: `cd scripts && python3 -m unittest test_home_placeholders test_home_sections test_upcoming_popularity`.
- Locked for all users, current and new (`home-sections.py`):
  - Server-side: `AllowUserOverride` off globally and on every section. The plugin then ignores the user's "Enable Modular Home" pref and their section ticks; Plugin Pages drops the "Modular Home" menu entry. Each user also gets `useModularHome=true` (display prefs `usersettings`/`emby`).
  - Hidden only (cosmetic, injector script `lockHomeSettings`): the 10 "Home screen section" pickers in Settings → Home. They do nothing under Modular Home. "Hide watched content", library order and folder groups stay.
  - Pagination on, 30 sections per page (`LazyLoadEnabled`, `NumSectionsPerPage`): the client sends a fresh page hash per home load, so new watch history shows on the next load. Without it the plugin reuses one section list per user while the home is opened within the hour.
- Coming soon data: Jellyfin calls `http://192.168.1.102:8989` / `:7878`, beefy's own published ports (DNAT bridge to bridge, no firewall rule, no shared network).

| Client | Shows the sections |
|---|---|
| Web, Jellyfin Android / iOS app, Samsung TV (Tizen) app (wrap the web client) | yes (force-reload once) |
| Android TV app, Swiftfin, Infuse (native) | no, vanilla home |

| Task | Command (in `Docker/Jellyfin/`) |
|---|---|
| Studio logos | `python3 scripts/studio-logos.py` (`--dry-run`); timer install: `cp systemd/jellyfin-studio-logos.* ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now jellyfin-studio-logos.timer` |
| Library options | `python3 scripts/library-options.py` (`--check`): automatic movie collections on the Movies library (franchises become BoxSets, e.g. Harry Potter Collection); after adding films a library scan creates them; tests `python3 -m unittest test_library_options` in `scripts/` |
| Check | `../_global/scripts/post-update-check.sh Jellyfin` (ids `jf.plugin-home-sections`, `jf.home-sections-*`; `../CUSTOMIZATIONS.md`) |
| Rollback | stop jellyfin, remove `/srv/appdata/jellyfin/data/plugins/{Home Screen Sections,Plugin Pages,File Transformation}_*`, start. Pre-change backup: `/srv/appdata/jellyfin-pre-hss-2026-10-27.tgz` (root, 600) |

## Currently popular (2026-10-29)
What streams in Switzerland right now. Every rank is shown; a title not in the library can be requested only when a suitable release exists. Nothing is downloaded automatically; a user's button press makes a normal Seerr request.

| Where | What |
|---|---|
| Top bar (desktop), app drawer (phone), 📈 header button next to search (TV layout) | **Currently popular** / **Gerade beliebt** → page under the header (injector overlay, Escape / TV back / × / any nav link closes, the logo too) |
| Page | Top 10 Shows + Top 10 Movies in Switzerland (Netflix), one row per service (`SERVICES` in `scripts/popular.py`: Netflix, Prime Video, Disney+, Apple TV+, Sky, Play Suisse, Paramount+, blue TV), one row per genre (`GENRES`: Action, Comedy, Drama, Thriller & Crime, Sci-Fi & Fantasy, Animation, Documentaries, Kids & Family, Horror, Romance); the first 10 of each |
| Home | **Top 10 Shows in Switzerland** after Recently Added Shows, **Top 10 Movies in Switzerland** after Recently Watched TV Series & Collections (slots via `window.HssExtraRows` in `empty-sections.js`) |
| Card | Netflix look: big outlined rank number, the poster covering a quarter of it (#10 almost not), TMDB poster, German poster/title in a German UI. In the library = normal card, opens the Jellyfin item. Release found = dimmed, `1080p`/`2160p`, buttons **🌙 Tonight** (Can wait, night download) and **⚡ Now** (`download-now` tag); TV layout: icon-only 🌙 / ⚡ (the labels did not fit the TV poster width), the row title shows what they mean, the full hint stays in the accessible label; the page remembers the poster size per window width, so a repeat open sets it before attaching the page (one layout instead of two; 6× CPU TV test: freeze on open 0.9 s → 0.55 s); after the request the state (🌙 / 🔍 / 📅 / ⬇️ from `request-state.json`). No release = greyscale with a red diagonal strike, **No release yet** / **Noch kein Release**, buttons disabled, hint "checked 29 Oct · rechecked daily" (not in the allow-list, so the backend refuses it too); Netflix title without a TMDB match: name only, "Title not identified" |
| Size | ≥ 1280 px wide (desktop 1920/2560, TV 1920): the poster width is computed from the row width so all 10 fit without scrolling (`fitPoster`: 10 cards = 15.3 posters + 9 gaps, posters ≥ 90 px); narrower (phone, tablet): the row scrolls |

### Sources

| Rows | Source | Terms |
|---|---|---|
| Top 10 Shows / Movies | Netflix Top 10, official weekly per-country list, `https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv` (country `CH`, published Tuesdays) | public download offered on top10.netflix.com |
| Services, genres | TMDB discover via Seerr (`/api/v1/discover/movies|tv`, `watchRegion=CH`, `watchProviders`, `genre`, `sortBy=popularity.desc`; Seerr's TMDB access, no own key) | TMDB API; streaming availability is JustWatch data via TMDB: credited on the page |
| Netflix title → TMDB id | Seerr search, exact title (also original title), most popular of the type; series season from Netflix's season name (`Season 4`, a TMDB season name, `Limited Series` = the only season, else the latest aired). Wrong or missing match: `scripts/popular-overrides.tsv` (`kind<TAB>Netflix title<TAB>TMDB id`) | |

### Release check (never grabs)

| | |
|---|---|
| How | Prowlarr search only (no Radarr/Sonarr entry, no download): NzbPlanet, NZBgeek by IMDb id / TVDB id + season, then Knaben by name; first suitable release wins. Treasure Maps is not used (night-search budget) |
| Suitable | 1080p or 2160p; no CAM/TS/TC/screener; ≥ 1.5 GB film, ≥ 0.3 GB episode, ≥ 1 GB season pack; a dub without the original audio is refused (a language tag other than the original without DL/DUAL/MULTI); torrents ≥ 1 seeder; name search: name must start with the title + year (±1) / season |
| Throttle | ≤ `POP_SEARCH_MAX` (300) searches per run, `POP_SEARCH_DELAY` 2 s apart; only the 10 listed titles of each row are checked |
| Cache | `~/.local/state/jellyfin-popular.json`: no release → again the next night (20 h), found → weekly, owned → never; a title whose release shows up flips to the buttons on that run; lists and TMDB details rebuilt weekly |
| Metrics | `~/.local/state/jellyfin-popular.prom` (atomic, every run incl. failure, not `--dry-run`): `jf_popular_last_run_timestamp_seconds`, `jf_popular_last_run_success` (1/0), `jf_popular_items{list}`, `jf_popular_items_available{list}` (release found or owned; lists `home-shows`, `home-movies`, `service-*`, `genre-*`), `jf_popular_searches_total`; fastpi `beefy-idle-pull` copies it |
| Schedule | user timer `jellyfin-popular.timer` 04:39 (maintenance window) and 22:20 (only when beefy is awake for night downloads; never wakes it). Netflix list fetched only when a new week is due (Tuesday) |

### Requests: backend and threat model

Any signed-in Jellyfin user may press a button; no Seerr login or `streaming-requesters` group needed.

| | |
|---|---|
| Path | `https://jellyfin.holy-grail.ch/hss-popular/` → fastpi Traefik (`HomeLab-FastPi` `Docker/Traefik/traefik/dynamic/jellyfin-popular.yml`: jellyfin-wake, rate limit, secure headers) → container `jellyfin-popular` (`popular/app.py`, python alpine, uid 65128, read-only, no caps, 64 MB, port 8097 answers only fastpi: DOCKER-USER) |
| Who | the caller's own Jellyfin token (`X-Emby-Token`) checked with Jellyfin `/Users/Me` (cached 30 s); invalid, an API key (no user) → 401, disabled user → 403 |
| What | body `{"id", "mode": night|now, "check"}` only. `id` = opaque random entry id from the current allow-list `/srv/appdata/jellyfin-popular/allow.json` (written by `popular.py`: kind, TMDB id, the season shown, the found resolution). Unknown/tampered id → 403; any other field (tmdb, season, title …) → 400; list older than 9 days → 403; entry without a found release → 403; owned / requested / declined in Seerr → 409 |
| Limits | 10 requests per user per 7 days (`POPULAR_LIMIT`, `POPULAR_DAYS` in `.env`; the button shows "Limit reached"), 30 per day for everyone (`POPULAR_DAILY_MAX`), 10 POSTs per user per minute, body ≤ 2 KB, JSON only, `Origin` must be `https://jellyfin.holy-grail.ch` |
| As whom | Seerr API key (`secrets/popular/seerr.apikey`, buntu:65128 0640) with `X-Api-User`: the Seerr user linked to the caller's Jellyfin id when it may request, else the local Seerr user **Jellyfin popular** (`jellyfin-popular`, permissions 160 = REQUEST + AUTO_APPROVE, random unused password, local login is off; created on first use). Tags: only `download-now` for ⚡ |
| Audit | `/srv/appdata/jellyfin-popular-state/audit.jsonl` (uid 65128): time, Jellyfin user + id, entry id, title, mode, decision, Seerr user, request id. Tokens are never logged |
| Not covered | a user can spend their own 10 requests on listed titles; an admin removes or declines in Seerr as usual (declined titles stay refused) |
| Verified 2026-10-29 | public path → `{"ok": true}`; real browser button press (owner session, Quick Connect test device, request rewritten to `check`) → 200 requestable with the browser's Origin; tampered id 403, extra `season` 400, no token 401, API key as token 401. A real Seerr request was not made in the test (it would have downloaded at once in the low tariff) |

| Task | Command (in `Docker/Jellyfin/`) |
|---|---|
| Lists now | `scripts/popular.py` (`--dry-run`: no searches, no writes; `--tsv FILE`: Netflix TSV downloaded elsewhere when netflix.com is unreachable) |
| UI script | `python3 scripts/popular-ui.py` → `upserted Currently popular UI` (injector entry, signed-in users); data entry "Currently popular" (`window.HssPopular`) is written by `popular.py` |
| Backend | `sudo docker compose up -d --no-deps popular`; `curl -s localhost:8097/hss-popular/health` |
| Timer | `cp systemd/jellyfin-popular.* ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now jellyfin-popular.timer` |
| Audit | `sudo tail /srv/appdata/jellyfin-popular-state/audit.jsonl` |
| Tests | `cd scripts && python3 -m unittest test_popular test_popular_ui`; `cd popular && python3 -m unittest test_app`; JS: `node --test popular.test.mjs` in `home-placeholders/` (same docker command as above) |

## Skip intro / credits buttons: Intro Skipper (2026-10-27)
Episodes and films get **"Skip intro" / "Skip credits"** buttons. Buttons only, nothing is skipped automatically.

| Plugin | Repo | Version | Config |
|---|---|---|---|
| Intro Skipper (intro-skipper) | `https://intro-skipper.org/manifest.json` | 12.0.6.0 (targetAbi 12.2) | `/config/data/plugins/configurations/IntroSkipper.xml`; defaults kept in `~/backups/jellyfin/IntroSkipper.xml.default-12.0.6.0` |

- Detects intros, credits, recaps, previews (also from subtitles: "Previously on", "Was bisher geschah"), commercials. It writes them as Jellyfin's native **media segments**; Intro Skipper is the only segment provider (both libraries, default on).
- Changed from the defaults: automatic analysis of new items **off** (it would run in the day), subtitle recap/preview detection **on**. Auto skip intro/credits stays **off**, File Transformation injection stays **off**: the client's native button is the only one.
- Analysis: task "Detect and Analyze Media Segments" daily **22:05** (night downloads) and **04:13** (window, after the 04:10 library scan), set by `scripts/night-tasks.py`. Already analysed seasons are skipped, so a night run only does new episodes. The idle watcher sees the run as CPU/disk load (15 % / 2 MB/s); 04:13 is inside the window hold.
- Re-analyse a series: Dashboard -> Intro Skipper (main menu) -> series / season -> erase its segments, then Dashboard -> Scheduled Tasks -> "Detect and Analyze Media Segments" -> run (in the evening; beefy's CPU is busy for minutes per season).

| Client | Button |
|---|---|
| Web, Jellyfin Android / iOS app, Samsung TV (Tizen) app (wrap the web client) | yes: Intro and Credits default to "Ask to skip" |
| Android TV app | own media segment setting (Settings -> Playback), not tested here |
| Recap, Preview, Commercial | off by default on every device: Settings -> Playback -> Advanced (bottom) -> "Ask to skip". Per device, the server cannot set it |

| Task | Command (in `Docker/Jellyfin/`) |
|---|---|
| Check | `../_global/scripts/post-update-check.sh Jellyfin` (ids `jf.plugin-intro-skipper`, `jf.intro-skipper-*`, `jf.segment-provider`) |
| Rollback | stop jellyfin, remove `/srv/appdata/jellyfin/data/plugins/Intro Skipper_*`, start. Pre-change backup: `~/backups/jellyfin/jellyfin-config-pre-introskipper-2026-10-27-210156.tar.gz` |

## IMDb link and rating (2026-10-25)
- Film and series pages (web) show an **IMDb** and **TMDB** link row (`ExternalUrls`, built from `ProviderIds`); all 9 items have both IDs, nothing to fix. Apps show no link row.
- IDs come from the TMDb provider on library scan / refresh. New item without IMDb link: Refresh metadata (not "Replace all") on the item.
- Rating shown is the IMDb rating: plugin **IMDb Ratings NG 6.1.0** (repo `https://verybadsoldier.github.io/jellyfin-plugin-imdbratings-ng/manifest.json`, targetAbi 12.0.0.0). It reads IMDb's public datasets (`datasets.imdbws.com`, personal non-commercial use), no account or key, and writes CommunityRating.
- Enabled as metadata downloader on Movies (Dashboard -> Libraries -> Movies); Shows use the defaults, which include it. Scheduled task "Update IMDb Ratings" runs daily at 03:00; ratings also apply on Refresh metadata.
- Verified: Ikiru 8.314 -> 8.3, The Secret in Their Eyes 7.965 -> 8.2 (= IMDb dataset).
- Rollback: stop jellyfin, remove `/srv/appdata/jellyfin/data/plugins/IMDb Ratings NG_*`, start. Pre-change backup: `/srv/appdata/jellyfin-pre-imdb-2026-10-25.tgz` (root, mode 600).

**Rebuild** (`jellyfin-local`, FROM `lscr.io/linuxserver/jellyfin` + the OpenCL mods image by digest): bump the tag in `Dockerfile` when Cup shows a newer linuxserver tag, then `sudo docker compose up -d --build --pull`; otherwise monthly.
