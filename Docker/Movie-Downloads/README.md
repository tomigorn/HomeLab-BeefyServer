# Movie-Downloads — VPN, torrents, usenet and Radarr on beefy

Everything needed to acquire a movie and file it into the library: **gluetun**
(ProtonVPN) wrapping **qBittorrent** and **Prowlarr**, plus **SABnzbd**, **Radarr**
and **Bazarr** outside the tunnel. Playback is a separate project — see
`../Jellyfin`.

**Deployed 2026-10-09.** The prerequisites below are kept as the rebuild recipe.
How a finished download becomes a correctly named, subtitled, scanned library
item is in [Download → library pipeline](#download--library-pipeline).

## Ports

| Service | URL | Published on |
|---|---|---|
| qBittorrent | `http://192.168.1.102:8080` | gluetun (shares its namespace) |
| Prowlarr | `http://192.168.1.102:9696` | gluetun (shares its namespace) |
| SABnzbd | `http://192.168.1.102:8081` | itself — 8080 was taken |
| Radarr | `http://192.168.1.102:7878` | itself |
| Bazarr | `http://192.168.1.102:6767` | itself |

## Prerequisites (sudo — these are the only steps that need root)

### 0. The one that can destroy the library — do this FIRST

Storage doc §6 / §14.2-F. If Docker starts **before** mergerfs is mounted, a
container bind-mounts an **empty** `/srv/video`, and an arr will conclude the whole
library is missing and delete its entries. The doc calls this *"a hard
prerequisite, not optional"*.

```bash
sudo install -d /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/10-require-srv-video.conf >/dev/null <<'EOF'
[Unit]
RequiresMountsFor=/srv/video
After=srv-video.mount
Requires=srv-video.mount
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
```

Do **not** add `nofail` to the merged mount — failing closed is correct here. (The
doc's original snippet also referenced `/srv/audio`; that tier was retired
2026-07-03, so it is left out.)

### 1. The rest

```bash
# Let buntu talk to the Docker daemon. The `docker` group currently has no
# members, which is why `docker info` fails. Log out and back in afterwards.
sudo usermod -aG docker buntu

# Identity is 1000:1000 = the existing `buntu` user (storage doc §14.0), so there
# is no user to create. setgid (2775) so new dirs inherit the group.
sudo mkdir -p /srv/video/torrents/movies \
             /srv/video/usenet/incomplete \
             /srv/video/usenet/complete/movies \
             /srv/video/media/movies \
             /srv/video/.recyclebin \
             /srv/appdata/{gluetun,qbittorrent,prowlarr,sabnzbd,radarr,bazarr}
sudo chown -R 1000:1000 /srv/video /srv/appdata
sudo chmod -R 2775 /srv/video /srv/appdata

# psmisc gives tier-move its open-file guard (it REFUSES to run without it, since
# it deletes the source after copying); attr gives getfattr for tier queries
sudo apt install -y psmisc attr

# Reclaim ext4's 5% root reserve on the hot SSD. It exists to stop a full disk
# locking out a ROOT filesystem; on a pure media volume it is ~372G of nothing.
# Online, instant, no data touched.
sudo tune2fs -m 1 /dev/sda1
```

Then fill in `.env` — at minimum `PROTONVPN_PRIVATE_KEY`, which must be a **second,
separate** ProtonVPN WireGuard key. Reusing fastpi's key makes both tunnels drop
intermittently as the two endpoints fight over one session. Pick a server that
supports **port forwarding** or torrents will never connect.

## Deploy

```bash
cd ~/Projects/Docker/Movie-Downloads
docker compose up -d
docker compose logs -f gluetun        # wait for the tunnel + a forwarded port
curl -s http://192.168.1.102:8080     # qBittorrent should answer
```

Confirm the tunnel is actually carrying traffic before adding a single torrent:

```bash
docker exec movie-gluetun wget -qO- https://ipinfo.io/ip   # must NOT be your WAN IP
```

## Storage — one mount, or Radarr copies instead of hardlinking

Every container that touches media gets the **same** `/data` mount
(`/srv/video`), and all paths live underneath it:

The layout is storage doc §13.2 — that doc describes every app in terms of these
paths, so don't improvise alternatives:

```
/srv/video/                     -> /data in every container
├── torrents/movies/            qBittorrent save path (radarr category)
├── usenet/
│   ├── incomplete/             SABnzbd scratch + unpack
│   └── complete/movies/        SABnzbd finished, pre-import
├── media/movies/               <- Radarr root folder; Jellyfin reads this :ro
└── .recyclebin/                arr recycle bin — stays on SSD, so deletes and
                                upgrades never wake the HDD (§14.2-D)
```

Separate `/downloads` and `/movies` mounts are the classic mistake: Radarr sees two
unrelated filesystems and every import becomes a full byte-for-byte copy rather
than an instant hardlink. Configure the apps with these **container** paths:

| App | Setting | Value |
|---|---|---|
| qBittorrent | category `radarr` save path | `/data/torrents/movies` |
| qBittorrent | seed limits | **ratio 2.0 OR 30 days, then stop** (§14.0) |
| SABnzbd | incomplete / complete | `/data/usenet/incomplete` / `/data/usenet/complete/movies` |
| Radarr | root folder | `/data/media/movies` |
| Radarr | **Use Hardlinks instead of Copy** | **on** (§13.6) |
| Radarr | Recycle Bin | `/data/.recyclebin`, 7-day cleanup |
| Radarr | qBittorrent client | host `gluetun`, port `8080` |
| Radarr | SABnzbd client | host `sabnzbd`, port `8080` |
| **Prowlarr** | storage volumes | **none — it gets no `/srv` mount at all** (§13.5) |

No remote-path mapping is needed — every container sees the identical tree.

Two behaviours to set deliberately, both from §14.2:

- **Don't auto-delete the download on import** (`import-and-keep`). A seeding
  torrent holds the second hardlink; deleting the library link early doesn't free
  the SSD anyway, and force-deleting a seeding torrent's data is worse.
- **Per-category disk-space limits** in qBittorrent and SABnzbd, so a large pack
  can't start without room and spill onto the cold HDD.

### The mergerfs catch, and why it matters here

`/srv/video` is a mergerfs union of `ssd-hot` (7.3T ext4) and `hdd-cold` (27.3T
xfs) with `category.create=ff`, so new files land on the SSD and downloads and
library start life on the same filesystem. Hardlinks work.

**A hardlink cannot span the two branches** — they are separate filesystems on
separate disks. This has nothing to do with ext4 vs xfs and would **not** change if
both were reformatted to match: two ext4 filesystems cannot share hardlinks either.
Do not reach for `mkfs` over this. The moment a movie is demoted to `hdd-cold`, its
hardlink to the still-seeding download breaks and you are paying for two full
copies. Set qBittorrent to remove torrents at a seed ratio or time
limit so the download side is gone before demotion, or expect the hot tier to
fill with orphans.

**Nothing demotes automatically yet.** The nightly mover (§5) and the
promote-on-detail-view daemon (§7) are designed but unbuilt, so the hot SSD only
ever fills. When it drops below `minfreespace=50G`, mergerfs `moveonenospc=true`
starts writing **new downloads straight onto the cold HDD** and nothing brings
them back. Until the mover exists you are the mover:

```bash
# Where is everything, and how close to spilling am I? (does not wake the HDD)
~/Projects/Server/3-Storage-Layout-and-Spindown/tier-report --top 20

# Demote a title once its torrent has stopped seeding (dry run by default)
sudo ~/Projects/Server/3-Storage-Layout-and-Spindown/tier-move demote media/movies/Dune
sudo ~/Projects/Server/3-Storage-Layout-and-Spindown/tier-move demote media/movies/Dune --apply
```

`tier-move` refuses any file that is open or still hardlinked, so it will not break
a seeding torrent by accident.

## Download → library pipeline

What happens after Radarr grabs a release, end to end. Everything below is set
via the apps' APIs (it lives in `/srv/appdata`, not in git) **except** the
quality/naming policy, which is `recyclarr/recyclarr.yml` in this folder.

```
SABnzbd ─ /data/usenet/complete/movies/<job>/ ─┐  Radarr: completed download handling
qBit ──── /data/torrents/movies/<torrent>/ ────┤  (checks every 1 min)
                                               ▼
        usenet: MOVE (atomic rename) · torrent: HARDLINK (qBit keeps seeding)
                                               ▼
  /data/media/movies/<Title> (<Year>) [tmdbid-<id>]/<Title> (<Year>) [tmdbid-<id>] - [<CF>][<Quality>][<Audio>][<HDR>][<Codec>]-<Group>.mkv
                    ┌──────────────────────────┴───────────────────────────┐
     Radarr Connect "Jellyfin" (Emby/Jellyfin type)       Radarr SignalR feed -> Bazarr
     on import/upgrade/rename/delete:                      (movie updated event, search NOT
     POST /Library/Media/Updated, /data/media/ -> /media/   deferred) -> immediate subtitle search
     + Jellyfin real-time monitor (inotify)
                    ▼                                                      ▼
     Jellyfin refreshes that folder: TMDB metadata,       <name>.en.srt / .de.srt /
     poster/backdrop, ffprobe media info, trickplay        .en.forced.srt next to the file
     (during the scan, while the file is still on SSD)     (Jellyfin real-time monitor sees them)
```

### Paths and hardlinks

| | host | container |
|---|---|---|
| SABnzbd complete (category `movies`, folder `movies`) | `/srv/video/usenet/complete/movies` | `/data/usenet/complete/movies` |
| qBittorrent category `radarr` save path | `/srv/video/torrents/movies` | `/data/torrents/movies` |
| Radarr root folder | `/srv/video/media/movies` | `/data/media/movies` |
| Jellyfin library `Movies` | `/srv/video/media/movies` | `/media/movies` (`:ro`) |

- One `/data` mount in qBit, SAB, Radarr and Bazarr → same filesystem as far as
  Radarr can tell, so **Use Hardlinks instead of Copy** works and no remote-path
  mapping exists. Jellyfin is the only one with a different view, hence the path
  mapping in the Jellyfin Connect entry.
- mergerfs (`category.create=ff`, non-path-preserving): `link()`/`rename()` are
  done on the branch the *source* file is on and the target directory is cloned
  there if missing. Downloads land on `ssd-hot`, so the library copy is created on
  `ssd-hot` too — even for a movie whose older version was demoted to `hdd-cold`.
  A hardlink can therefore only fail if the download itself spilled to the HDD
  (`minfreespace=50G`); Radarr then falls back to a copy, which is correct.
- Verified 2026-10-19 through the Radarr container: `ln /data/torrents/movies/x
  /data/media/movies/x` → same inode, link count 2, both on `ssd-hot`.
  (`stat` *through mergerfs* can show a stale link count for ~1 s; check the
  branch, `/srv/.disks/ssd-hot/...`, when in doubt.)
- Usenet is always moved (nothing seeds). Torrents are hardlinked; qBit stops
  them at ratio 2 or 30 days, then Radarr's "Remove Completed" deletes the torrent
  and its download-side link — the library link is untouched.
- Identity: every container runs as `1000:1000`, `UMASK=002`; folders are
  `2775` setgid `buntu`. Radarr's own "Set Permissions" is off (not needed).

### Radarr settings that matter here

| Setting | Value | Why |
|---|---|---|
| Completed Download Handling | on, check every 1 min | the import trigger |
| Remove Completed (SAB + qBit) | on | SAB history/scratch is cleaned; torrents only after seeding stops |
| Use Hardlinks instead of Copy | on | |
| Import Extra Files | on, `srt,ass,ssa,sub,idx` | release-supplied subs are kept and renamed `<movie>.en.srt` |
| Rename Movies | on (Recyclarr) | |
| Propers and Repacks | Do not Prefer (Recyclarr) | the Repack/Proper custom formats score them instead |
| Rescan Movie Folder after Refresh | **After Manual Refresh** | the scheduled refresh would `stat` cold files and wake the HDD (storage doc §13.6) |
| Recycle Bin | `/data/.recyclebin`, 7 days | |
| Metadata (Kodi/Emby NFO) | **all off** | Jellyfin fetches its own from TMDB; two writers = duplicate/conflicting artwork |
| Connect | `Jellyfin` (Emby/Jellyfin) | see below |

### Naming (TRaSH "Jellyfin", TMDB flavour)

```
Folder: {Movie CleanTitle} ({Release Year}) [tmdbid-{TmdbId}]
File:   {Movie CleanTitle} {(Release Year)} [tmdbid-{TmdbId}] - {{Edition Tags}} {[MediaInfo 3D]}{[Custom Formats]}{[Quality Full]}{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}
```

Recyclarr keys `jellyfin-tmdb` (folder + file). TMDB rather than IMDb because
Radarr is TMDB-native: every movie has a TMDB id, not every one has an IMDb id.
Jellyfin matches on `[tmdbid-…]`, so identification never guesses.
Real example (the test import): `Meridian (2016) [tmdbid-417407]/Meridian (2016)
[tmdbid-417407] - [WEBRip-2160p][EAC3 5.1][PQ][HEVC]-OpenContent.mkv`. `[PQ]` rather
than `[HDR10]` because that encode carries no mastering-display metadata; normal
HDR10 releases show `[HDR10]`, DV ones `[DV HDR10]`.

### Quality profiles — Recyclarr (TRaSH Guides)

`movie-recyclarr` (`ghcr.io/recyclarr/recyclarr`, pinned) syncs TRaSH custom
formats, scores, quality profiles, quality sizes and naming into Radarr every
night at `RECYCLARR_CRON` (04:15, inside the nightly window beefy is woken for).
**Edit `recyclarr/recyclarr.yml`, not the Radarr UI** — Recyclarr overwrites what
it manages. API key: `RADARR_API_KEY` in `.env`.

```bash
docker compose run --rm recyclarr sync --preview   # what would change
docker compose run --rm recyclarr sync             # apply now
docker compose run --rm recyclarr config list templates   # TRaSH templates
```

| Profile | Use | Qualities (top wins) | Upgrade until |
|---|---|---|---|
| **4K HDR - UHD Bluray + WEB (1080p fallback)** — default | everything | Bluray-2160p › WEB-2160p › Bluray-1080p › WEB-1080p | Bluray-2160p, CF score 10000 |
| HD Bluray + WEB | space saver, pick per movie | Bluray-1080p › WEB-1080p › Bluray-720p | Bluray-1080p |

Why this shape:

- **TRaSH "UHD Bluray + WEB"** = high-quality 4K *encodes* (20–60 GB), not remuxes
  (50–90 GB). With ~34 TB free that is roughly 1 000 movies; remuxes would halve it
  for a difference few screens show.
- **1080p fallback in the same profile** is TRaSH's documented "combine profiles"
  option: a movie with no 4K release still gets 1080p now and is upgraded to 4K
  automatically when one appears. Radarr always ranks **quality before score**, so
  any acceptable 2160p beats any 1080p. The HD Bluray tier scores are added so the
  fallback picks a good 1080p group.
- **HDR**: `SDR` (−10000) rejects 2160p releases without HDR (usually upscales), so
  "4K" here always means 4K HDR; without an HDR 4K the 1080p tier wins.
  `DV (w/o HDR fallback)` (−10000) rejects Dolby Vision Profile 5 (no HDR10 base
  layer → green/purple on non-DV clients, and Jellyfin's tone mapping of P5 is
  untested here). DV *with* HDR10 fallback (P7/P8, "DV HDR10") scores like HDR10.
  DV Boost / HDR10+ Boost are **not** enabled — turn on `DV Boost` if every TV in
  the house does Dolby Vision.
- **Golden Rule**: x265 only for HDR/4K (`x265 (no HDR/DV)` −10000); 1080p stays x264,
  which direct-plays everywhere.
- **Unwanted**: BR-DISK, LQ groups, 3D, AV1, upscaled, extras, generated HDR, … −10000.
- **Language**: profile language `Original` (TRaSH default). English audio for an
  English film; a German-DL release (German + original) is accepted, a German-only dub
  is not. German comes from subtitles (Bazarr).
- The six stock Radarr profiles (Any, SD, HD-720p, …) are unused and can be deleted
  in the UI. The new profile sorts first, so it is the default in the Add dialog.

### Subtitles (Bazarr)

- Radarr connection `radarr:7878`; the **SignalR live feed** is the trigger: on
  import Radarr pushes a movie event, Bazarr re-syncs that movie and searches at
  once (`defer_search_signalr: false`, the default). Measured: search started in the
  same second as the import.
- **No Radarr → Bazarr webhook, on purpose.** Tested 2026-10-19: Radarr fires it in
  the same instant as the import, before Bazarr knows the new file id, so Bazarr
  answers 200 "nothing to do" and never searches (twice in a row). SignalR does not
  have that race.
- Language profile **English + German** (en, de, en forced, de forced), applied
  automatically to every new movie (`movie_default_profile`).
- Files land next to the video: `<movie>.en.srt`, `<movie>.de.srt`,
  `<movie>.en.forced.srt` — the names Jellyfin understands.
- Embedded subtitle tracks count as present (`use_embedded_subs`), so a remux/encode
  that already carries en/de subs triggers no download.
- Providers: **YIFY Subtitles** (no account). The good ones need a free account →
  see "To do" below: **OpenSubtitles.com** (username/password), **SubDL** and
  **SubSource** (API keys).
- Missing-subs search every 24 h (was 6 h): providers that hash the file read it,
  which would wake the HDD for demoted movies.

### Jellyfin

- Radarr Connect **Emby / Jellyfin** works with Jellyfin 12: Radarr 6 sends the
  `Authorization: MediaBrowser Token=…` header (the legacy `X-MediaBrowser-Token`
  / `api_key` forms are rejected by this server). Host `192.168.1.102:8096`,
  path mapping `/data/media/` → `/media/`, "Update Library" on, notifications off.
- Real-time monitoring stays on as a second trigger (picks up Bazarr's subtitles;
  measured: the new `.en.srt` showed up in Jellyfin about 60 s later, which is
  `LibraryMonitorDelay`).
- ⚠️ **Gotcha — an empty library is never watched.** Until the first full scan put
  an item in it, Jellyfin did not start the watcher for `/media/movies` (no
  "Watching directory" in the log), and `/Library/Media/Updated` from Radarr was
  accepted (204) and silently ignored. One manual *Scan All Libraries* fixed it for
  good. If the library is ever emptied and rebuilt: scan once by hand, then check
  `grep "Watching directory" /srv/appdata/jellyfin/log/log_*.log`.
- Upgrades get a new Jellyfin item id (the file name changes), so per-user watch
  state of the *old* file is not carried over. Jellyfin behaviour, not a bug here.
- Metadata/artwork: Jellyfin's own TMDB + OMDb fetchers, saved in `/config`
  (media is read-only). Radarr writes no NFO/images.
- HDD-friendly schedule (storage doc §13.10): trickplay is extracted during the
  scan, i.e. at import while the file is on SSD. The periodic tasks moved into the
  04:00–06:00 window: Scan Media Library daily 04:10 (was every 12 h), Trickplay
  04:25, Chapter Images 04:40 (chapter extraction is off anyway), Media Segment
  Scan 04:50 (was every 12 h).

### Test the pipeline (no download client involved)

Uses an openly licensed film so nothing copyrighted is ever fetched.
Netflix Open Content **Meridian (2016)**, CC BY 4.0, TMDB 417407.

```bash
# 1. a "release" in SAB's completed folder
R=Meridian.2016.2160p.WEBRip.DDP5.1.HDR10.HEVC-OpenContent
mkdir -p /srv/video/usenet/complete/movies/$R && cp <file>.mkv /srv/video/usenet/complete/movies/$R/$R.mkv
# 2. add the movie (TMDB id) with the default profile, no search
# 3. import it exactly like completed download handling would:
curl -s -X POST -H "X-Api-Key: $RK" -H 'Content-Type: application/json' \
  http://127.0.0.1:7878/api/v3/command \
  -d "{\"name\":\"DownloadedMoviesScan\",\"path\":\"/data/usenet/complete/movies/$R\",\"importMode\":\"Move\"}"
```

Then check: the folder/file name under `/srv/video/media/movies`, the inode
(`stat`), `docker logs jellyfin | grep -i Library/Media/Updated` (or the item via
`/Items?searchTerm=…`), Bazarr's history, and a transcode
(`/Videos/<id>/master.m3u8?...&VideoBitrate=8000000&MaxHeight=1080`) in
`/srv/appdata/jellyfin/log/FFmpeg.Transcode-*.log`.

To check the *scoring* of a real title without downloading it: add the movie
**unmonitored** with "Start search" off, then
`GET /api/v3/release?movieId=<id>` (interactive search; it never grabs).

### Verified 2026-10-19

Test file: Netflix Open Content *Meridian* (CC BY 4.0). Netflix publishes no
ready-made HDR10 encode of it (only an untagged 8-bit H.264 preview and ≥100 GB
mezzanines), so the preview + the official 5.1 PCM track were re-encoded locally
with QSV to HEVC Main10, BT.2020/PQ, E-AC3 5.1, 1.4 GB — a legal derivative.

| Step | Result |
|---|---|
| Import (`DownloadedMoviesScan`, Move) | accepted as WEBRip-2160p, release folder deleted afterwards |
| Move, not copy | same inode before/after (`241369098`), link count 1, on `ssd-hot` |
| Name | `Meridian (2016) [tmdbid-417407]/Meridian (2016) [tmdbid-417407] - [WEBRip-2160p][EAC3 5.1][PQ][HEVC]-OpenContent.mkv` |
| Permissions | file `-rw-rw-r--`, folder `drwxrwsr-x`, `buntu:buntu` |
| Upgrade path | PROPER/REPACK copies imported as upgrades (CF score 2250→2255→…), old file to `.recyclebin` |
| Radarr → Jellyfin | `POST /Library/Media/Updated` → 204 on every import; item refreshed ~60 s later (after the empty-library gotcha above) |
| Jellyfin item | TMDB + IMDb ids, overview, poster/logo/thumb, 1 backdrop, genres, PG; video HEVC Main 10 3840×2160 `HDR10` bt2020/smpte2084, E-AC3 6 ch |
| Trickplay | 72 tiles at 320 px, created during the refresh in ~3 s (hardware, key frames only) |
| Bazarr | SignalR event → search at import; YIFY: 404 "No subtitles found" (no provider has this short). Official English CC subtitles (EBU STL from Netflix) converted to SRT and placed as `<movie>.en.srt`; Bazarr indexed it, Jellyfin shows it as external `subrip` `eng` |
| Transcode | HLS 1080p/8 Mbps: VA-API decode → `scale_vaapi` → `tonemap_vaapi` (HDR10→BT.709) → `h264_qsv -low_power 1`, 119 fps (2× real time for 4K60); stopped via `DELETE /Videos/ActiveEncodings` |

## Network topology

```
            ┌─ gluetun (ProtonVPN) ─────────────┐
 LAN :8080 ─┤  qBittorrent    Prowlarr          │   no leak if tunnel drops
 LAN :9696 ─┤  (no ports/networks of their own) │
            └───────────┬───────────────────────┘
                        │ movie-downloads bridge, 172.28.10.0/24
       ┌────────────────┼────────────────┐
   SABnzbd :8081    Radarr :7878    Bazarr :6767
```

qBittorrent and Prowlarr are inside the tunnel because they talk to trackers and
peers. SABnzbd is outside it on purpose: usenet is already TLS, it never seeds,
and a VPN hiccup cannot stall a download. `FIREWALL_OUTBOUND_SUBNETS` in `.env`
**must** list the bridge subnet, or gluetun silently drops the arr apps' replies
and Radarr cannot see qBittorrent at all.

Port forwarding uses gluetun's own `VPN_PORT_FORWARDING_UP_COMMAND` hook
(`scripts/qbit-port.sh`). That is why this stack has **no port-updater container
and no docker.sock**, unlike the older `mouse` stack on fastpi.

⚠️ **Enable qBittorrent → Options → WebUI → "Bypass authentication for clients on
localhost"**, or the hook gets a 403 and the forwarded port is never applied —
which looks exactly like a dead tracker.

## Auth: Authentik is the only login

Every UI in this stack sits behind Authentik forward auth on fastpi's Traefik, and the
apps' own logins are turned off — Radarr/Prowlarr auth = `External`, Bazarr = `None`,
SABnzbd username/password empty, qBittorrent bypassed for the proxy subnet. Full
setup, including the Authentik objects that must be created by hand, is in
`HomeLab-FastPi` → `Docker/Traefik/docs/2026-10-06-authentik-sso.md`.

⚠️ **The host ports below are an unauthenticated bypass of all that.** Traefik reaches
these apps at `192.168.1.102:<port>`, so the publications cannot simply be removed —
without them every route breaks. Close the LAN bypass with ufw instead, allowing only
fastpi:

```bash
sudo ufw allow from 192.168.1.2 to any port 7878,9696,6767,8080,8081,8096 proto tcp
sudo ufw deny  to any port 7878,9696,6767,8080,8081,8096 proto tcp
```

Until that is in place, anyone on the LAN can reach every one of these apps with no
login at all.

## Known trade-off: seeding on a host that sleeps

beefy powers itself off after 15 idle minutes. An **active** transfer keeps it
awake (net/disk exceed the idle thresholds) so downloads always finish, and
because downloads are local there is no "mount to a sleeping host" problem. But
**idle seeding reads as idle**, so beefy sleeps and seeding stops.

This stack is therefore effectively a hit-and-run client. That is fine on public
trackers and will get you banned on a private one. Private-tracker content stays
on fastpi's always-on `mouse` stack — that split is deliberate, not an oversight.

## Related

- `../Jellyfin` — playback, reads `/srv/video/media` read-only
- `../../Server/3-Storage-Layout-and-Spindown.md` — mergerfs tiers, demotion
- `../../Server/8-Idle-Watcher.md` — what counts as idle, and `beefy-keep-awake`
