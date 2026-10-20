# Movie-Downloads — VPN, torrents, usenet, Radarr and Sonarr on beefy

Everything needed to acquire a movie or a series and file it into the library:
**gluetun** (ProtonVPN) wrapping **qBittorrent** and **Prowlarr**, plus **SABnzbd**,
**Radarr**, **Sonarr** and **Bazarr** outside the tunnel. The folder keeps its
original name although it handles series too (since 2026-10-19); requests come in
via `../Seerr`. Playback is a separate project — see
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
| Sonarr | `http://192.168.1.102:8989` | itself |
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
sudo mkdir -p /srv/video/torrents/{movies,tv} \
             /srv/video/usenet/incomplete \
             /srv/video/usenet/complete/{movies,tv} \
             /srv/video/media/{movies,tv} \
             /srv/video/.recyclebin \
             /srv/appdata/{gluetun,qbittorrent,prowlarr,sabnzbd,radarr,sonarr,bazarr,recyclarr}
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
├── torrents/tv/                qBittorrent save path (sonarr category)
├── usenet/
│   ├── incomplete/             SABnzbd scratch + unpack
│   └── complete/{movies,tv}/   SABnzbd finished, pre-import (categories movies, tv)
├── media/movies/               <- Radarr root folder; Jellyfin reads this :ro
├── media/tv/                   <- Sonarr root folder; Jellyfin reads this :ro
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
| Sonarr | the same, with category `sonarr` / `tv`, root `/data/media/tv` | see [Series (Sonarr)](#series-sonarr) |
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
  SABnzbd "Permissions for completed downloads" = `775` (since 2026-10-20: files unpacked from RARs kept the archive's `644`).
- SABnzbd unwanted extensions (since 2026-10-20): `exe bat cmd com scr pif lnk msi msp ps1 vbs vbe js jse jar hta wsf wsh cpl reg dll apk` → job aborted (fake-video malware releases), on top of the ClamAV scan.

### Indexers (Prowlarr)

All Newznab, priority 25, synced by Prowlarr to Radarr (movie categories 2000-2090) and Sonarr (TV 5000-5090, anime 5070).

| Indexer | Base URL | App profile | Limits | Added by |
|---|---|---|---|---|
| NZBgeek | `https://api.nzbgeek.info` | Standard (RSS + search) | none | UI |
| NzbPlanet | `https://api.nzbplanet.net` | Standard (RSS + search) | none | UI |
| Treasure Maps | `https://treasure-maps.com` | **Search only** (no RSS) | free tier: 100 API calls + 5 downloads per rolling 24 h, set as Prowlarr query/grab limit; API trial until ~2026-11-19 | `scripts/treasure-maps.sh` |

- Treasure Maps is the German source: tags German releases with the standard category too (`2040` + `Movies - DE/HD`), so the normal sync categories find them. Knives Out 2019: 31 `German.DL` releases, the other two indexers 0.
- RSS off because Radarr (every 30 min) + Sonarr (every 15 min) would use ~144 calls/day. Search costs ~3 calls per movie search.
- `scripts/treasure-maps.sh` reads the key from `secrets/treasure-maps.apikey` (gitignored, 600) on every run, enables the indexer and is idempotent. New key: replace the file, re-run the script. A rejected key makes it exit 1 and change nothing.
- **Disabled since 2026-10-19:** the first account was blocked (duplicate after the site migration). Re-enable by running the script with the new account's key.

### Wrong-film guard

Found 2026-10-20: Treasure Maps answers an ID search (`imdbid`/`tmdbid`) with every title match and stamps the *searched* IDs on all of them. Radarr trusts indexer IDs over title + year, so a search for *Night of the Living Dead* (1968) grabbed the 1990 remake twice (NZBgeek/NzbPlanet return only the right film).

| | |
|---|---|
| Fix | Radarr Connect → Custom Script **Wrong-film guard**, On Grab: `scripts/grab-guard.sh` parses the release year (`/api/v3/parse`); more than 2 years off the movie's year and secondary year → queue item removed from SABnzbd/qBit, blocklisted, Radarr searches again |
| Kept | no year in the name, ±2 years (premiere vs release year: *Plan 9 from Outer Space* is 1957 on TMDB, 1959 in release names) |
| Cost | each wrong grab uses one of Treasure Maps' 5 grabs/day and a few seconds of download |
| Not covered | Sonarr (series names rarely carry a year); a remake with the same title *and* year |
| Registered by | `scripts/language-policy.sh` |
| Tests | `tests/test_grab_guard.sh`; live: `sudo docker exec movie-radarr env radarr_eventtype=Grab radarr_movie_id=<id> radarr_release_title=<name> radarr_download_id=x GG_WAIT=0 bash /scripts/grab-guard.sh` |

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
| **4K HDR - UHD Bluray + WEB (1080p fallback)** — default | everything | Bluray-2160p › WEB-2160p › Bluray-1080p › WEB-1080p | Bluray-2160p, CF score 20000 |
| 4K HDR - … (1080p fallback) - English original | English-original titles, set by `scripts/language-policy.sh` | same | same; `Language: Original + English` 0 (see [Languages](#languages)) |
| Any quality - old & rare (upgrades to 4K) (+ `- English original` twin) | old/niche titles, set automatically on add ([Old and rare titles](#old-and-rare-titles)) or picked per request | Bluray-2160p › WEB-2160p › Bluray-1080p › WEB-1080p › HDTV-1080p › Bluray-720p › WEB-720p › HDTV-720p › Bluray-576p › Bluray-480p › WEB-480p › DVD › SDTV | Bluray-2160p, 20000; scores identical to the default |
| HD Bluray + WEB | space saver, pick per movie | Bluray-1080p › WEB-1080p › Bluray-720p | Bluray-1080p, 20000 |
| Anime - Remux + WEB 1080p | anime films, pick per movie (Seerr has no anime profile for movies) | Remux/Bluray-1080p › WEB-1080p › 720p … | Remux-1080p, 20000; TRaSH `[Anime] Remux-1080p`: anime group tiers, no Golden Rule (anime is x265 10-bit by default), minimum score 100 |

Why this shape:

- **TRaSH "UHD Bluray + WEB"** = high-quality 4K *encodes* (20–60 GB), not remuxes
  (50–90 GB). With ~34 TB free that is roughly 1 000 movies; remuxes would halve it
  for a difference few screens show.
- **1080p fallback in the same profile** is TRaSH's documented "combine profiles"
  option: a movie with no 4K release still gets 1080p now and is upgraded to 4K
  automatically when one appears. Radarr always ranks **quality before score**, so
  any acceptable 2160p beats any 1080p. The HD Bluray tier scores are added so the
  fallback picks a good 1080p group.
- **HDR**: `SDR` (−50000) rejects 2160p releases without HDR (usually upscales), so
  "4K" here always means 4K HDR; without an HDR 4K the 1080p tier wins.
  `DV (w/o HDR fallback)` (−50000) rejects Dolby Vision Profile 5 (no HDR10 base
  layer → green/purple on non-DV clients, and Jellyfin's tone mapping of P5 is
  untested here). DV *with* HDR10 fallback (P7/P8, "DV HDR10") scores like HDR10.
  DV Boost / HDR10+ Boost are **not** enabled — turn on `DV Boost` if every TV in
  the house does Dolby Vision.
- **Golden Rule**: x265 only for HDR/4K (`x265 (no HDR/DV)` −50000); 1080p stays x264,
  which direct-plays everywhere. Not applied to the anime profile.
- **Unwanted**: BR-DISK, LQ groups, 3D, AV1, upscaled, extras, generated HDR, … −50000.
- **Languages**: see [Languages](#languages) — the same scores in every profile.
- **Old and rare titles**<a id="old-and-rare-titles"></a>: the 4K profiles allow nothing below 1080p, so a film that only exists on DVD (e.g. *The Ring Thing*, 2004) is rejected. `Any quality - old & rare (upgrades to 4K)` (Radarr and Sonarr) has every custom format and score of the default (languages, rejects, HDR rules, Golden Rule), but allows SDTV/DVD up to 2160p and upgrades until Bluray-2160p / WEB 2160p. Not allowed: CAM/TS/screener, DVD-R/BR-DISK images, remuxes, Raw-HD. Its twin `… - English original` has `Language: Original + English` at 0, like the 4K twin.
  - **Automatic on add** (`language-policy-hook.sh`), only for titles added on the default 4K profile or its twin; any other profile picked in Seerr stays:

    | Rule | Movies (Radarr) | Series (Sonarr) |
    |---|---|---|
    | old | `year` ≤ current year − 12 | `year` (first aired) ≤ current year − 12 |
    | niche | ≥ 2 years old and TMDB votes < 100 (none = 0) | ≥ 2 years old and votes < 1000 (Sonarr's rating, IMDb scale) |
    | unknown year (0) | stays | stays |

  - Why: Radarr ranks quality first, so on Any quality an old title still gets the best release that exists (4K if there is one) and keeps upgrading; the only difference is that a DVD-only title is grabbed instead of rejected. ≤ 2014 is before UHD Bluray (2016) and 4K streaming; titles that old with no 1080p are exactly the DVD-only case. Votes catch obscure newer titles (*The Ring Thing*: 19 TMDB votes, popular films have thousands); the 2-year gate keeps brand-new films (few votes yet, early 480p/720p WEB rips) on the 4K profile.
  - English originals go to the Any twin, the rest to Any; `language-policy.sh` swaps existing titles between Any and its twin like the 4K pair (no age rule for existing titles).
  - Verified 2026-10-20: *Nosferatu* (1922, German) added on the default -> `movie 9: profile 7 -> 11 (old: 1922)` in the debug log.
  - Golden Rule applies: an x265 SD/HD encode without HDR is rejected (−50000), as in the 1080p tier.
  - Verified 2026-10-20: *The Ring Thing* (TMDB 10248) on this profile grabbed `The.Ring.Thing.Das.Ding.mit.dem.Ring.German.2004.AC3.DVDRiP.x264-IND` (DVD, score 20750, 0.96 GB, SABnzbd); the DVD9 image was rejected (DVD-R not wanted).
- The six stock Radarr profiles (Any, SD, HD-720p, …) cannot be disabled (the
  API has no such flag), so they are renamed with a `zz ` prefix to sort last;
  Recyclarr does not touch them. Delete them in the UI if they are in the way.

### Upgrades

| | |
|---|---|
| Profiles | every Recyclarr profile `upgradeAllowed: true`; cutoff = best quality (Radarr Bluray-2160p, Sonarr WEB 2160p) + score 20000 |
| RSS | Radarr every 30 min, Sonarr every 15 min, NZBgeek + NzbPlanet (Treasure Maps is search-only). Runs only while beefy is awake: new releases that appear while it sleeps are missed by RSS |
| Weekly search | `systemd/cutoff-search.timer`, Sunday 04:12 (inside the 03:55 wake): `scripts/cutoff-search.sh` searches the 10 least recently searched cutoff-unmet movies (`MoviesSearch`) and 10 episodes (`EpisodeSearch`) |
| Indexer budget | ~3 Treasure Maps calls per title -> ≤ ~60 of its 100/day; Prowlarr's query limit stops the rest. Radarr's own `CutoffUnmetMoviesSearch` has no cap, hence the script |

```bash
cd ~/Projects/Docker/Movie-Downloads/systemd
sudo install -m 0644 cutoff-search.service cutoff-search.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now cutoff-search.timer
sudo systemctl start cutoff-search.service; journalctl -u cutoff-search -n 5   # run now
```

- More or fewer per run: `Environment=CS_MOVIES=… CS_EPISODES=…` in the service (0 = skip).
- Tests: `tests/test_cutoff_search.sh`. Verified 2026-10-20: `radarr: 2 of 2 cutoff-unmet searched: Bad Apples, The Ring Thing` -> Radarr `Completed search for 2 movies. 0 reports downloaded.`

### Languages

Policy (2026-10-19): audio = the original language plus German plus English; if
that does not exist, drop one (German first choice, English second); never a dub
without the original track. Subtitles = English + German + the original language.
Lives in three places: `recyclarr/recyclarr.yml` (scores), `scripts/language-policy.sh`
(what Recyclarr cannot express: one native custom format, tags, Bazarr profiles),
`../Seerr/scripts/configure.sh` (anime profile).

| Rung | Audio | Score | How it is detected |
|---|---|---|---|
| 1 | original + German + English | 28000 | `German DL` + `Language: Original + English` |
| 2 | original + German | 20000 | TRaSH `German DL` (German + original audio), `German DL (undefined)` |
| 3 | original + English (non-English title) | 8000 | `Language: Original + English` (own CF: English, the original, and a language other than the original all present); anime also `Anime Dual Audio` 8000 (title says "Dual Audio"); 0 in the English-original profiles |
| 4 | original only | 0 | untagged releases count as the original language |
| rejected | dub only (`German`, `Dubs Only`), no original track (`Language: Not Original`), every unwanted format | −50000 | Radarr additionally keeps profile language `Original` (rejects a release without the original audio, source-checked in Radarr 6: a multi-language release passes if the original is among its languages); Sonarr has no profile language, the CF does it |

- Rungs are ≥ 8000 apart and the audio + HDR + group-tier sum of a release is
  ≤ ~7200, so language always wins over quality *within* the same quality tier
  (quality itself still ranks first: any allowed 2160p beats any 1080p). Rejects are
  −50000 rather than TRaSH's −10000 so the best possible sum (28000 + 7200) stays
  below the minimum score of 0.
- English originals: a custom format cannot see that the original *is* English, so `Language: Original + English` fires on any extra dub (English + Portuguese got 8000). English-original titles therefore use the twin profiles `… - English original` (Radarr and Sonarr, 4K default and Any quality), identical except that CF at 0: English + German 20000 › English (+ any dub) 0.
  - `scripts/language-policy.sh` moves English originals on the main 4K profile to the twin and others back (existing titles).
  - Titles added later (Seerr, Radarr/Sonarr UI, import lists): Connect → Custom Script **Language policy** runs `scripts/language-policy-hook.sh` (mounted at `/scripts:ro` in both containers, registered by `language-policy.sh`) and does the same move for that one title (plus old/niche -> Any quality, see [Old and rare titles](#old-and-rare-titles)), before its first search.

    | App | Trigger | Env | Runs |
    |---|---|---|---|
    | Radarr | On Movie Added | `radarr_eventtype=MovieAdded`, `radarr_movie_id` | inside the add (`POST /movie`), before the refresh that queues the search |
    | Sonarr | On Series Add | `sonarr_eventtype=SeriesAdd`, `sonarr_series_id` | after the first refresh, right before the search is queued |

    Both apps run the script synchronously, so there is no race: the search always sees the new profile, no re-search needed. A failed hook (exit ≠ 0, e.g. API unreachable) leaves the title on the profile it was added with; the apps ignore the exit code outside the Test button, so it shows only in the debug log (`Executed external script: … Status: 1`); `language-policy.sh` fixes it.
  - Check: add a film in Seerr or Radarr, open its page: an English original shows `… - English original` within seconds; log: `grep language-policy-hook /srv/appdata/radarr/logs/radarr.debug.txt`.
  - Tests: `tests/test_language_policy_hook.sh` (fake API, needs bash, curl, jq, python3).
- No German release for a title usually means the indexers do not carry one, not a filter: check with Prowlarr's search first.
- 2160p German scene releases often omit `HDR` in the name (`GERMAN.DL.2160P.UHD.BLURAY.HEVC-UNDERTAKERS`): `SDR` (2160p without an HDR marker) rejects them (Radarr parse of such a Knives Out title: −22000).
- Upgrade until score 20000 = German dual audio reached. A release that also has
  English wins at grab time but is not chased afterwards.
- The TRaSH German guide uses profile language `Any` and accepts `German` dub-only
  (+10000); this setup deliberately does not.
- Anime: Seerr sends everything TMDB calls anime to the Sonarr profile
  **Anime - Bluray + WEB 1080p** with series type `anime`, same root folder `/data/media/tv`
  (one Jellyfin **Shows** library; a separate anime root would mean a second library
  for no gain). TRaSH `[Anime] Remux-1080p` qualities and group tiers, `[Streaming
  Services] Anime` (CR, HIDIVE, …), Anime Raws / LQ groups / `Dubs Only` rejected,
  TRaSH's minimum score 100 (no-name groups are skipped). Anime films: pick the Radarr
  anime profile by hand.
- Tags: Radarr and Sonarr auto-tagging sets `lang-xx` (every language with `subs = yes` in `scripts/languages.tsv`) from the title's original language on add and on every refresh, except while a `subs-xx` tag is present (see [Per-request audio and subtitles](#per-request-audio-and-subtitles)); Bazarr maps the tag to the matching subtitle profile. English originals carry no tag and keep **English + German**. New language: add a row to `scripts/languages.tsv` and re-run the script.
- Not built: **extra audio tracks by merging two downloads** (download EN+DE and
  EN+ES, copy the Spanish track into the first file). No maintained tool does this
  (Tdarr/Unmanic transcode, they do not add languages); it would be a bespoke
  mkvmerge pipeline with two grabs per title, audio offsets that differ between
  releases (different cuts, intros, frame rates), double disk and usenet use, and
  a file Radarr cannot account for. Multi-audio releases (`German DL`, `MULTi`,
  `Dual Audio`) are the standard way to get the extra lanes, and the scores above
  grab them whenever they exist.

Verified 2026-10-19 (`GET /api/v3/parse?title=…`, never grabs; a title without a
language tag is parsed as the original language):

| Title | Radarr (Knives Out, English) | Sonarr (Money Heist, Spanish) | Sonarr (Frieren, anime, Japanese) |
|---|---|---|---|
| untagged `1080p.NF.WEB-DL` / `CR.WEB-DL` | 0 (English) | 75 (Spanish) | 306 (Japanese, Anime Web Tier 04 + CR) |
| `German.DL` | 28500 (German + English = both rungs) | 20075 | 20000 |
| `German.ML` (German + original + English) | 28500 | **28075** | — |
| `English.Spanish` / `Dual.Audio` | — | 8075 | 8000 |
| `German` only | −99500 (`German`, `Not Original`) | −99925 | −100000 |
| `English` only (non-English title) / `English.Dubbed` | — | −49925 | −100000 (`Dubs Only`) |

English-original profile, Knives Out (2160p Bluray, same encode):

| Title | Main profile | English original |
|---|---|---|
| `German.DL` | 30200 | 22200 |
| `English.Portuguese` | 11950 | 3950 |
| untagged | 3950 | 3950 |
| `German` only | −97800 | −97800 |

Bazarr after the sync: Pioneer One (English) → profile `English + German`; Money
Heist (Spanish, tag `lang-es`) → `English + German + Spanish`; Frieren (Japanese,
`lang-ja`) → `English + German + Japanese`. Money Heist and Frieren are in Sonarr
unmonitored, nothing was grabbed.

### Per-request audio and subtitles

Tags picked in Seerr (request -> Advanced -> Tags) or set in Radarr/Sonarr. Defined in `scripts/languages.tsv`, created by `scripts/language-policy.sh`.

| Tag | Meaning | How |
|---|---|---|
| `audio-xx` | the release **must** contain that audio language; not grabbed otherwise. Normal ranking on top | tag-scoped Release Profile `audio-xx: <Language> audio required`, required term = the regex in `languages.tsv`, matched against the release name |
| `subs-xx` | Bazarr also fetches subtitles in that language (on top of EN + DE + original) | hook adds `bz-<original>-<requested…>` and creates the Bazarr profile `English + German + <Original> + <Requested…> (requested)` bound to it |
| `lang-xx` | set automatically (original language), do not pick | auto-tagging |
| `bz-…` | set by the hook, do not pick | one per subtitle combination |

| Code | Language | `audio-` | `subs-` |
|---|---|---|---|
| `gsw` | Swiss German | ✓ (name match, see below) | – (no providers carry it; `de` is always fetched) |
| `de`, `en` | German, English | ✓ | – (always fetched) |
| `fr` `it` `es` `ja` `ko` `pt` `nl` `ru` `sv` `da` `nb` `zh` `pl` `tr` | | ✓ | ✓ |
| `fi` `cs` `hu` `el` `ar` `he` `uk` | | – | ✓ |

- Several `audio-` tags = all of them required (each release profile must match). Several `subs-` tags = one combined Bazarr profile.
- `audio-<original>` is dropped on add: the original audio is required anyway, and release names of e.g. English films rarely say `English`.
- Limits of name matching (release profiles see only the name, not the tracks):
  - **Swiss German** has no Radarr/Sonarr language. Real names (NZBgeek/NzbPlanet search, 2026-10-20): `Swissgerman`, `SwissGerman`, `SWISSGERMAN`, `Swiss.German`, `Schweizerdeutsch`, `Schweizerduetsch`. `Mundart`, `Dialekt`, `CH-DE`, plain `Swiss` are noise and not matched. Nearly only Swiss productions are released that way; Radarr parses them as German.
  - `audio-en` also accepts `Dual Audio`; `audio-fr` accepts `VFF/VFQ/VF2/TRUEFRENCH` but not plain `MULTi` (it does not say which languages) and not `VOSTFR` (French subtitles).
  - A language word in the film title (*The French Connection*) satisfies the term.
- Tags added to an existing title later: re-run `scripts/language-policy.sh` (runs the hook with `LP_TAGS_ONLY=1` for every title with a `subs-` tag, then rescans those titles in Bazarr: a profile changed by the sync keeps its old missing-subtitles list until then).
- The hook reads the Bazarr key from `secrets/hook/bazarr.apikey` (gitignored, written by the script, mounted at `/secrets:ro`) and reaches `http://bazarr:6767`. Rebuild: `mkdir -p secrets/hook` before the first `docker compose up` (otherwise Docker creates it root-owned), then run the script.
- Tests: `tests/test_language_policy_hook.sh` (hook), `python3 tests/test_audio_terms.py` (patterns against real names).

### Subtitles (Bazarr)

- Radarr connection `radarr:7878`; the **SignalR live feed** is the trigger: on
  import Radarr pushes a movie event, Bazarr re-syncs that movie and searches at
  once (`defer_search_signalr: false`, the default). Measured: search started in the
  same second as the import.
- **No Radarr → Bazarr webhook, on purpose.** Tested 2026-10-19: Radarr fires it in
  the same instant as the import, before Bazarr knows the new file id, so Bazarr
  answers 200 "nothing to do" and never searches (twice in a row). SignalR does not
  have that race.
- Language profiles: **English + German** (en, de, en forced, de forced) is the
  default for every new movie and series (`movie_default_profile`,
  `serie_default_profile`); **English + German + <Language>** (tag `lang-xx`) is
  chosen automatically from the Radarr/Sonarr tag (`movie_tag_enabled`,
  `serie_tag_enabled`), see [Languages](#languages). All of it is set by
  `scripts/language-policy.sh`.
- Files land next to the video: `<movie>.en.srt`, `<movie>.de.srt`,
  `<movie>.en.forced.srt` — the names Jellyfin understands.
- Embedded subtitle tracks count as present (`use_embedded_subs`), so a remux/encode
  that already carries en/de subs triggers no download.
- Providers: **YIFY Subtitles** (no account, movies only) and **OpenSubtitles.com**
  (free account, username/password in Bazarr; `use_hash` on, AI-translated and
  machine-translated subtitles **off**). Still possible: **SubDL** and **SubSource**
  (API keys).
- Missing-subs search every 24 h for movies and series (series was still 6 h until 2026-10-20): providers that hash the file read it,
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

### Series (Sonarr)

Added 2026-10-19 (`lscr.io/linuxserver/sonarr:4.0.20`). Same pipeline as the movies,
configured via the API the same way; what differs:

| | Value |
|---|---|
| Root folder | `/data/media/tv`, Jellyfin library **Shows** on `/media/tv` |
| SABnzbd | category `tv` -> `/data/usenet/complete/tv`, priority -100 (same as movies) |
| qBittorrent | category `sonarr` -> `/data/torrents/tv` |
| Prowlarr | application `Sonarr` (`http://sonarr:8989`), fullSync, TV categories 5000-5090 |
| Auth | `External` (Authentik forward auth on `sonarr.holy-grail.ch`, group `streaming-admins`) |
| Media management | hardlinks, recycle bin `/data/.recyclebin` 7 d, extra files `srt,ass,ssa,sub,idx`, rescan after manual refresh, delete empty folders, no metadata writers |
| Connect | `Jellyfin` (Emby/Jellyfin), `/data/media/` -> `/media/`, on import/upgrade/rename/delete |
| Bazarr | Sonarr `sonarr:8989`, SignalR feed (no webhook, same race as Radarr), profile **English + German** by default, `English + German + <Language>` via the `lang-xx` tag ([Languages](#languages)) |

Naming (Recyclarr `jellyfin-tvdb` series folder, TRaSH default season/episode):

```
{Series CleanTitleWithoutYear} {(Series Year)} [tvdbid-{TvdbId}]/Season {season:00}/
{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - {Episode CleanTitle:90} {[Custom Formats]}{[Quality Full]}{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}
```

Quality profiles (Recyclarr, `recyclarr/recyclarr.yml` section `sonarr`):

| Profile | Use | Qualities (top wins) | Why |
|---|---|---|---|
| **4K HDR - WEB-2160p (1080p fallback)** — default | everything | WEB 2160p › WEB 1080p | TRaSH "WEB-2160p (Combined)": the TV counterpart of the movie profile. WEB, not Bluray: series are released on WEB first and TV Bluray encodes are rare. `SDR` and `DV (w/o HDR fallback)` rejected like the movies, Golden Rule UHD (x265 only for HDR) |
| WEB-1080p | space saver, per series | WEB 1080p | plain TRaSH WEB-1080p, Golden Rule HD (x264) |
| Any quality - old & rare (upgrades to 4K) (+ `- English original` twin) | old/niche series, set automatically on add or picked per request | WEB 2160p › Bluray-2160p › WEB 1080p › Bluray-1080p › HDTV-1080p › WEB 720p › Bluray-720p › HDTV-720p › Bluray-576p › WEB 480p › Bluray-480p › DVD › SDTV, upgrade until WEB 2160p | same scores as the default; see the movie [Old and rare titles](#old-and-rare-titles) note |
| Anime - Bluray + WEB 1080p | anime, set by Seerr (series type `anime`) | Bluray-1080p (remux or encode) › WEB 1080p › 720p … | TRaSH `[Anime] Remux-1080p`: anime group tiers, `[Streaming Services] Anime`, Anime Raws / LQ groups / Dubs Only rejected, no Golden Rule, minimum score 100 |

Shared by all: the [Languages](#languages) scores (`German DL` 20000, `Language:
Original + English` 8000, `Language: Not Original` and every reject −50000),
`[Streaming Services] HD/UHD boost` + `General`, `[Unwanted] Unwanted Formats`,
TRaSH series size limits (one table per instance, `series`). The six stock Sonarr
profiles are renamed `zz …` (no disable exists); Recyclarr ignores them.

Verified 2026-10-19 with *Pioneer One* (2010, CC BY-NC-SA, TVDB 170551), the
archive.org `S01E01 ... 720p.x264-VODO` mp4 placed in `/data/usenet/complete/tv/<release>/`:

| Step | Result |
|---|---|
| Add series (unmonitored, no search) | `/data/media/tv/Pioneer One (2010) [tvdbid-170551]` |
| Interactive search (never grabs) | 1080p AMZN WEB-DL scored 1725 accepted; two 720p HDTV releases rejected (not in profile) |
| `DownloadedEpisodesScan`, Move | `Season 01/Pioneer One (2010) - S01E01 - Earthfall [SDTV][AAC 2.0][x264]-VODO.mp4`, release folder removed, file on `ssd-hot` (the archive mp4 is really 640x360, so MediaInfo said SDTV, not the 720p of the name - correct) |
| Jellyfin | `/Library/Media/Updated` sent; the new Shows library needed one full scan first (empty-library gotcha), then TVDB/IMDb/TMDB ids, overview, poster, backdrop, trickplay, and `/media/tv` is watched |
| Bazarr | series synced with profile English + German, 4 missing subtitles listed; the search is a no-op because the only enabled provider (YIFY) is movies-only - add OpenSubtitles.com / SubDL / SubSource (To do) |

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

### Pipeline check

```bash
RK=$(grep -oP '(?<=<ApiKey>)[^<]+' /srv/appdata/radarr/config.xml)
curl -s -H "X-Api-Key: $RK" localhost:7878/api/v3/config/mediamanagement | jq '{copyUsingHardlinks,importExtraFiles}'
curl -s -H "X-Api-Key: $RK" localhost:7878/api/v3/config/downloadclient | jq '{enableCompletedDownloadHandling}'
curl -s -H "X-Api-Key: $RK" localhost:7878/api/v3/downloadclient | jq '.[] | {name,removeCompletedDownloads}'
curl -s -H "X-Api-Key: $RK" -H 'Content-Type: application/json' -X POST localhost:7878/api/v3/notification/test -d "$(curl -s -H "X-Api-Key: $RK" localhost:7878/api/v3/notification/1)"   # {} = Jellyfin reachable
curl -s -H "X-Api-Key: $RK" localhost:7878/api/v3/releaseprofile | jq -r '.[].name'
grep -a 'language-policy-hook\|grab-guard' /srv/appdata/radarr/logs/radarr.debug.txt | tail
sudo docker exec movie-qbittorrent curl -s localhost:8080/api/v2/app/preferences | jq '{max_ratio,max_seeding_time,max_ratio_act,autorun_program}'
```

Same for Sonarr on `8989` with `/srv/appdata/sonarr/config.xml`.

### Verified 2026-10-20: Seerr request to Jellyfin

*His Girl Friday* (1940, public domain, TMDB 3085), requested with `POST /api/v1/request` (Seerr API key, owner):

| Time | Step | Evidence |
|---|---|---|
| 03:35:34 | Seerr → Radarr add, hook | `movie 12: profile 7 -> 12 (old: 1940, English original)` |
| 03:35:37 | search, grab | `His.Girl.Friday.(1940).(2160p.BluRay.x265.HEVC.10bit.DV.HDR.FLAC.2.0.r00t)`, Treasure Maps, 16.4 GB, CF 22750 |
| 03:37:46 | SABnzbd download done | ~110 MB/s |
| 03:39:57 | par2 verify + unpack done | |
| 03:42:19 | ClamAV hook done | 12 files scanned, 0 infected, the 12 GB main file skipped (over clamd's size limit) |
| 03:43:00 | import | moved (usenet): same inode `253466871261803363` in `complete/` and `media/`, link count 1 (hardlink count 2 is the torrent case); `His Girl Friday (1940) [tmdbid-3085] - [German DL][Bluray-2160p][FLAC 2.0][DV HDR10][x265]-r00t.mkv` |
| 03:43:00 | Connect → Jellyfin | `POST /Library/Media/Updated` 204 |
| 03:44:00 | Jellyfin refresh (+60 s `LibraryMonitorDelay`) | item `2747eb9158519689d8192afab4304774`, TMDB + IMDb ids, trickplay |
| 03:45:00 | Seerr "Jellyfin Recently Added Scan" | media status 5 (Available), request completed |
| | Bazarr | profile `English + German` (English original); EN + DE embedded (Criterion/Sony tracks) → nothing to download; forced EN/DE: no provider has them |

Tag path, *Plan 9 from Outer Space* (1957, TMDB 10513), requested with tags `audio-de` + `subs-fr`: hook `profile 7 -> 12 (old: 1957, English original)`, `bz-fr` added, Bazarr profile created. No grab, correctly: every non-German release `Does not contain one of the required terms: /\b(german|deutsch|ger|deu)\b/i`, the German DL ones are from LQ group `msd` (−30000), the German-only ones dub-only (−100000).

`subs-` on an existing title (*His Girl Friday*): `subs-fr` → `bz-fr` (French is embedded, nothing missing); `subs-pl` → `bz-fr-pl`, profile `English + German + French + Polish (requested)` → `….pl.srt` from OpenSubtitles.com (score 90 %).

Delete (cleanup): Radarr delete with files → Connect → the Jellyfin item was gone 60 s later.

## Completion scan

Every finished download is scanned by ClamAV before Radarr/Sonarr import it: SABnzbd post-processing script and qBittorrent "run on torrent finished" call `../Antivirus/hook/clamav-hook.py`, which streams each file to `clamav:3310` (the `clamav` container from `../Antivirus` joins this bridge).

| | |
|---|---|
| Setup | `scripts/clamav-hooks.sh` (SABnzbd + qBittorrent APIs, idempotent); compose mounts `../Antivirus/hook` → `/clamav-hook:ro` and `/var/lib/clamav-scan/textfile` → `/clamav` in `sabnzbd` and `qbittorrent` |
| Hit | file → `/data/quarantine` (`/srv/.disks/ssd-hot/quarantine`), SABnzbd job failed (no import), torrent stopped + tagged `infected` |
| clamd down | job passes, the nightly scan covers it |
| Test | `docker exec -u abc movie-sabnzbd /clamav-hook/clamav-hook.py /data/usenet/complete/movies/<job>` |

Details, restore and metrics: `../Antivirus/README.md`.

## Network topology

```
            ┌─ gluetun (ProtonVPN) ─────────────┐
 LAN :8080 ─┤  qBittorrent    Prowlarr          │   no leak if tunnel drops
 LAN :9696 ─┤  (no ports/networks of their own) │
            └───────────┬───────────────────────┘
                        │ movie-downloads bridge, 172.28.10.0/24
       ┌────────────────┼────────────────┐
   SABnzbd :8081   Radarr :7878   Sonarr :8989   Bazarr :6767   (+ Seerr :5055 from ../Seerr)
```

qBittorrent and Prowlarr are inside the tunnel because they talk to trackers and
peers. SABnzbd is outside it on purpose: usenet is already TLS, it never seeds,
and a VPN hiccup cannot stall a download. Radarr, Sonarr and Bazarr only talk to
the LAN and metadata APIs. `FIREWALL_OUTBOUND_SUBNETS` in `.env`
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
apps' own logins are turned off — Radarr/Sonarr/Prowlarr auth = `External`, Bazarr = `None`,
SABnzbd username/password empty, qBittorrent bypassed for the proxy subnet. Full
setup, including the Authentik objects that must be created by hand, is in
`HomeLab-FastPi` → `Docker/Traefik/docs/2026-10-06-authentik-sso.md`.

⚠️ **The host ports below are an unauthenticated bypass of all that.** Traefik reaches
these apps at `192.168.1.102:<port>`, so the publications cannot simply be removed —
without them every route breaks. Close the LAN bypass with ufw instead, allowing only
fastpi:

```bash
# DOCKER-USER rule in /etc/iptables/rules.v4 (+ the IPv6 INPUT rule in rules.v6), persisted with netfilter-persistent
# ports: 7878 radarr, 8989 sonarr, 9696 prowlarr, 6767 bazarr, 8080 qbit, 8081 sab, 8096 jellyfin, 8000 cup, 9001 portainer agent, 5055 seerr, 8010 ryot, 8011 ryot-go
sudo iptables -S DOCKER-USER
```

ufw was the wrong tool (Docker bypasses it); the DOCKER-USER rule is what is in place.

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
- `../Seerr` — requests from users, talks to Radarr and Sonarr over this bridge
- `../Antivirus` — ClamAV: completion scan (clamd on this bridge) and nightly scan
- `../../Server/3-Storage-Layout-and-Spindown.md` — mergerfs tiers, demotion
- `../../Server/8-Idle-Watcher.md` — what counts as idle, and `beefy-keep-awake`
