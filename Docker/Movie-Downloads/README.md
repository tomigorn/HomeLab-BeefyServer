# Movie-Downloads — VPN, torrents, usenet, Radarr and Sonarr on beefy

Everything needed to acquire a movie or a series and file it into the library:
**gluetun** (ProtonVPN) wrapping **qBittorrent**, **Prowlarr** and **SABnzbd**, plus
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
| SABnzbd | `http://192.168.1.102:8081` | gluetun (shares its namespace; 8081 inside too, qBittorrent has 8080) |
| VPN status | https://vpn-status-beefy.holy-grail.ch (Holy Grail, homelab-admins) | itself, `:8087` (fastpi only) — see [VPN status](#vpn-status) |
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
# Docker always runs as `sudo docker …`: buntu is deliberately not in the docker group.

# Every container has its own uid (table in "Identity" below). /srv/video is shared
# through group `media` (1100); setgid (2775) so new dirs inherit the group.
sudo groupadd -g 1100 media
sudo groupadd -g 65117 hook-secret
sudo groupadd -g 65118 textfile-writers
sudo usermod -aG media,hook-secret buntu
sudo mkdir -p /srv/video/torrents/{movies,tv} \
             /srv/video/usenet/incomplete \
             /srv/video/usenet/complete/{movies,tv} \
             /srv/video/media/{movies,tv} \
             /srv/video/.recyclebin \
             /srv/appdata/{gluetun,qbittorrent,prowlarr,sabnzbd,radarr,sonarr,bazarr,recyclarr}
sudo chown -R 1000:media /srv/video
sudo chmod -R 2775 /srv/video
sudo chown -R 1000:1000 /srv/appdata
sudo chmod 2770 /srv/appdata/{gluetun,qbittorrent,prowlarr,sabnzbd,radarr,sonarr,bazarr,recyclarr}
for a in radarr:65101 sonarr:65102 prowlarr:65103 bazarr:65104 qbittorrent:65105 \
         sabnzbd:65106 recyclarr:65107 gluetun:65116; do
    sudo chown -R "${a#*:}" "/srv/appdata/${a%%:*}"
done

# psmisc gives tier-move its open-file guard (it REFUSES to run without it, since
# it deletes the source after copying); attr gives getfattr for tier queries
sudo apt install -y psmisc attr

# Reclaim ext4's 5% root reserve on the hot SSD. It exists to stop a full disk
# locking out a ROOT filesystem; on a pure media volume it is ~372G of nothing.
# Online, instant, no data touched.
sudo tune2fs -m 1 /dev/sda1
```

Then fill in `.env` and the secret files ("Secrets" below) — at minimum `secrets/wireguard_private_key`, which must be a **second,
separate** ProtonVPN WireGuard key. Reusing fastpi's key makes both tunnels drop
intermittently as the two endpoints fight over one session. Pick a server that
supports **port forwarding** or torrents will never connect.

## Secrets

Files under `secrets/` (gitignored), never in `.env`: `docker inspect` stays clean. How-to: `../_global/docs/secrets-as-files.md`.

| File | Read by | Owner / mode | Mechanism |
|---|---|---|---|
| `secrets/wireguard_private_key` | gluetun | `root:root 0400` | `WIREGUARD_PRIVATE_KEY_SECRETFILE` |
| `secrets/recyclarr.yml` (`radarr_api_key: '…'`, `sonarr_api_key: '…'`) | recyclarr | `65107:65107 0400` | mounted as `/config/secrets.yml`, `!secret` in `recyclarr/recyclarr.yml` |
| `secrets/vpn-status.apikey` | vpn-status, gluetun healthcheck | `65111 0400` | bind `:ro` |
| `secrets/hook/*.apikey` | Radarr/Sonarr hooks | `buntu:hook-secret 0640` | bind `:ro` |

Check: `sudo docker inspect movie-gluetun movie-recyclarr --format '{{range .Config.Env}}{{println .}}{{end}}' | cut -d= -f1` (`WIREGUARD_PRIVATE_KEY` is the image's empty default).

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
docker exec movie-sabnzbd curl -s https://ipinfo.io/ip      # same VPN IP (also movie-qbittorrent, movie-prowlarr)
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
| qBittorrent | no-password bypass | only Radarr 172.28.10.201, Sonarr .202, vpn-status .203, ClamAV queue worker .204 (stops/tags an infected torrent; pinned in compose, added by `scripts/clamav-hooks.sh`), the gateway .1 (host scripts), localhost (qbit-port.sh) and 192.168.1.2 (Traefik); the clamd container and the rest need the password (review M5, 2026-10-25). Change: `app/setPreferences` key `bypass_auth_subnet_whitelist` |
| SABnzbd | incomplete / complete | `/data/usenet/incomplete` / `/data/usenet/complete/movies` |
| Radarr | root folder | `/data/media/movies` |
| Radarr | **Use Hardlinks instead of Copy** | **on** (§13.6) |
| Radarr | Recycle Bin | `/data/.recyclebin`, 7-day cleanup |
| Radarr | qBittorrent client | host `gluetun`, port `8080` |
| Radarr | SABnzbd client | host `gluetun`, port `8081` |
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
- Identity: no container runs as `buntu` (1000, sudo). Each app has its own uid, in linuxserver non-root mode (no root PID1, `cap_drop: ALL`):

  | uid | App | Primary gid | Extra groups |
  |---|---|---|---|
  | 65101 | radarr | 1100 media | 65117 hook-secret |
  | 65102 | sonarr | 1100 media | 65117 hook-secret |
  | 65103 | prowlarr | 65103 | |
  | 65104 | bazarr | 1100 media | |
  | 65105 | qbittorrent | 1100 media | 65118 textfile-writers |
  | 65106 | sabnzbd | 1100 media | 65118 textfile-writers |
  | 65107 | recyclarr | 65107 | |
  | 65111 | vpn-status | 65111 | |
  | 65116 | gluetun (root, owns `/gluetun` files) | | |

  `media` is the primary gid because `/srv/video` is mergerfs: FUSE passes only the primary gid, `group_add` does not count there. `UMASK=002` keeps files group-writable, which the kernel's `protected_hardlinks` needs for Radarr's hardlinks. `/srv/appdata/<app>` is owned by the app, group `buntu` via setgid, so the host scripts still read the API keys; `o-rwx`. Radarr's own "Set Permissions" is off (not needed).
  SABnzbd "Permissions for completed downloads" = `775` (since 2026-10-20: files unpacked from RARs kept the archive's `644`).
- SABnzbd unwanted extensions (since 2026-10-20): `exe bat cmd com scr pif lnk msi msp ps1 vbs vbe js jse jar hta wsf wsh cpl reg dll apk` → job aborted (fake-video malware releases), on top of the ClamAV scan.

### Indexers (Prowlarr)

All Newznab, synced by Prowlarr to Radarr (movie categories 2000-2090) and Sonarr (TV 5000-5090, anime 5070).

| Indexer | Priority | Base URL | App profile | Limits (Prowlarr query/grab limit) | Added by |
|---|---|---|---|---|---|
| NZBgeek | 25 | `https://api.nzbgeek.info` | Standard (RSS + search) | none | UI |
| NzbPlanet | 26 | `https://api.nzbplanet.net` | Standard (RSS + search) | 20,000 API calls per day (query limit 20000, unit Day) | UI |
| Treasure Maps | 30 | `https://treasure-maps.com` | **Search only** (no RSS) | free tier: 100 API calls + 5 downloads per rolling 24 h (query 100, grab 5, unit Day); API trial until ~2026-11-19 | `scripts/treasure-maps.sh` |

Priority (set 2026-10-26, owner):
- Only a tie-breaker: Radarr/Sonarr pick by quality and custom format score first; priority decides only between equal releases. Lower = preferred.
- Unlimited before limited: NZBgeek 25, NzbPlanet 26, Treasure Maps 30, torrents 40, NZBIndex 50. So the 5 Treasure Maps grabs a day go to releases the others do not have.
- Treasure Maps becomes lifetime/unlimited: priority 27 (`PRIORITY` in `scripts/treasure-maps.sh`, re-run it).
- Change: Prowlarr -> Indexers -> edit -> Indexer Priority (or the API), then `ApplicationIndexerSync`; Radarr/Sonarr follow. Check: `GET /api/v3/indexer` on both shows the same numbers.
- The query/grab limits stop Prowlarr before the indexer blocks the account (Prowlarr counts a rolling 24 h for unit Day). The same limits feed the usage bar above Seerr: `../Seerr/README.md` "Indexer bar" (`../Seerr/indexer-bar/limits.toml`, keep both equal).

- Treasure Maps is the German source: tags German releases with the standard category too (`2040` + `Movies - DE/HD`), so the normal sync categories find them. Knives Out 2019: 31 `German.DL` releases, the other two indexers 0.
- RSS off because Radarr (every 30 min) + Sonarr (every 15 min) would use ~144 calls/day. Search costs ~3 calls per movie search.
- `scripts/treasure-maps.sh` reads the key from `secrets/treasure-maps.apikey` (gitignored, 600) on every run, enables the indexer and is idempotent. New key: replace the file, re-run the script. A rejected key makes it exit 1 and change nothing.
- **Disabled since 2026-10-19:** the first account was blocked (duplicate after the site migration). Re-enable by running the script with the new account's key.

Torrent indexers (`scripts/torrent-indexers.sh`, idempotent, also the FlareSolverr proxy). Each one twice:

| Indexer | Priority | Min. seeders | App tag (Radarr/Sonarr) | Used for | Via |
|---|---|---|---|---|---|
| 1337x, EZTV (Sonarr only), The Pirate Bay, Knaben | 40 | 5 | none | every title | 1337x/EZTV: FlareSolverr (Prowlarr tag `flaresolverr`, Cloudflare); others direct |
| same four, `<name> (old-rare)` | 40 | 1 | `old-rare` | only titles tagged `old-rare` (= on Any quality, [Old and rare titles](#old-and-rare-titles)) | same |

- Owner 2026-10-26: 5 seeders for normal titles, 1 for old & rare (they may have one seeder). Usenet unchanged.
- Radarr/Sonarr: a tagged indexer is used only for titles with a matching tag; untagged indexers for all. An old-rare title sees both copies (same results, the min-1 copy keeps the 1-seeder ones).
- Prowlarr's sync (also "Sync App Indexers", forced) overwrites `minimumSeeders` in the apps but keeps app-side tags, and never pushes its own tags (tested 2026-10-26). So: seeders in Prowlarr (`appMinimumSeeders`), `old-rare` tag set app-side by the script after the sync. Change: `MINSEEDERS` / `RARE_MINSEEDERS` in the script, re-run it.
- Radarr refuses an indexer whose empty test query returns nothing in movie categories ("No Results in configured categories"); The Pirate Bay hits this now and then. Re-run the script later; Prowlarr retries on its own sync too.

### Wrong-film guard

Found 2026-10-20: Treasure Maps answers an ID search (`imdbid`/`tmdbid`/`tvdbid`) with every title match and stamps the *searched* IDs on all of them. Radarr/Sonarr trust indexer IDs over title + year, so a search for *Night of the Living Dead* (1968) grabbed the 1990 remake twice (NZBgeek/NzbPlanet return only the right film). The same risk hits Sonarr: a same-named series from another year.

The shared `scripts/grab-guard.sh` runs On Grab in both apps.

| | |
|---|---|
| Radarr | parses the release year (`/api/v3/parse`); more than 2 years off the movie's year *and* secondary year → queue item removed from SABnzbd/qBit, blocklisted, Radarr searches again |
| Sonarr | parses the year from the release title (`parsedEpisodeInfo.seriesTitleInfo.year`) and keeps it when it is within 2 years of the series start year, inside the series run (`firstAired` year to `lastAired` year, or the current year while it continues) or within 1 year of the grabbed episodes air years (`sonarr_release_episodeairdates`, else `/api/v3/episode` for `sonarr_release_seasonnumber`): groups name a season pack with the season air year (`The.Boys.2024.S04`, 2026-10-27). A same-named series outside the run → removed, blocklisted, re-searched |
| Kept | no year in the title, ±2 years (premiere vs release year: *Plan 9 from Outer Space* is 1957 on TMDB, 1959 in release names), unknown app-side year; Sonarr daily shows carry an air date, not a title year, so they are always kept |
| Cost | each wrong grab uses one of Treasure Maps' 5 grabs/day and a few seconds of download |
| Not covered | a remake / same-named show with the same title *and* year; a release whose title carries no year |
| Queue lookup | by download id over the whole queue with `includeUnknownMovieItems=true` (Sonarr `includeUnknownSeriesItems`), never the `movieIds`/`seriesIds` filter: a release the app cannot map to the title (the wrong year, *A Separation* 2026 vs 2011, 2026-10-24) is queued with `movieId` null, which the filter and the default listing both hide, so the guard never saw it |
| Sonarr detached | Sonarr writes the grab history and lists the queue item only after the script returns, so Sonarr removals run in a detached background copy (`GG_DETACH=0` = inline, log `/config/grab-guard.log`); inline, the queue and history lookups could never succeed |
| Fallback | queue DELETE failing or not in the queue after `GG_WAIT` (90 s) → the `grabbed` history record with the same download id is marked failed (`POST /history/failed/{id}`: blocklist + new search); no record or the call fails → `check by hand` |
| Telegram | every wrong-year or dub-only case (removed / marked failed / check by hand) writes `<config dir>/guard-events/*.json` (`/srv/appdata/radarr`, `/srv/appdata/sonarr`); [Queue watchdog](#queue-watchdog) handles it within 15 min (removed / marked failed → morning digest, check by hand → Telegram) and deletes the file. The containers hold no bot token: no secret mount, no outbound call from the apps |
| Dub-only | the grabbed release title is on `/config/dub-only.json` ([NFO scout](#nfo-scout): ≥ 2 languages in the NFO, the original missing) with the same title id and younger than 30 days → same removal as above (queue lookup, remove + blocklist + re-search, history fallback), whatever the year. Exact title match only; no list or an unreadable one = no action. Morning digest entry (`guard_removed`); `Dub-only release` Telegram only when it could not be removed |
| Registered by | `scripts/language-policy.sh` (both apps, idempotent) |
| Tests | `tests/test_grab_guard.sh`, `tests/test_grab_guard_sonarr.sh`; live: `sudo docker exec movie-radarr env radarr_eventtype=Grab radarr_movie_id=<id> radarr_release_title=<name> radarr_download_id=x GG_WAIT=0 bash /scripts/grab-guard.sh` (Sonarr: `movie-sonarr`, `sonarr_eventtype`, `sonarr_series_id`, `sonarr_release_title`, `sonarr_download_id`) |

### Queue watchdog

`scripts/queue-watchdog.py` (beefy host, `systemd/queue-watchdog.timer`, every 15 min, first run 15 min after boot) sends **one** Telegram per item that needs a hand; what the apps handle themselves goes to fastpi's 06:30 morning digest instead.

| Message | Where | When |
|---|---|---|
| blocked: state `importBlocked` / `importPending` / `failedPending`, or status `warning` / `error` (needs a manual import) | Telegram | after `QW_BLOCKED_MIN` (30 min); 22:00–07:00 held, sent on the first run from 07:00 if still blocked |
| guard "could not be removed: check by hand" | Telegram | at once; 22:00–07:00 held (event file kept), sent from 07:00 |
| indexers disabled after failures | Telegram | at once; 22:00–07:00 held, sent from 07:00 if still disabled |
| guard removed / marked failed, re-searching (wrong film, dub-only) | digest `guard_removed` | at once |
| failed (status `failed`, the app re-searches) | digest `failed` | at once |
| stalled (`downloading` / `queued`, `sizeleft` not smaller) | digest `stalled` | after `QW_STALL_HOURS` (6 h) |
| indexers recovered | digest `indexer_recovered` | at once |
| subtitle repaired / suspect ([Subtitle check](#subtitle-check)) | digest `subtitle_repaired` / `subtitle_suspect` | at once |

| | |
|---|---|
| Indexers | `/health` of each app: **one** Telegram while indexers are disabled after failures (`IndexerStatusCheck`, `IndexerLongTermStatusCheck`: indexer names, disabled until / last error when `/indexerstatus` answers, fix `System → Status → test all indexers` or `POST /api/v3/indexer/testall`), recovery = digest. Never runs testall itself. State key `indexers/<app>` |
| Indexer exceptions | no alert for Treasure Maps backoffs (grab limit: error empty or 429 / limit) and The Pirate Bay (flaky "no results", [Indexers](#indexers-prowlarr)); any other error on them alerts. Found 2026-10-26: 5 of 6 indexers sat in a 24 h backoff after 401s during a Prowlarr key rotation |
| Message | app, title (+year / SxxExx), release, state, status messages, how long, hint `Radarr → Activity → Queue` (Sonarr: `Sonarr → …`) |
| Once | per app + download id + condition; again only after the condition cleared and came back (or a failed or held Telegram: retried next run) |
| Quiet hours | 22:00–07:00 Europe/Zurich (DST-safe); beefy asleep at 07:00 = the first run after it wakes |
| Digest | `~/.local/state/movie-downloads/watchdog-digest.prom`, rewritten every run: `beefy_watchdog_event_timestamp_seconds{kind,app,title}` (last 48 h, one series per kind + app + title, newest 40, title ≤ 80 chars) + `beefy_watchdog_digest_updated_timestamp_seconds`; events kept in `watchdog-digest.json` |
| Sends log | `~/.local/state/movie-downloads/watchdog-sends.log`: `<ISO time> <kind> sent\|held\|digest <title>`, last 1000 lines |
| State | `~/.local/state/movie-downloads/queue-watchdog.json`: first seen per condition, last `sizeleft`; entries of downloads that left the queue are dropped; an unreachable app keeps its entries (exit 1) |
| Telegram | `../_global/scripts/oob-alert.sh` (direct to api.telegram.org, no edge); the token stays in `_global/secrets/` |
| Env | `QW_BLOCKED_MIN`, `QW_STALL_HOURS`, `QW_STATE`, `QW_EVENTS` (colon-separated dirs; default the two `guard-events` dirs + `/srv/appdata/bazarr/subtitle-check/events`), `QW_ALERT`, `QW_RADARR_CONFIG`, `QW_SONARR_CONFIG` |
| Tests | `python3 tests/test_queue_watchdog.py` |

```bash
cd ~/Projects/Docker/Movie-Downloads/systemd
sudo install -m 0644 queue-watchdog.service queue-watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now queue-watchdog.timer
scripts/queue-watchdog.py; echo $?    # empty queue: no message, 0
```

### Can wait: downloads at night

Downloads wait for the low tariff (22:00-06:00, all Sunday) unless the title carries the tag `download-now`. The search still runs at once; only the download is held. Seerr's request dialog has the checkbox (`../Seerr/README.md` "Can wait").

| Download client (Radarr + Sonarr) | Tags | Category | Adds |
|---|---|---|---|
| `qBittorrent night` | none | `radarr-night` / `sonarr-night` | stopped (Initial State) |
| `SABnzbd night` | none | `movies-night` / `tv-night` | paused (priority Paused) |
| `qBittorrent`, `SABnzbd` | `download-now` | `radarr` / `sonarr`, `movies` / `tv` | started |

Radarr/Sonarr use a tagged client only for a title with a matching tag, otherwise the untagged ones. Every grab path (Seerr, RSS, scouts, `request-tags`, cutoff search) ends there. Night categories use the same folders, so imports and hardlinks are unchanged.

| Part | What |
|---|---|
| Resume (FIFO) | `scripts/night-downloads.py` (`systemd/night-downloads.timer`: boot, every 2 min, 22:05, as buntu): in the low tariff one night download per client at a time, in request order: qBittorrent oldest `added_on`, SABnzbd queue position; one title (film/series) finishes before the next starts. Later night items that run anyway are paused back. A torrent `stalledDL`/`metaDL` for `STALL_MIN` (20, env `ND_STALL_MIN`) lets the next one start too. Own logic, not qBittorrent queueing (global) or SABnzbd `top_only` (would hold `download-now` jobs behind night jobs) |
| 06:00 pause | outside the low tariff (not Sunday) every night item still downloading is paused, except ETA < `GRACE_MIN` (15 min; qBittorrent `eta`, SABnzbd `timeleft`; unknown, stalled or no speed = pause). Checked every run, so a grace item whose ETA grows to ≥ 15 min is paused too. Paused items keep their partial data and resume at the next low tariff in FIFO order |
| `download-now` | resumed any time, never paused or queued by this |
| State for fastpi | `~/.local/state/movie-downloads/night-downloads.json` (`pending`; `stalled` = stall clock per torrent). fastpi copies it while beefy is awake and wakes beefy at 22:05 only when `pending > 0` (`HomeLab-FastPi` `Docker/Beefy-Waker/README.md` "Night downloads wake") |
| Seerr label | outside the low tariff the waiting download ids also go to `/srv/appdata/indexer-bar/night-waiting.json` (env `ND_PUBLIC`, empty = off): Seerr shows them as "Waiting for the night 🌙" (`../Seerr/README.md` "Night label") |
| First download only | Connect **Download now: first download only** (`scripts/download-now-hook.sh`, On Import/Upgrade) removes `download-now` after the first import; upgrades and later episodes wait again |
| Existing titles | `scripts/request-tags.sh` copies `download-now` only onto a title that was in Radarr/Sonarr before the request; the paused item then resumes within 2 min |
| By hand | Radarr/Sonarr: add the tag `download-now` to the title. Resuming a night item in qBittorrent/SABnzbd lasts only until the next run (paused again by day, or FIFO at night) |
| Setup | `scripts/night-downloads-setup.sh` (tag, clients, categories; idempotent), `scripts/language-policy.sh` (Connect script) |
| Check | `scripts/night-downloads.py --dry-run`; simulate a time: `ND_NOW=2026-10-27T22:10 scripts/night-downloads.py --dry-run` (`ND_NOW` refused without `--dry-run`) |
| Tests | `python3 tests/test_night_downloads.py`, `tests/test_download_now_hook.sh`, `tests/test_request_tags.sh` |

| Side effect | Behaviour |
|---|---|
| beefy sleep | paused items are idle (no net/disk traffic for the idle watcher): after 06:00 beefy sleeps once the grace items finished; seeding never keeps it awake; a resumed download does |
| Queue watchdog | `paused` is never stalled; the stall clock starts on resume |
| Completion scan, Delete a title | unchanged: the scan runs when the download finishes; the wipe reads the categories of all clients |
| Paused by hand in a night category | resumed at night in FIFO order like the rest |

```bash
cd ~/Projects/Docker/Movie-Downloads/systemd
sudo install -m 0644 night-downloads.service night-downloads.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now night-downloads.timer
```

### Delete a title

| Seerr button (Manage panel, admin) | What goes |
|---|---|
| **Remove from Radarr** / **Remove from Sonarr** | the title in Radarr/Sonarr with its files, nothing else (torrent, SABnzbd history and Seerr request stay; Jellyfin drops the item when Radarr/Sonarr tell it the files are gone) |
| **Advanced → Clear Data** | everything: Seerr request, Radarr/Sonarr entry + files, qBittorrent torrent + files, SABnzbd history and Jellyfin item (within ~2 min) |

Clear Data after an earlier Remove from Radarr/Sonarr still removes the leftovers. A new request after Clear Data starts fresh.

Seerr has no event for Clear Data (it only deletes its media row), so `scripts/clear-data-wipe.sh` (timer `clear-data-wipe.timer`, every 2 min, as buntu) compares Seerr's media list with its last snapshot and runs `scripts/title-wipe-hook.sh wipe` for every row that is gone.

| Step | What |
|---|---|
| Record (On Grab / Import / Upgrade) | `title-wipe-hook.sh` (Connect → Custom Script **Title wipe**, both apps) appends `<tmdbId or tvdbId> <qbit/sab> <hash or nzo id>` to `/config/title-wipe.tsv` (`/srv/appdata/radarr`, `/srv/appdata/sonarr`). Deleting in Radarr/Sonarr wipes nothing |
| Detect | snapshot `~/.local/state/movie-downloads/seerr-media.tsv` (type, tmdbId, tvdbId, status, row id); a row gone, or back with a new id and no request (re-created by Seerr's own Jellyfin/Radarr/Sonarr sync), = Clear Data |
| Never wipes | first run (snapshot only); Seerr list unreadable, empty or incomplete; row back in a second listing; title requested again; more than 5 rows gone at once (`CW_MAX=<n> scripts/clear-data-wipe.sh` once by hand if intended); blocklisted rows (un-blocklisting deletes the row); series without tvdb id |
| Radarr/Sonarr | the title by exact tmdbId/tvdbId: `DELETE …?deleteFiles=true` (file to the recycle bin `/data/.recyclebin`, 7 days) |
| qBittorrent | the recorded hashes, only in the app's own category (`radarr` / `sonarr`): torrent **and its files**. fastpi's qBittorrent is never touched |
| SABnzbd | the recorded jobs: queue and history (`del_files=1`) |
| Jellyfin | the item with exactly that Tmdb/Tvdb provider id under `/media/movies/` or `/media/tv/`: waits 30 s for Radarr/Sonarr's own Jellyfin notification, then `DELETE /Items/<id>` |
| Seerr | a row its sync re-created meanwhile (no request) is removed |
| No record (grabbed before 2026-10-23, history gone) | name match in the app's category: film `<title> <year> …` only if exactly one torrent/job matches; series `<title> S01…`/`<title> <year>`/`Season`/`Complete`; skipped (logged) when ambiguous or another library title has the same name |
| Failures | retried on the next runs, given up after 5 tries (logged) |
| Log | `~/.local/state/movie-downloads/clear-data-wipe.log`, `journalctl -u clear-data-wipe` |
| Keys | Seerr `../Seerr/secrets/api-key`, SABnzbd `secrets/hook/sabnzbd.apikey` (640, group `hook-secret`, written by `scripts/language-policy.sh` via `sudo -n`), Jellyfin `../Jellyfin/secrets/api-key` |
| Registered by | `scripts/language-policy.sh` (Connect script, both apps, idempotent) |
| Seed old grabs | `sudo docker exec movie-radarr bash /scripts/title-wipe-hook.sh seed radarr` (Sonarr: `movie-sonarr … seed sonarr`), from the apps' grab history, idempotent |
| Tests | `tests/test_title_wipe_hook.sh`, `tests/test_clear_data_wipe.sh` |

Install the timer:

```bash
cd ~/Projects/Docker/Movie-Downloads/systemd
sudo install -m 0644 clear-data-wipe.service clear-data-wipe.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now clear-data-wipe.timer
```

Verified 2026-10-23 with throwaway *Night of the Living Dead* (1968) and *The General* (1926): Remove from Radarr → only the Radarr entry + file gone; then Clear Data → torrent + files and SABnzbd entry gone within one cycle; Clear Data alone → Radarr entry + file, torrent, SABnzbd entry, Jellyfin item gone, and the row Seerr's Jellyfin sync re-created seconds later removed; a 1990 decoy torrent in the same category stayed. Sonarr, same day: [Radarr / Sonarr parity](#radarr--sonarr-parity).

The records of a wiped title stay in `title-wipe.tsv`: the wipe runs as buntu and the file belongs to the app's uid (644). Harmless, those hashes and jobs no longer exist.

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
| Connect (Custom Script) | `Language policy`, `Wrong-film guard`, `Title wipe`, `Original audio`, `Download now: first download only` | [Languages](#languages), [Wrong-film guard](#wrong-film-guard), [Delete a title](#delete-a-title), [Can wait](#can-wait-downloads-at-night) |

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
it manages. API keys: `secrets/recyclarr.yml` (`!secret radarr_api_key` / `sonarr_api_key`, "Secrets"). Own custom formats (Swiss German, resolution, `Bad Group: BlackBit`, `Bad Group: Vyndros`): `recyclarr/custom-formats/radarr/*.json` and `…/sonarr/*.json` (same patterns, own ids; Sonarr has no `Resolution: DVD`, its DVD quality is 480p and counts as SD), loaded through `recyclarr/settings.yml` (local resource provider); both are mounted into the container.

```bash
docker compose run --rm recyclarr sync --preview   # what would change
docker compose run --rm recyclarr sync             # apply now
docker compose run --rm recyclarr config list templates   # TRaSH templates
```

After the first sync on a rebuild, and after any sync that changes the Swiss German profile (Recyclarr resets its language to Original), run `scripts/language-policy.sh` (native language formats and their scores, profile language Any, tags, hook).

| Profile | Use | Qualities (top wins) | Upgrade until |
|---|---|---|---|
| **4K HDR - UHD Bluray + WEB (1080p fallback)** — default | everything | Bluray-2160p › WEB-2160p › Bluray-1080p › WEB-1080p | Bluray-2160p, CF score 25000000 (HDR + original + English, see [Release scoring](#release-scoring)) |
| 4K HDR - … (1080p fallback) - English original | English-original titles, set by `scripts/language-policy.sh` | same | Bluray-2160p, 20000000 (HDR + English; German DL wins at grab time, not chased); `Language: Original + English` 0 (see [Languages](#languages)) |
| Any quality - old & rare (upgrades to 4K) (+ `- English original` twin) | old/niche titles, set automatically on add ([Old and rare titles](#old-and-rare-titles)) or picked per request | Bluray-2160p › WEB-2160p › Bluray-1080p › WEB-1080p › HDTV-1080p › Bluray-720p › WEB-720p › HDTV-720p › Bluray-576p › Bluray-480p › WEB-480p › DVD › SDTV › Unknown | Bluray-2160p, 25000000 (twin 20000000); scores identical to the default |
| Swiss German (Mundart) only - any quality | titles tagged `audio-swiss-german`, set by the hook ([Swiss German](#swiss-german)) | one group: Unknown, SDTV … Bluray-2160p, ranked by resolution scores (Unknown gets none, so it ranks last) | Swiss German + 2160p (score 1200000) |
| HD Bluray + WEB | space saver, pick per movie | Bluray-1080p › WEB-1080p › Bluray-720p | Bluray-1080p, 5000000 |
| Anime - Remux + WEB 1080p | anime films, pick per movie (Seerr has no anime profile for movies) | Remux/Bluray-1080p › WEB-1080p › 720p … | Remux-1080p, 5000000; TRaSH `[Anime] Remux-1080p`: anime group tiers, no Golden Rule (anime is x265 10-bit by default), minimum score 100 |

Why this shape:

- **TRaSH "UHD Bluray + WEB"** = high-quality 4K *encodes* (20–60 GB), not remuxes
  (50–90 GB). With ~34 TB free that is roughly 1 000 movies; remuxes would halve it
  for a difference few screens show.
- **1080p fallback in the same profile** is TRaSH's documented "combine profiles"
  option: a movie with no 4K release still gets 1080p now and is upgraded to 4K
  automatically when one appears. Radarr always ranks **quality before score**, so
  any acceptable 2160p beats any 1080p. The HD Bluray tier scores are added so the
  fallback picks a good 1080p group.
- **HDR**: 2160p SDR is allowed (owner 2026-10-26: 4K SDR beats 1080p HDR; quality ranks first). HDR (+20000000) beats SDR inside one quality, so a 4K HDR release wins over a 4K SDR one and later replaces it (cutoff). Marked upscales stay rejected (`Upscaled`); the profile names keep "4K HDR" (Seerr and the hooks use them).
  `DV (w/o HDR fallback)` (−50000000) rejects Dolby Vision Profile 5 (no HDR10 base
  layer → green/purple on non-DV clients, and Jellyfin's tone mapping of P5 is
  untested here). DV *with* HDR10 fallback (P7/P8, "DV HDR10") scores like HDR10. HDR outranks languages: [Release scoring](#release-scoring).
  DV Boost / HDR10+ Boost are **not** enabled — turn on `DV Boost` if every TV in
  the house does Dolby Vision.
- **Golden Rule**: x265 only for HDR/4K (`x265 (no HDR/DV)` −50000000); 1080p stays x264,
  which direct-plays everywhere. Not applied to the anime profile.
- **Unwanted**: BR-DISK, LQ groups, 3D, AV1, upscaled, extras, generated HDR, … −50000000.
- **Languages**: see [Release scoring](#release-scoring) and [Languages](#languages) — the same scores in every profile.
- **Old and rare titles**<a id="old-and-rare-titles"></a>: the 4K profiles allow nothing below 1080p, so a film that only exists on DVD (e.g. *The Ring Thing*, 2004) is rejected. `Any quality - old & rare (upgrades to 4K)` (Radarr and Sonarr) has every custom format and score of the default (languages, rejects, HDR rules, Golden Rule), but allows SDTV/DVD up to 2160p and upgrades until Bluray-2160p / WEB 2160p. Quality `Unknown` (e.g. `Title (2004) [XVID]`, no source in the name) is allowed but ranks lowest and is upgraded by any known quality; the same holds for the Swiss German profile. Not allowed: CAM/TS/screener, DVD-R/BR-DISK images, remuxes, Raw-HD. Its twin `… - English original` has `Language: Original + English` at 0, like the 4K twin.
  - **Automatic on add** (`language-policy-hook.sh`), only for titles added on the default 4K profile or its twin; any other profile picked in Seerr stays:

    | Rule | Movies (Radarr) | Series (Sonarr) |
    |---|---|---|
    | old | `year` ≤ current year − 12 | `year` (first aired) ≤ current year − 12 |
    | niche | ≥ 2 years old and TMDB votes < 100 (none = 0) | ≥ 2 years old and votes < 1000 (Sonarr's rating, IMDb scale) |
    | unknown year (0) | stays | stays |

  - Why: Radarr ranks quality first, so on Any quality an old title still gets the best release that exists (4K if there is one) and keeps upgrading; the only difference is that a DVD-only title is grabbed instead of rejected. ≤ 2014 is before UHD Bluray (2016) and 4K streaming; titles that old with no 1080p are exactly the DVD-only case. Votes catch obscure newer titles (*The Ring Thing*: 19 TMDB votes, popular films have thousands); the 2-year gate keeps brand-new films (few votes yet, early 480p/720p WEB rips) on the 4K profile.
  - English originals go to the Any twin, the rest to Any; `language-policy.sh` swaps existing titles between Any and its twin like the 4K pair (no age rule for existing titles).
  - Tag `old-rare` = exactly the titles on Any or its twin (torrent indexers with min. 1 seeder, [Indexers](#indexers-prowlarr)): the hook sets it on add (and removes it when a title lands elsewhere, e.g. Swiss German); `language-policy.sh` does the same for every existing title. A profile changed by hand later: re-run `language-policy.sh`.
  - Verified 2026-10-20: *Nosferatu* (1922, German) added on the default -> `movie 9: profile 7 -> 11 (old: 1922)` in the debug log.
  - Golden Rule applies: an x265 SD/HD encode without HDR is rejected (−50000000), as in the 1080p tier.
  - Verified 2026-10-20: *The Ring Thing* (TMDB 10248) on this profile grabbed `The.Ring.Thing.Das.Ding.mit.dem.Ring.German.2004.AC3.DVDRiP.x264-IND` (DVD, score 20750 on the scale before 2026-10-26, 0.96 GB, SABnzbd); the DVD9 image was rejected (DVD-R not wanted).
- The six stock Radarr profiles (Any, SD, HD-720p, …) cannot be disabled (the
  API has no such flag). The six stock profiles were DELETED 2026-10-22 (they were
  Recyclarr does not touch them. Delete them in the UI if they are in the way.

### Upgrades

| | |
|---|---|
| Profiles | every Recyclarr profile `upgradeAllowed: true`; cutoff = best quality (Radarr Bluray-2160p, Sonarr WEB 2160p) + score 25000000 = HDR + original + English (twins 20000000; HD and anime 5000000); minimum upgrade step 1, see [Release scoring](#release-scoring) |
| RSS | Radarr every 30 min, Sonarr every 15 min, NZBgeek + NzbPlanet (Treasure Maps is search-only). Runs only while beefy is awake: new releases that appear while it sleeps are missed by RSS |
| Night search | `systemd/night-search.timer`, daily 04:17 (inside the 03:55 wake, after Recyclarr 04:15): `scripts/night-search.py`, 1 Treasure Maps call per movie or episode, ~3 per season |
| Order | 1 episodes aired in the last 14 days without a file; 2 movies released (digital or physical) in the last 60 days without a file; 3 cutoff-unmet: 5 movies + 5 episodes. Monitored only, not in the queue, not searched in the last 20 h, least recently searched first. 3+ missing episodes of one season = one `SeasonSearch`, else `EpisodeSearch` |
| Indexer budget | ≤ 30 Treasure Maps searches a night (`NS_TM_MAX`) and never above 70 % of its window (`NS_TM_MAX_PCT`, read from the indexer bar `usage.json`; bar missing or older than 15 min = 10, `NS_TM_FALLBACK`). Over budget: the rest is logged `skipped (budget)` and, being least recently searched, comes first next night. Radarr's own `CutoffUnmetMoviesSearch` has no cap, hence the script |
| Downloads | land in the untagged night clients, `night-downloads.timer` (every 2 min) resumes them at once in the low tariff; the idle watcher counts download traffic as busy, and the script waits for its searches (cap 30 min), so the window holds beefy awake |

```bash
cd ~/Projects/Docker/Movie-Downloads/systemd
sudo install -m 0644 night-search.service night-search.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now night-search.timer && sudo systemctl disable --now cutoff-search.timer
scripts/night-search.py --dry-run     # what it would search, TM estimate
sudo systemctl start night-search.service; journalctl -u night-search -n 20   # run now
```

- Knobs (`Environment=` in the service): `NS_TM_MAX`, `NS_TM_MAX_PCT`, `NS_CUTOFF_MOVIES`, `NS_CUTOFF_EPISODES` (0 = skip), `NS_NEW_EPISODE_DAYS`, `NS_NEW_FILM_DAYS`, `NS_SEASON_MIN`, `NS_MIN_GAP_HOURS`.
- Tests: `python3 tests/test_night_search.py`. Replaces the Sunday `cutoff-search.sh` (10 + 10): removed 2026-10-26.

### Release scoring

Owner's order 2026-10-26, inside one quality (quality always ranks first: any allowed 2160p beats any 1080p). The same in Radarr and Sonarr, every profile except Swiss German (own scale, see [Swiss German](#swiss-german)).

| # | Wins first | Score | Custom format |
|---|---|---|---|
| 0 | hard language tags | filter, no score | `audio-<language>` release profiles, Swiss German profile ([Per-request audio and subtitles](#per-request-audio-and-subtitles)) |
| 1 | HDR (DV with HDR10 fallback, HDR10, HDR10+, HLG, PQ); SDR none (2160p SDR allowed) | 20000000 | TRaSH `HDR` (the only HDR format synced); DV without fallback rejected |
| 2 | language rung: original + English + German › original + English › original + German › original | 7000000 › 5000000 › 2000000 › 0 | `Language: Original + English` 5000000 + `German DL` / `German DL (undefined)` 2000000; twins: English + German 2000000 › English 0 ([Languages](#languages)) |
| 3 | Sonarr: one complete season pack | 2000 | own `Season Pack` (`recyclarr/custom-formats/sonarr/season-pack.json`, Release Type = Season Pack); 0 in the anime (TRaSH anime ranks by group tier) and Swiss German profiles |
| 4 | audio format, group tier, streaming service, repack | Radarr < 7000, Sonarr < 2000 | TRaSH defaults (TrueHD Atmos 5000, Bluray tier 1800, WEB Tier 01 1700, streaming 75, …); the HD and anime templates score no audio formats |
| 5 | each further audio language | +10 | `Extra audio: <language>`, one per app language except English and German (Radarr 55, Sonarr 44); tie-breaker only (owner 2026-10-26: an Italian dub must not beat a clean WEB Tier 01 release) |
| – | reject | −50000000 | dub only, no original audio, x265 without HDR below 2160p, DV without fallback, unwanted formats (upscaled, BR-DISK, LQ, …), VOSTFR (anime), own `Bad Group: BlackBit` / `Bad Group: Vyndros` (4K SDR x265 re-encodes of HDR sources) |

| Guarantee | Why it holds |
|---|---|
| HDR beats any SDR release of the same quality (2160p too) | best without HDR: 7000000 + anime `Dual Audio` 5000000 + 55 × 10 + audio + season pack < 12010000 < 20000000 |
| rungs before season pack, audio and extras | smallest rung gap 2000000 > season pack + audio + group + all extras (< 10000) |
| season pack before single episodes (Sonarr) | 2000 > WEB Tier 01 1700 + streaming 75 + 5 extras 50; a multi-season pack stays rejected by Sonarr itself (a score cannot lift a rejection) |
| audio, group and streaming before extras | one extra (10) < the smallest audio, group or streaming bonus (Radarr 15, Sonarr 75) |
| rejects stay rejected | −50000000 + best positive (≈ 33000000) < minimum 0; sums stay far inside int32 |
| 2160p SDR allowed | `SDR` 0 in the 4K and Any profiles (TRaSH's `SDR` matches only 2160p without an HDR marker); any allowed 2160p beats any 1080p (quality first) |

Upgrades (5 grabs/day on Treasure Maps):

| | |
|---|---|
| Cutoff | 4K and Any: 2160p HDR + original + English 25000000; English-original twins: HDR + English only 20000000 (owner 2026-10-27: German DL keeps its +2000000 at grab time, so it still wins when both exist, but is never upgraded to afterwards). HD and anime (1080p top): original + English 5000000 (no HDR in the cutoff, see trade-offs). Swiss German 1200000 (unchanged) |
| Minimum upgrade step | 1 (`min_upgrade_format_score`, Radarr's default; owner 2026-10-26): below the cutoff any better score re-downloads — HDR, a language rung, a season pack, better audio, group or repack, an extra language (+10). At or above the cutoff nothing upgrades. Season pack and extras (≤ 2550) never bridge a cutoff gap (≥ 2990000 below each cutoff) |
| HDR | HDR wins at grab time. A 2160p SDR file stays below the cutoff (best SDR ≈ 7600000) and is replaced by a 2160p HDR release; a 2160p HDR file without English (original + German 22000000) keeps upgrading to original + English. |
| Trade-off | an SDR 4K is often an unmarked upscale or an HDR release that omits `HDR` in its name: it is taken now and replaced when a marked HDR 4K appears (one more download). A 2160p HDR file with original + English but without German is done (German is not chased, as before). |
| Trade-off HD / anime | their top quality is 1080p, where HDR is rare (HD rejects x265, nearly every 1080p HDR is x265): a 25000000 cutoff would leave almost every file cutoff-unmet and the weekly search would chase it forever, so they keep 5000000. A 1080p HDR file there is done at once (also without English). |

Language detection (the apps see only the release name and the indexer):

- `ITALIAN.ENGLISH.GERMAN` = 3 languages; `German.DL` = German + original; `German.ML` = German + English + original.
- `MULTi`, `Multi-Audio` or no language word = the original only: no bonus. A multi release that lists no languages ranks like an original-only one, unless the [NFO scout](#nfo-scout) pushes it with the languages from its NFO.
- `Extra audio: <original>` fires only when another language is present too: a constant +10 on every multi-language release of that title, so it never changes the order, and an original-only release gets nothing (the anime minimum 100 still skips no-name groups).

Change it:

| What | Where, then |
|---|---|
| HDR, `German DL`, `Anime Dual Audio`, rejects, cutoff, minimum upgrade step | `recyclarr/recyclarr.yml` → `docker compose run --rm recyclarr sync`. The container mounts the whole `recyclarr/` folder at `/config/homelab` (`recyclarr.yml`, `settings.yml`, `custom-formats` in `/config` are symlinks into it), so any edit is seen by the 04:15 sync. Single-file mounts kept old inodes and restored old scores on 2026-10-26 |
| `Language: Original + English`, extra-language score, which languages count | `scripts/language_formats.py` → `scripts/language-policy.sh`  |
| `Season Pack` (Sonarr) | definition `recyclarr/custom-formats/sonarr/season-pack.json`, score `recyclarr/recyclarr.yml` → `docker compose run --rm recyclarr sync` |
| Tests | `python3 tests/test_scoring_order.py` (offline, every profile) |
| Live check | `scripts/scoring-check.py` (`GET /parse`, never searches or grabs) |

Recyclarr's nightly sync keeps the native scores (`except` / `except_patterns: ^Extra audio: `). After a rebuild: sync, then `scripts/language-policy.sh`.

Own rejects `Bad Group: BlackBit` and `Bad Group: Vyndros` (`recyclarr/custom-formats/*/bad-group-{blackbit,vyndros}.json`): TRaSH `Bad Dual Groups` matches only a parsed group (`-BlackBit`), so 1337x names like `… H.265 BlackBit.mkv` escaped it (2026-10-26); Vyndros (4K SDR x265 re-encodes of HDR sources, ~2 GB/episode) added 2026-10-27. −50000000 everywhere, Swiss German −2000000. Release-title regex `(?<![A-Za-z0-9])<Group>(?![A-Za-z0-9])`, case-insensitive: matches with dash, space, dot, underscore, brackets/parentheses, at the end or before `.mkv`; no match inside longer words (`BlackBitter`, `XVyndros`, `Blackbitumen`) or on separated spellings (`Black Bit`). Checked by `tests/test_scoring_order.py` (`OwnRejects`) and `scripts/scoring-check.py` (l, live `/parse`); verified 2026-10-27: all checks passed.

Verified 2026-10-26 after extras 10 and `Season Pack` (`scripts/scoring-check.py`, all 95 checks passed; Radarr *Io Capitano*, Italian, 4K profile, cutoff 25000000). Quality ranks first: every 2160p row beats every 1080p row whatever the score.

| Release (…-GRP) | Score |
|---|---|
| `ITALIAN.ENGLISH.GERMAN.2160p.UHD.BluRay.TrueHD.Atmos.7.1.HDR.x265` | 27005010 |
| `ITALIAN.ENGLISH.GERMAN.SPANISH.PORTUGUESE.2160p.UHD.BluRay.DDP5.1.HDR.x265` | 27001780 (audio beats 2 extras) |
| `ITALIAN.ENGLISH.SPANISH.PORTUGUESE.FRENCH.2160p.UHD.BluRay.DDP5.1.HDR.x265` | 25001790 |
| `ITALIAN.ENGLISH.2160p.UHD.BluRay.DDP5.1.HDR.x265` | 25001760 (cutoff) |
| `ITALIAN.GERMAN.SPANISH.2160p.UHD.BluRay.DDP5.1.HDR.x265` | 22001770 |
| `ITALIAN.2160p.UHD.BluRay.DDP5.1.HDR.x265` | 20001750 |
| `ITALIAN.ENGLISH.GERMAN.SPANISH.PORTUGUESE.2160p.UHD.BluRay.TrueHD.Atmos.7.1.x265` (SDR) | 7005030 (accepted, below the cutoff) |
| `ITALIAN.ENGLISH.2160p.UHD.BluRay.DDP5.1.x265` (SDR) | 5001760 (accepted) |
| `ITALIAN.ENGLISH.1080p.BluRay.DDP5.1.HDR.x265` | 25001760 (1080p: loses to any 2160p) |
| `ITALIAN.ENGLISH.GERMAN.1080p.BluRay.DDP5.1.x264` | 7001760 |
| `ITALIAN.ENGLISH.GERMAN.SPANISH.PORTUGUESE.1080p.BluRay.DDP5.1.x265` (x265 SDR 1080p) | −42998220 |
| `GERMAN.2160p.UHD.BluRay.DDP5.1.HDR.x265` (dub only) | −79998250 |

Sonarr (*Money Heist*, Spanish, 2160p NF WEB-DL) ranks the same: `SPANISH.ENGLISH.GERMAN.FRENCH.PORTUGUESE` HDR 27000180 › `SPANISH.ENGLISH` HDR season pack `S01` 25002160 › `SPANISH.ENGLISH` HDR from WEB Tier 01 (`-FLUX`) 25001860 › `SPANISH.ENGLISH.PORTUGUESE` HDR 25000170 › `SPANISH.ENGLISH` HDR 25000160 (cutoff) › `SPANISH.GERMAN.PORTUGUESE` HDR 22000170 › `SPANISH` HDR 20000150 › `SPANISH.ENGLISH.GERMAN.PORTUGUESE.FRENCH` SDR TrueHD Atmos 7000180 › `SPANISH.ENGLISH` SDR 5000160.

### Languages

Policy (2026-10-19, order changed by the owner 2026-10-25): audio = the original language plus German plus English; if
that does not exist, drop one (English first choice, German second); never a dub
without the original track. Subtitles = English + German + the original language, every new subtitle synced to the original-language audio by Bazarr (below). Films, series and anime alike; existing titles below the cutoff upgrade to the new order.
Lives in four places: `recyclarr/recyclarr.yml` (scores), `scripts/language-policy.sh`
(what Recyclarr cannot express: the native language formats from `scripts/language_formats.py`, tags, Bazarr profiles),
`scripts/request-tags.sh` (Seerr tags on existing titles), `../Seerr/scripts/configure.sh` (anime profile).

| Rung | Audio | Score | How it is detected |
|---|---|---|---|
| 1 | original + German + English | 7000000 | `German DL` + `Language: Original + English` |
| 2 | original + English (non-English title) | 5000000 | `Language: Original + English` (own CF: English, the original, and a language other than the original all present); anime also `Anime Dual Audio` 5000000 (title says "Dual Audio"); 0 in the English-original profiles |
| 3 | original + German | 2000000 | TRaSH `German DL` (German + original audio), `German DL (undefined)`; the Swiss German profile keeps 20000 |
| 4 | original only | 0 | untagged releases count as the original language |
| + | each further language | +10 | `Extra audio: <language>`, tie-breaker below audio, group and streaming ([Release scoring](#release-scoring)) |
| rejected | dub only (`German`, `Dubs Only`), no original track (`Language: Not Original`), every unwanted format | −50000000 | Radarr additionally keeps profile language `Original` (rejects a release without the original audio, source-checked in Radarr 6: a multi-language release passes if the original is among its languages); Sonarr has no profile language, the CF does it |

- Rungs, gaps and the reasons for the numbers: [Release scoring](#release-scoring). HDR ranks above the rungs; season pack (Sonarr), audio, group and streaming tiers rank above the extra languages.
- Anime: `Anime Dual Audio` and `Language: Original + English` add up when both match (10000000), so a Japanese + English "Dual Audio" release beats a Japanese + German + English one named only "Multi" (7000000). Rare, accepted.
- Subtitle sync (Bazarr, set by `scripts/language-policy.sh`): every downloaded subtitle is synced (ffsubsync) to the original-language audio track, with frame-rate correction (fixes the slowly growing lag of subtitles made for 24 vs 23.976 fps). One title by hand: Bazarr → the title → subtitle → Sync, or `PATCH /api/subtitles?action=sync&reference=a:<n>`.
- English originals: a custom format cannot see that the original *is* English, so `Language: Original + English` would fire on any extra dub (English + Portuguese). English-original titles therefore use the twin profiles `… - English original` (Radarr and Sonarr, 4K default and Any quality), identical except that CF at 0: English + German 2000000 › English (+ any dub) 0; extra languages count there too.
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
- 2160p German scene releases often omit `HDR` in the name (`GERMAN.DL.2160P.UHD.BLURAY.HEVC-UNDERTAKERS`): since 2026-10-26 they are accepted as SDR 4K (ranked below a marked HDR release of the same quality, replaced by one later).
- Upgrade until score 25000000 = 2160p HDR + original + English reached (HD and anime: 5000000 = original + English). A release that also has
  German wins at grab time but is not chased afterwards.
- The TRaSH German guide uses profile language `Any` and accepts `German` dub-only
  (+10000); this setup deliberately does not.
- Anime: Seerr sends everything TMDB calls anime to the Sonarr profile
  **Anime - Bluray + WEB 1080p** with series type `anime`, same root folder `/data/media/tv`
  (one Jellyfin **Shows** library; a separate anime root would mean a second library
  for no gain). TRaSH `[Anime] Remux-1080p` qualities and group tiers, `[Streaming
  Services] Anime` (CR, HIDIVE, …), Anime Raws / LQ groups / `Dubs Only` rejected,
  TRaSH's minimum score 100 (no-name groups are skipped). Anime films: pick the Radarr
  anime profile by hand.
- Tags: Radarr and Sonarr auto-tagging sets `lang-xx` (every language with `subs = yes` in `scripts/languages.tsv`) from the title's original language on add and on every refresh, except while a `subs-<language>` tag is present (see [Per-request audio and subtitles](#per-request-audio-and-subtitles)); Bazarr maps the tag to the matching subtitle profile. English originals carry no tag and keep **English + German**. New language: add a row to `scripts/languages.tsv` and re-run the script.
- Not built: **extra audio tracks by merging two downloads** (download EN+DE and
  EN+ES, copy the Spanish track into the first file). No maintained tool does this
  (Tdarr/Unmanic transcode, they do not add languages); it would be a bespoke
  mkvmerge pipeline with two grabs per title, audio offsets that differ between
  releases (different cuts, intros, frame rates), double disk and usenet use, and
  a file Radarr cannot account for. Multi-audio releases (`German DL`, `MULTi`,
  `Dual Audio`) are the standard way to get the extra lanes, and the scores above
  grab them whenever they exist.

Verified 2026-10-19 on the scale before 2026-10-26 (`GET /api/v3/parse?title=…`, never grabs; a title without a
language tag is parsed as the original language; current scores: [Release scoring](#release-scoring)):

| Title | Radarr (Knives Out, English) | Sonarr (Money Heist, Spanish) | Sonarr (Frieren, anime, Japanese) |
|---|---|---|---|
| untagged `1080p.NF.WEB-DL` / `CR.WEB-DL` | 0 (English) | 75 (Spanish) | 306 (Japanese, Anime Web Tier 04 + CR) |
| `German.DL` | 28500 (German + English = both rungs) | 20075 | 20000 |
| `German.ML` (German + original + English) | 28500 | **28075** | — |
| `English.Spanish` / `Dual.Audio` | — | 8075 | 8000 |
| `German` only | −99500 (`German`, `Not Original`) | −99925 | −100000 |
| `English` only (non-English title) / `English.Dubbed` | — | −49925 | −100000 (`Dubs Only`) |

English-original profile, Knives Out (2160p Bluray, same encode, scale before 2026-10-26):

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

Tags picked in Seerr (request -> Advanced -> Tags) or set in Radarr/Sonarr. Defined in `scripts/languages.tsv`, created by `scripts/language-policy.sh`. Tags of a request for a title that is already in Radarr/Sonarr are copied over by `scripts/request-tags.sh` (below).

| Tag | Meaning | How |
|---|---|---|
| `audio-<language>` | the release **must** contain that audio language; not grabbed otherwise. Normal ranking on top; `audio-swiss-german` also switches to its own profile ([Swiss German](#swiss-german)) | tag-scoped Release Profile `audio-<language>: <Language> audio required`, required term = the regex in `languages.tsv`, matched against the release name |
| `subs-<language>` | Bazarr also fetches subtitles in that language (on top of EN + DE + original) | hook adds `bz-<original>-<requested…>` and creates the Bazarr profile `English + German + <Original> + <Requested…> (requested)` bound to it |
| `lang-xx` | set automatically (original language), do not pick | auto-tagging |
| `bz-…` | set by the hook, do not pick | one per subtitle combination |

| Code | Language | `audio-` | `subs-` |
|---|---|---|---|
| `gsw` | Swiss German | ✓ (name match, see below) | – (no providers carry it; `de` is always fetched) |
| `de`, `en` | German, English | ✓ | – (always fetched) |
| `fr` `it` `es` `ja` `ko` `pt` `nl` `ru` `sv` `da` `nb` `zh` `pl` `tr` | | ✓ | ✓ |
| `fi` `cs` `hu` `el` `ar` `he` `uk` | | – | ✓ |

Friendly names (docs only: Radarr/Sonarr tag labels allow only `a-z 0-9 -`, Seerr shows the label):

| Tag | Friendly name |
|---|---|
| `audio-swiss-german` | Swiss German (Mundart) — hard filter, only this audio, everything else is skipped |
| `audio-german` / `audio-english` | German / English |
| `audio-french`, `audio-italian`, … | French, Italian, … (the language name) |
| `subs-french`, `subs-italian`, … | French subtitles, Italian subtitles, … |
| (none) `audio-orig` "Original language" | always required, no tag |
| (none) `subs-de` / `subs-en` "German / English subtitles" | always fetched, no tag |
| (none) `subs-forced` "Forced subtitles only" | forced EN + DE are always fetched, no tag |

- **Tag labels are the language name, lowercase with hyphens, from `languages.tsv` column 2** (`audio-swiss-german`, `audio-german`, `subs-french`, …); the `Code` column below is only the Bazarr/`lang-xx`/`bz-` code. The `bz-<codes>` and `lang-xx` tags stay code-based.
- Several `audio-` tags = all of them required (each release profile must match). Several `subs-` tags = one combined Bazarr profile.
- `audio-<original>` is dropped on add: the original audio is required anyway, and release names of e.g. English films rarely say `English`.
- Limits of name matching (release profiles see only the name, not the tracks):
  - **Swiss German** has no Radarr/Sonarr language. Real names (NZBgeek/NzbPlanet search, 2026-10-20 and 2026-10-23): `Swissgerman`, `SwissGerman`, `SWISSGERMAN`, `Swiss.German`, `SWISS.German`, `Schweizerdeutsch`, `Schweizerduetsch`, `Schwyzerduetsch`, `Schwyzerd&uuml;tsch`. The pattern is deliberately tolerant (spellings: [Swiss German](#swiss-german)). Nearly only Swiss productions are released that way; Radarr parses them as German.
  - `audio-english` also accepts `Dual Audio`; `audio-french` accepts `VFF/VFQ/VF2/TRUEFRENCH` but not plain `MULTi` (it does not say which languages) and not `VOSTFR` (French subtitles).
  - A language word in the film title (*The French Connection*) satisfies the term.
- **`audio-<language>` is a hard filter, not a score.** The release profile rejects every release without the term before scoring, so resolution cannot outrank it ("Does not contain one of the required terms").
- **Swiss German**: see [Swiss German](#swiss-german) (own profile, also for a Swiss German dub of a foreign film).
- **Tag labels are fixed:** Radarr/Sonarr only allow `a-z`, `0-9` and `-` in a tag label (API: "Allowed characters a-z, 0-9 and -"), so friendly names like "Swiss German (Mundart)" cannot be tag labels, and the scripts match on the `audio-<language>` / `subs-<language>` labels. There are no `audio-orig` or `subs-forced` tags (original audio is always required, forced subtitles are always fetched).
- **Seerr sends tags only when it adds a new title**; for one already in Radarr it logs "Movie is already monitored" and drops them. `scripts/request-tags.sh` (timer every 2 min while beefy is awake) copies them onto the title, runs the hook and searches; each request once (`~/.local/state/movie-downloads/request-tags.done`), so a tag removed by hand stays removed.

  ```bash
  cd ~/Projects/Docker/Movie-Downloads/systemd
  sudo install -m 0644 request-tags.service request-tags.timer /etc/systemd/system/
  sudo systemctl daemon-reload && sudo systemctl enable --now request-tags.timer
  journalctl -u request-tags -n 20     # "radarr: request 9, The Ring Thing: tags 13 added, searched"
  ```
- Tags added to an existing title later: re-run `scripts/language-policy.sh` (runs the hook with `LP_TAGS_ONLY=1` for every title with a `subs-` tag, then rescans those titles in Bazarr: a profile changed by the sync keeps its old missing-subtitles list until then).
- The hook reads the Bazarr key from `secrets/hook/bazarr.apikey` (gitignored, written by the script, mounted at `/secrets:ro`) and reaches `http://bazarr:6767`. Rebuild: `mkdir -p secrets/hook` before the first `docker compose up` (otherwise Docker creates it root-owned), then run the script.
- Tests: `tests/test_language_policy_hook.sh` (hook), `tests/test_request_tags.sh` (Seerr tags), `python3 tests/test_audio_terms.py` (patterns against real names, Swiss German CF = `languages.tsv`). Live: `scripts/swiss-german-check.py`.

#### Original audio is the default track

Jellyfin plays the user's preferred language, else the track flagged *default* in the file. German DL releases flag the German dub, so a user without the original language in their preferences got the dub. User preferences stay untouched; `scripts/original-audio.py` fixes the files instead.

- Every `.mkv` of Radarr / Sonarr: the one audio track in the original language (from the app) gets `flag-default` and the name `<existing name> (Original)` if it contains the language name, else `<Language> (Original)`, all other audio tracks lose `flag-default` (`mkvpropedit`, header edit in place, no remux, mtime restored). Idempotent.
- Skipped and reported: not mkv, link count > 1 (still seeding, edited on a later run), no original-language track (dub only, `und`), several original-language tracks are not a skip: commentaries (name or flag) are dropped, then the track already flagged default wins, else the first; commentary tracks get `flag-commentary`.
- Jellyfin: the changed items are refreshed (FullRefresh) afterwards.
- `systemd/original-audio.timer`: daily 04:20 in the maintenance window (sweep of the whole library, no boot trigger); `systemd/original-audio-import.path` runs `original-audio.py --queue` for the files Radarr/Sonarr queue on import/upgrade (Connect script `scripts/original-audio-hook.sh`, registered by `scripts/language-policy.sh`; a file still seeding, i.e. hard-linked, is dropped and left to the sweep). Tests: `python3 tests/test_original_audio.py`, `tests/test_original_audio_hook.sh`. Install as above (`sudo install -m 0644 original-audio.service original-audio.timer /etc/systemd/system/`, `enable --now`). Needs `mkvtoolnix` (`sudo apt-get install -y mkvtoolnix`).
- By hand: `scripts/original-audio.py --dry-run`, then without `--dry-run`. Tests: `python3 tests/test_original_audio.py`.

### Swiss German

`audio-swiss-german` = Swiss German audio is mandatory and outranks quality: a Swiss German DVD is taken over a High German 4K; no Swiss German release = nothing is grabbed.

| Part | What |
|---|---|
| Release profile `audio-swiss-german: …` | hard filter on the raw release name (spellings below; regex in `languages.tsv`) |
| Profile **Swiss German (Mundart) only - any quality** (Radarr and Sonarr) | language **Any** (Radarr; Sonarr profiles have no language: `German` and `Language: Not Original` are 0 there, so a Swiss German dub of a foreign film or series passes), all qualities Unknown, SDTV … Bluray-2160p in **one group** (a Swiss German release replaces any non-Swiss file, also a better one), cutoff Swiss German + 2160p |
| Scores in that profile | `Audio: Swiss German` 1000000 · `Resolution: 2160p/1080p/720p/SD/DVD` 200000/150000/100000/50000/50000 · `German`, `Language: Not Original` 0 · LQ/BR-DISK/upscaled/DV w/o HDR … −2000000 · x265 HD and SDR 4K −50000 (one resolution step, taken instead of rejected: Swiss German releases are rare) · the rest as in the default profile |
| Hook (on add, and on tags added later) | tag present → move to the profile, whatever was picked; added on the profile without the tag → tag added; profile language back to Any |
| `request-tags.sh` (timer) | Seerr tags on titles already in Radarr/Sonarr; also puts the Radarr profile language back to Any (Recyclarr resets it to Original whenever it updates that profile) |
| File name | `Audio: Swiss German` is renamed into the file name (`{Custom Formats}`), so Jellyfin shows it |

Matched spellings (case-insensitive; one regex in `languages.tsv`, the same in the release profile and `Audio: Swiss German`; `tests/test_audio_terms.py` has 55 positive and 19 negative names):

| Kind | Matches |
|---|---|
| `Swiss` + `German`/`Ger` | `Swiss German`, `Swiss.German`, `Swiss-German`, `Swiss_German`, `Swissgerman`, `SwissGer`, `SWISS.GER` |
| `Schweizer`/`Schwyzer`/`Schwizer`/`Schwiizer`/`Shwizer`/`Swizer` + `deutsch`/`dütsch`/`tütsch` | separator space `.` `_` `-` or none: `Schweizerdeutsch`, `Schwyzerdütsch`, `Schwiizertüütsch`, `Schwiizer.Dütsch`, `Schwyzer-Duetsch`, `Schwizertütsch` |
| ü written as | `ü`, `ue`, `u`, `y`, doubled (`düütsch`), broken encodings `Ã¼` / `Ã` + any char, `?`, `�`, `&uuml;`, `%C3%BC`, or missing (`Schwyzerdtsch`: Radarr drops `?` before custom-format matching) |
| Alone | `Schwizer`, `Schwiizer`, `Shwizer`, `Swizer` (dialect spellings), `Mundart`, `gsw`, `de-CH` / `de_CH` / `deCH` |
| Not matched | `Swiss` alone (*Swiss Army Man*, *The Swiss Family Robinson*, `Swiss.Cheese`, `Swiss.Miss`), `Swiss.Germany`, `Schweiz`, `CH`, `German`, `Schwyzer` / `Schweizer` alone (place name, surname, *Die Schweizermacher*), `Schweizer.Deutschland`, `Mund Art`, `de.CH` |

- `Mundart` also matches other German dialects (rare in release names; accepted).
- Nothing found shows: Seerr keeps the request at *Requested/Processing* (not Available); Radarr/Sonarr → Wanted → Missing (or Cutoff Unmet when a non-Swiss file exists); Interactive Search lists "Does not contain one of the required terms". `scripts/swiss-german-check.py` prints "Waiting for a Swiss German release: …"; after 7 days a Telegram: [Swiss German not found alert](#swiss-german-not-found-alert).
- The weekly cutoff search keeps looking for titles with a non-Swiss file (cutoff unmet).
- Swiss German only in the description, not the name: [Description scout](#description-scout).
- `Audio: Swiss German` only ranks, it is not the filter: Radarr matches custom formats against the name minus the parsed film title, so in `Achtung.fertig.WK.Swissgerman.2013` the word counts as title and the format misses (the release profile still passes it; it then ranks by resolution like a non-Swiss file and may not replace an existing non-Swiss file).
- Sonarr: the same profile, release profile, hook and scores since 2026-10-23 (before: release profile only, a Swiss German dub of a foreign series was rejected by `Language: Not Original`).

```bash
./scripts/swiss-german-check.py      # Radarr and Sonarr (or: … sonarr): profile, scores, release profile, hook, timer, tagged titles, ranking; read-only
```

Verified 2026-10-23 (Interactive Search, `GET /api/v3/release?movieId=`, never grabs):

| Title (original) | Approved | Rejected (best non-Swiss) |
|---|---|---|
| *The Ring Thing* (German) | none: all 5 releases from the configured indexers are High German / Polish (re-checked with the tolerant pattern; a `Schwyzerd…` release is not on any of them) | `…German.2004.AC3.DVDRiP.x264-IND` (the file on disk), `…German.DL.COMPLETE.PAL.DVD9` — "Does not contain one of the required terms" |
| *The Divine Order* (German) | `…2017.Swissgerman.1080p.BluRay.x264-ETM` 1170000 › `…Swissgerman.German.720p.BluRay` 1120000 › `…Swissgerman.2017.AC3.BDRiP` 70750 | `The.Divine.Order.2017.GERMAN.1080p.BluRay`, German DL remuxes |
| *Babe* (English: Swiss German dub) | `…1995.German.Schwyzerduetsch.Oesterreichisch.DVDRip.x264` 1050000 | `Babe.1995.UHD.BluRay.2160p.DDP.5.1.DV.HDR.x265` 203950 — only for the missing Swiss German |

Hook live: both test films added on the default profile with the tag → `movie 15: profile 7 -> 13 (audio-swiss-german)`; removed again afterwards (no files).

Sonarr, verified 2026-10-23 (series added unmonitored with the tag, no search; `GET /api/v3/release?seriesId=&seasonNumber=`, never grabs; removed afterwards):

| Series | Hook | Approved | Rejected |
|---|---|---|---|
| *Tschugger* (2021, Swiss German original) | `series 8: profile 7 -> 13 (audio-swiss-german)` | none on the name: all 129 releases are High German / English names | `Tschugger.2021.S01.German.DL.EAC3.1080p.NF.WEB.H264-ZeroTwo`, `…S01.1080p.NF.WEB-DL…-playWEB` — "Does not contain one of the required terms" |
| *The Bachelor (CH)* (2012) | `series 9: profile 7 -> 13` | `Der.Bachelor.CH.S08E01.SWISSGERMAN.720p.WEB.x264-BTGTV` 1120000 (Swiss German + German DL + 720p) | the EZTV/1337x copies of the same names: "Unknown Series" (no alias `Der Bachelor CH` in Sonarr, they carry no TVDB id) |

`scripts/swiss-german-check.py`: all checks passed for both apps.

### Description scout

`audio-<language>` matches only the release **name**. `scripts/description-scout.py` (timer daily 04:14, in beefy's maintenance window; a request's search is done by `request-tags`) also reads the **description** of torrent releases, for Radarr and Sonarr: *The Ring Thing* TPB `The Ring Thing (2004) [XVID]` says `Language: Schwyzerdütsch` only in its description.

| Step | What |
|---|---|
| Titles | Radarr movies: monitored, no file; Sonarr series: monitored, with missing monitored episodes (`/wanted/missing`); both with a scouted `audio-` tag (`DS_LANGUAGES`, default `gsw`) |
| Search | Prowlarr `/api/v1/search`, torrent indexers only. Movies: `type=movie`, category 2000, `<title> <year>`, also the original title; by IMDb id only on indexers that support it (none today). Series: `type=search`, category 5000, the title without a `(CH)`-style suffix and its alternate titles (max 3); by TVDB id only on indexers that support it |
| Same title? | imdb id equal when the indexer or the description names one. Movies: Radarr `/parse` maps the name to that movie, parsed year = year or secondary year. Series: Sonarr `/parse` maps the name to that series (by name: a name without a Sonarr alias, e.g. `Der Bachelor CH` for *The Bachelor (CH)*, is skipped), a year in the name = the series' year, and at least one of its episodes (episode or season pack) is missing. Otherwise skipped, never fetched |
| Description | TPB and Knaben→TPB: `apibay.org/t.php?id=` field `descr`; 1337x: the page's description block only. Both through FlareSolverr (VPN namespace). Others: skipped |
| Match | the same `languages.tsv` regex, after decoding HTML entities and broken umlauts; a match after `Subtitles`/`Untertitel` on the same line does not count |
| Push | seeders ≥ 1 → `POST /api/v3/release/push` to Radarr (tmdb/imdb id) or Sonarr (tvdb id) with the magnet, name + `.<Language>.DESC` (`The Ring Thing (2004) [XVID].Swissgerman.DESC`; the release profile and `Audio: Swiss German` accept it, `.DESC` marks it). The app decides (profile, quality, wrong-film guard, which episodes) |
| Limits | 2 pushes and 15 description fetches per run, both apps together (`DS_MAX_PUSH`, `DS_MAX_FETCH`); each release pushed once; a failing fetch or push is retried 3 times, then dropped; a Prowlarr API error skips the run, a Radarr or Sonarr one skips that app; seeders 0 = not pushed, checked again next run (description not fetched again). `DS_APPS=radarr` scouts one app only |
| Not covered | usenet (no descriptions in NZB results; MULTi releases: [NFO scout](#nfo-scout)); series names Sonarr cannot map by name |

| File | |
|---|---|
| `~/.local/state/movie-downloads/description-scout.log` | every decision, timestamped (also `journalctl -u description-scout`) |
| `~/.local/state/movie-downloads/description-scout.json` | per release (btih): `match`/`no`/`failed`, pushed, approved. Delete an entry to re-check it |

```bash
cd ~/Projects/Docker/Movie-Downloads/systemd
sudo install -m 0644 description-scout.service description-scout.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now description-scout.timer
# dry run, one movie (never pushes, state unchanged); FlareSolverr has no published port, the unit resolves gluetun's IP as root first
sudo ../scripts/description-scout.py --resolve-flaresolverr /tmp/fs && DS_FLARESOLVERR=$(cat /tmp/fs) ../scripts/description-scout.py --dry-run --tmdb 10248
DS_FLARESOLVERR=$(cat /tmp/fs) ../scripts/description-scout.py --dry-run --tvdb 412881   # one series; with --dry-run an id also takes an unmonitored title
python3 ../tests/test_description_scout.py
```

- Another language: its `audio-` regex in `languages.tsv` must match the language name without spaces (`French`), then `Environment=DS_LANGUAGES=gsw,fr` in the service. Common words (`French`, `German`) appear in many descriptions as subtitle or menu lists: only add rare ones.
- Verified 2026-10-23 (series, dry run): *Tschugger* (unmonitored, tagged) → 10 torrent results; `Tschugger - S01 (Eng subs)` (TPB) and `Tschugger   season 1 (Eng subs)` (1337x) say Swiss German in the description → would push `….Swissgerman.DESC` to Sonarr with tvdb id 412881 (30 and 6 seeders); the `SUBBED XviD-AFG` episodes and `S01-S02.1080p.WEB-DL.PlaySuisse` do not. *The Bachelor (CH)*: 369 results, none mapped by name, nothing fetched.
- Verified 2026-10-23: *The Ring Thing* → TPB 3872789 description matches, seeders 0, not pushed. Payload checked with a push Radarr had to reject (original name, seeders 0): mapped to the movie, indexer `The Pirate Bay (Prowlarr)`, rejected for missing term, seeders < 1 and `Unknown is not wanted in profile`: an `[XVID]` name without source parses as quality Unknown, which the Swiss German profile does not allow.

### NFO scout

Radarr/Sonarr read languages only from the release name, so `MULTi` = the original only ([Release scoring](#release-scoring)). `scripts/nfo-scout.py` (timer daily 04:16, in beefy's maintenance window) reads the audio languages of MULTi-only usenet releases from the indexer and pushes them with the languages in the name. Not the per-indexer "Multi Languages" setting (owner).

| Step | What |
|---|---|
| Titles | Radarr: monitored movies missing or below the cutoff (`/wanted/missing`, `/wanted/cutoff`); Sonarr: monitored series with such episodes; `audio-swiss-german` titles skipped ([Description scout](#description-scout)) |
| Search | Prowlarr `/api/v1/search`, only the usenet indexers below; by IMDb id (movies) / TVDB id (series) where supported, else title (+ year). Treasure Maps only below 70 % of its window and max 10 searches/day |
| Candidate | name has `MULTi` / `Multi-Audio`, `/parse` gives fewer than 2 languages, same title (the description scout's guards: `/parse` maps it, year); best quality first |
| Source | NzbPlanet, Treasure Maps: `t=getnfo` (NFO text in the RSS item); NZBgeek: `t=details` `language` attributes (no getnfo: it answers with its newest releases). Direct to the indexer (Prowlarr's proxy: "No such function (getnfo)"), base URL + key from Prowlarr's DB, never logged; own user agent (Treasure Maps rejects a bare `Prowlarr`) |
| Parse | audio only: values of `Audio` / `Language` / `Sprache` / `Ton` / `Idioma` / … keys and their indented continuation lines, MediaInfo `Audio #n` blocks. Dropped: `Subs` / `Untertitel` / `Sous-titres` / MediaInfo `Text`, the rest of a list after a subtitle word, keys like `Audio/Subs`. Names in EN/DE/FR/ES/IT, ISO 639-2 (`ger`, `eng`, `spa`), `VFF`/`VFQ` = French, upper-case `DE`/`EN`/`FR`/`IT` |
| Push | ≥ 2 languages incl. the original → languages + `.NFO` after the marker: `The.Secret.In.Their.Eyes.2009.MULTi.French.Spanish.NFO.1080p.BluRay.x264-FHD`. `/parse` of the new name: exactly those languages, same quality, group and title, score ≥ 0 and higher than before, else not pushed. Extras the parser cannot name (Czech, Estonian) are dropped; original/English/German not nameable = no push. Same Prowlarr download link and indexer; the app decides |
| Dub-only | NFO / details list ≥ 2 languages but not the title's original (nameable, `Spanish (Latino)` counts as Spanish) → the release is written to `<app config dir>/dub-only.json` (`/srv/appdata/radarr`, `/srv/appdata/sonarr`; guid, exact title, indexer, languages, original, TMDB/TVDB id, time), read by the [Wrong-film guard](#wrong-film-guard). Also in a dry run; entries expire after 30 days. Never for releases without NFO data or with one language |
| Not covered | NFOs without an audio list, a single listed language, Swiss German |

| Env (unit) | Default | |
|---|---|---|
| `NS_MAX_PUSH` | 3 | pushes per run, both apps |
| `NS_MAX_FETCH` | 200 | NFO/details calls per indexer per run |
| `NS_TM_MAX_DAY` | 5 | Treasure Maps NFO calls per rolling 24 h (2026-10-26: it refused getnfo after 6 in minutes, searches kept working) |
| `NS_TM_MAX_PCT` | 70 | no Treasure Maps search or NFO call from searches used (bar, `/srv/appdata/indexer-bar/usage.json`) + own NFO calls ≥ 70 % of its limit; bar missing or older than 15 min = none |
| `NS_TM_MAX_SEARCH_DAY` | 10 | Treasure Maps searches per 24 h |
| `NS_CALL_DELAY` | 1 | seconds between calls to one indexer |
| `NS_APPS` | `radarr,sonarr` | one app only: `radarr` |

- NFO calls bypass Prowlarr: the bar does not count them (hence the own Treasure Maps budget).
- Refused (newznab 1xx / 5xx, e.g. `500 Request limit reached`): no calls to that indexer for 24 h. Not that release (`202 No such function`, a feed): indexer unsupported for 30 days. Failing call: 3 tries, then dropped.
- Each release read once (also in a dry run, so the real run does not fetch again) and pushed once.

| File | |
|---|---|
| `~/.local/state/movie-downloads/nfo-scout.log` | every decision (also `journalctl -u nfo-scout`) |
| `/srv/appdata/{radarr,sonarr}/dub-only.json` | dub-only list for the guard: in the app's `/config` (already mounted, read by the guard like `guard-events/`), no new mount, no restart; scripts/ is the git-tracked read-only code mount, not for state. Env `NS_RADARR_DUB_ONLY` / `NS_SONARR_DUB_ONLY` |
| `~/.local/state/movie-downloads/nfo-scout.json` | per release (`<indexer>\|<id>`): `langs`/`none`/`skip`/`failed`, languages, pushed, approved; call counters, unsupported/refused indexers. Delete an entry to re-check it |

```bash
cd ~/Projects/Docker/Movie-Downloads/systemd
sudo install -m 0644 nfo-scout.service nfo-scout.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now nfo-scout.timer
../scripts/nfo-scout.py --dry-run --tmdb 25376     # one movie, never pushes (--tvdb for a series)
python3 ../tests/test_nfo_scout.py
```

Verified 2026-10-26 (dry run, 16 movies + 1 series, 74 MULTi-only releases; scores on the scale before extras became 10):

| Title | Release (indexer, source) | NFO audio | Score |
|---|---|---|---|
| *The Secret in Their Eyes* | `…2009.MULTi.1080p.BluRay.x264-FHD` (NZBgeek, details) | French, Spanish | 0 → 20000 |
| *A Separation* | `A.Separation.2011.MULTi.1080p.BluRay.x264-ROUGH` (NZBgeek, details) | French, Persian | 0 → 20000 |
| *The Hunt* | `Jagten.2012.MULTi.1080p.VFi…-XSHD` (NZBgeek, details) | Danish, French | −49989250 (French only = not original) → 20750 |
| *The Rivals of Amziah King* | `…2025.MULTi.CA.HDR.2160p.WEB.H265-SUPPLY` and 4 more (Treasure Maps, getnfo) | French, English | 0 → 10000 |
| *I'm Still Here*, *Blue Ruin*, *Kneecap*, *The Brutalist*, *Money Heist* (41 episodes) | NZBgeek / NzbPlanet | not named | not pushed |

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

### Subtitle check

`scripts/subtitle-check.py` (Python stdlib only): broken text encoding in subtitle files. Found 2026-10-27: OpenSubtitles.com upload 955197 (*The Hunt*, 2012) had every ♪ as `в™Є` / `В™Є` (valid UTF-8, broken at the uploader); 1 of 62 files then.

| | |
|---|---|
| Finds | not UTF-8, U+FFFD, C1 control characters, mojibake (UTF-8 read as cp1252 / latin-1 / cp1251 and saved again: `в™Є` `В™Є` → ♪, `Ã¤` → ä, `Г¤` → ä, `â€™` → ’, also twice over) |
| Repairs | only when unambiguous: each sequence turned back into bytes must decode as UTF-8 to an expected character (Latin-1 letter or symbol after `Â` `Ã` `В` `Г`, typographic punctuation or ♪ after `â` `в`, emoji), and the file is written only when nothing suspicious is left. Cyrillic-language files: no cp1251 two-byte repairs |
| Reports only | everything else: not UTF-8, U+FFFD, leftovers (`â€` alone), C1 characters, UTF-16 |
| Skips | binary VobSub `.sub`, `.idx`, symlinks |
| Original | `/srv/appdata/bazarr/subtitle-check/backup/<date>_<time> <folder> -- <file>`: outside the library, so Jellyfin and Bazarr never see it; kept |
| Log | `/srv/appdata/bazarr/subtitle-check/hook.log` (every hook run) |
| Digest | every repair and finding: `…/subtitle-check/events/*.json` → [Queue watchdog](#queue-watchdog) → fastpi 06:30 digest (`subtitle_repaired`, `subtitle_suspect`) |
| Hook | Bazarr post-processing, no score threshold: `/scripts/subtitle-check.py "{{subtitles}}" "{{provider}}" "{{subtitle_id}}"`; runs after ffsubsync for every downloaded, upgraded and uploaded subtitle; mount `./scripts:/scripts:ro` |
| Setup | `scripts/subtitle-check-setup.sh` (idempotent: state dirs owned by Bazarr's uid 65104, group buntu; Bazarr settings through `/api/system/settings`) |
| By hand | `sudo docker exec movie-bazarr /scripts/subtitle-check.py [--dry-run] "/data/media/…/<file>.en.srt"` |
| Tests | `python3 tests/test_subtitle_check.py` (fixtures in `tests/fixtures/subtitles/`) |

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
  03:58–04:45 window (`../Jellyfin/scripts/night-tasks.py`, see the Jellyfin README): Scan Media Library daily 04:10, Trickplay
  04:24, Chapter Images 04:30, Media Segment Scan 04:33.

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
| **4K HDR - WEB-2160p (1080p fallback)** — default | everything | WEB 2160p › WEB 1080p | TRaSH "WEB-2160p (Combined)": the TV counterpart of the movie profile. WEB, not Bluray: series are released on WEB first and TV Bluray encodes are rare. 2160p SDR allowed and `DV (w/o HDR fallback)` rejected like the movies, Golden Rule UHD (x265 below 2160p only with HDR) |
| WEB-1080p | space saver, per series | WEB 1080p | plain TRaSH WEB-1080p, Golden Rule HD (x264) |
| Any quality - old & rare (upgrades to 4K) (+ `- English original` twin) | old/niche series, set automatically on add or picked per request | WEB 2160p › Bluray-2160p › WEB 1080p › Bluray-1080p › HDTV-1080p › WEB 720p › Bluray-720p › HDTV-720p › Bluray-576p › WEB 480p › Bluray-480p › DVD › SDTV › Unknown, upgrade until WEB 2160p | same scores as the default; see the movie [Old and rare titles](#old-and-rare-titles) note |
| Anime - Bluray + WEB 1080p | anime, set by Seerr (series type `anime`) | Bluray-1080p (remux or encode) › WEB 1080p › 720p … | TRaSH `[Anime] Remux-1080p`: anime group tiers, `[Streaming Services] Anime`, Anime Raws / LQ groups / Dubs Only rejected, no Golden Rule, minimum score 100 |
| Swiss German (Mundart) only - any quality | series tagged `audio-swiss-german`, set by the hook | one group: Unknown, SDTV … Bluray-2160p, ranked by resolution scores | Swiss German + 2160p (1200000); same as the movie profile, see [Swiss German](#swiss-german) |

Shared by all: the [Release scoring](#release-scoring) scores (HDR 20000000, `Language:
Original + English` 5000000, `German DL` 2000000, `Season Pack` 2000 (not anime), `Extra audio: <language>` 10, `Language: Not Original` and every reject −50000000),
`[Streaming Services] HD/UHD boost` + `General`, `[Unwanted] Unwanted Formats`,
TRaSH series size limits (one table per instance, `series`). The six stock Sonarr
stock profiles were deleted 2026-10-22 (unused; they only cluttered the Seerr dropdown).

#### Radarr / Sonarr parity

Every language, profile and delete feature works the same for movies and series (owner, 2026-10-23: "must be all the same for Sonarr as on Radarr").

| Feature | Radarr | Sonarr | Where |
|---|---|---|---|
| Swiss German profile (all qualities incl. Unknown in one group, resolution scores, cutoff Swiss German + 2160p) | ✓ | ✓ since 2026-10-23 | `recyclarr/recyclarr.yml`, `recyclarr/custom-formats/<app>/` |
| Profile language Any for that profile | ✓ (`language-policy.sh`, hook, `request-tags.sh`) | ➖ Sonarr profiles have no language; `German` / `Language: Not Original` 0 instead | |
| Tolerant Swiss German regex: release profile (hard filter) + `Audio: Swiss German` CF, both = `languages.tsv` | ✓ | ✓ | `language-policy.sh`, `tests/test_audio_terms.py` |
| `audio-` / `subs-` tags, hook switches the profile per tag (on add and for tags added later) | ✓ | ✓ (Swiss German move since 2026-10-23) | `language-policy-hook.sh` |
| Seerr tags onto existing titles | ✓ | ✓ | `request-tags.sh` |
| Remove from Radarr/Sonarr = only that app | ✓ | ✓ verified 2026-10-23 | Seerr |
| Clear Data wipe (torrents, SABnzbd, Jellyfin, files) | ✓ | ✓ verified 2026-10-23 | `clear-data-wipe.sh`, `title-wipe-hook.sh` |
| Description scout | ✓ | ✓ since 2026-10-23 (episodes and season packs) | `description-scout.py` |
| NFO scout (MULTi releases) | ✓ | ✓ new 2026-10-26 (episodes and season packs) | `nfo-scout.py`, [NFO scout](#nfo-scout) |
| Unknown quality in the old & rare profiles (+ twins) | ✓ | ✓ | `recyclarr/recyclarr.yml` |
| Wrong-film guard (finds unmapped queue items, history fallback, digest / Telegram) | ✓ year | ✓ series start year | `grab-guard.sh` |
| Queue watchdog (Telegram: blocked, check by hand, indexers; digest: the rest) | ✓ | ✓ new 2026-10-25 | `queue-watchdog.py`, [watchdog](#queue-watchdog) |
| Live check | ✓ | ✓ since 2026-10-23 | `swiss-german-check.py` |
| Swiss German not found alert (Telegram) | ✓ | ✓ new 2026-10-23 | `language-missing.py`, [alert](#swiss-german-not-found-alert) |
| Night search (new, cutoff-unmet) | ✓ movies | ✓ episodes | `night-search.py` |

Delete a title, verified 2026-10-23 with *Pioneer One* (TVDB 170551, added unmonitored without search) and fixtures that never download (a stopped magnet in category `sonarr`, recorded like a grab, plus an unrecorded decoy; a paused NZB in category `tv`, recorded): Seerr **Remove from Sonarr** → only the Sonarr entry gone, torrent, SABnzbd job and Seerr row kept; then **Clear Data** → within one cycle `torrent removed with files`, `SABnzbd queue entry … removed`, the stale Jellyfin item of the 2026-10-19 test removed, Seerr row gone; the decoy stayed.

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
grep -a 'language-policy-hook\|grab-guard\|title-wipe' /srv/appdata/radarr/logs/radarr.debug.txt | tail
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

Tag path, *Plan 9 from Outer Space* (1957, TMDB 10513), requested with tags `audio-german` + `subs-french`: hook `profile 7 -> 12 (old: 1957, English original)`, `bz-fr` added, Bazarr profile created. No grab, correctly: every non-German release `Does not contain one of the required terms: /\b(german|deutsch|ger|deu)\b/i`, the German DL ones are from LQ group `msd` (−30000), the German-only ones dub-only (−100000).

`subs-` on an existing title (*His Girl Friday*): `subs-french` → `bz-fr` (French is embedded, nothing missing); `subs-polish` → `bz-fr-pl`, profile `English + German + French + Polish (requested)` → `….pl.srt` from OpenSubtitles.com (score 90 %).

Delete (cleanup): Radarr delete with files → Connect → the Jellyfin item was gone 60 s later.

### Swiss German not found alert

A title tagged `audio-swiss-german` (movie or series) that still has no Swiss German release after 7 days sends **one** Telegram: `[FIRING] AudioLanguageNotFound` / `Swiss German not found: <title> (<year>)`.

| | |
|---|---|
| Counts | tagged, monitored, no file (series: missing monitored episodes), searched at least once, not downloading |
| Since | first seen waiting (first run: the date the title was added); a file, unmonitor, untag or delete resets it |
| Notified | since + 7 days (`LM_DAYS`); again only when the state changes: a new failed or blocklisted grab = a new `failed` label = one new Telegram |
| beefy | `scripts/language-missing.py` (timer `language-missing.timer`, daily 04:18 in the maintenance window) → `~/.local/state/movie-downloads/media-language.prom` (+ `language-missing.json` state) |
| fastpi | `Server/4-Security-Monitoring/security_scan.py` group `media` (with `av`, 04:20/05:40/06:40) pulls it over ssh into node-exporter's textfile dir as `media_language_beefy.prom` → rule `grafana/prometheus/rules/media-language.yml` → Alertmanager route `AudioLanguageNotFound` → receiver `telegram-once` (no resolved message) |
| Why this path | beefy has no Prometheus/Alertmanager and no exporter; fastpi already pulls beefy's ClamAV textfile this way (no new port, no firewall change, works while beefy sleeps: the file stays on fastpi, so the series never go stale) |
| Once | the alert holds for 5 days after notify, then ends silently; the route's repeat interval is 168 h. A pull missed 5 days in a row loses it; two missed days already fire `SecurityScanStale` (subject `media-beefy`) |
| Metrics | `media_language_missing_since_timestamp_seconds`, `media_language_missing_notify_timestamp_seconds` {app, title, language, failed}, `media_language_missing_last_run_timestamp_seconds` |
| Tests | `python3 tests/test_language_missing.py`; fastpi: `promtool test rules tests/media-language_test.yml`, `amtool config routes test alertname=AudioLanguageNotFound` → `telegram-once` |

```bash
cd ~/Projects/Docker/Movie-Downloads/systemd
sudo install -m 0644 language-missing.service language-missing.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now language-missing.timer
cat ~/.local/state/movie-downloads/media-language.prom    # what fastpi will see
```

Verified 2026-10-23: *The Ring Thing* (tagged, searched, no file) → `since` 2026-10-23 10:56 (added), `notify` 2026-10-30; fastpi query returns it, rule `AudioLanguageNotFound` healthy and inactive until then.

## Completion scan

Every finished download is scanned by ClamAV: SABnzbd post-processing script and qBittorrent "run on torrent finished" call `../Antivirus/hook/clamav-hook.py`, which streams executables, archives and unknown files to `clamav:3310` before the job ends (the `clamav` container from `../Antivirus` joins this bridge) and hands media files to the background queue (`clamav-queue`, 1 file at a time), so SABnzbd post-processing no longer waits ~2 min per episode.

| | |
|---|---|
| Setup | `scripts/clamav-hooks.sh` (SABnzbd + qBittorrent APIs, idempotent); compose mounts `../Antivirus/hook` → `/clamav-hook:ro` and `/var/lib/clamav-scan/textfile` → `/clamav` in `sabnzbd` and `qbittorrent` |
| Failed SABnzbd job | not scanned, PP ends at once, Sonarr/Radarr see the failure and search again |
| Hit | file → `/data/quarantine` (`/srv/.disks/ssd-hot/quarantine`), SABnzbd job failed (no import), torrent stopped + tagged `infected` |
| Background hit | already imported: library file and seeding copy removed, file quarantined, torrent stopped + tagged; `sudo docker logs clamav-queue` |
| clamd down | job passes, the nightly scan covers it |
| Test | `docker exec -u abc movie-sabnzbd /clamav-hook/clamav-hook.py /data/usenet/complete/movies/<job>` |

Details, restore and metrics: `../Antivirus/README.md`.

## Network topology

```
            ┌─ gluetun (ProtonVPN) ───────────────────┐
 LAN :8080 ─┤  qBittorrent    Prowlarr    SABnzbd     │   no leak if tunnel drops
 LAN :9696 ─┤  (no ports/networks of their own)       │
 LAN :8081 ─┤                                         │
            └───────────┬─────────────────────────────┘
                        │ movie-downloads bridge, 172.28.10.0/24
       ┌────────────────┼────────────────┬──────────────┐
   Radarr :7878   Sonarr :8989   Bazarr :6767   vpn-status :8087   clamav :3310   (+ Seerr :5055 from ../Seerr)
```

Every download client is inside the tunnel (SABnzbd since 2026-10-23, like `sabnzbd` behind `gluetun_nzb` on fastpi): trackers, peers, indexers and the usenet provider only see the VPN IP. Radarr, Sonarr and Bazarr only talk to the LAN and metadata APIs.

| | |
|---|---|
| Kill switch | gluetun's firewall: `OUTPUT` policy `DROP`; allowed only `tun0`, the WireGuard endpoint (UDP 51820, gluetun itself) and `FIREWALL_OUTBOUND_SUBNETS` (bridge only since 2026-10-24; replies to fastpi leave via eth0 because gluetun routes its own eth0 address there). Tunnel down = no internet for qBittorrent, Prowlarr and SABnzbd (bridge still reachable, so the WebUIs and Radarr keep working) |
| Reached as | qBittorrent `gluetun:8080`, Prowlarr `gluetun:9696`, SABnzbd `gluetun:8081` (Radarr/Sonarr download client host) |
| SABnzbd | `port = 8081` and `host_whitelist` += `gluetun` in `sabnzbd.ini` (not in git; backup `backups/pre-sab-vpn-2026-10-23/`); still reaches `clamav:3310` (gluetun's DNS resolves the container names) |
| Verified 2026-10-23 | egress of sab, qBit, Prowlarr = VPN IP (≠ WAN); Radarr/Sonarr client tests 200; SAB "test server" (eweka, 563 TLS) OK; clamav hook clean scan from sab; Prowlarr testall valid; `192.168.1.102:8081` from fastpi and `sabnzbd.holy-grail.ch` (302 to SSO) unchanged |
| Rollback | `backups/pre-sab-vpn-2026-10-23/`: compose, `sabnzbd.ini`, the two download-client JSONs (PUT back to `/api/v3/downloadclient/2`) | `FIREWALL_OUTBOUND_SUBNETS` in `.env`
**must** list the bridge subnet, or gluetun silently drops the arr apps' replies
and Radarr cannot see qBittorrent at all.

Port forwarding uses gluetun's own `VPN_PORT_FORWARDING_UP_COMMAND` hook
(`scripts/qbit-port.sh`: sets `listen_port`, then reannounces every torrent). That is why this stack has **no port-updater container**, unlike the older `mouse` stack on fastpi; the rest of port_updater's job is split up, see [Recovery](#recovery-parity-with-fastpis-mouse-stack).

⚠️ **Enable qBittorrent → Options → WebUI → "Bypass authentication for clients on
localhost"**, or the hook gets a 403 and the forwarded port is never applied —
which looks exactly like a dead tracker.

Restarting or recreating only `qbittorrent` resets its listen port to 6881 and gluetun does not re-run the hook; `movie-vpn-status` sets it back within 30 s (`port_fix` in the history). By hand: `sudo docker exec movie-gluetun /scripts/qbit-port.sh $(sudo docker exec movie-gluetun cat /tmp/gluetun/forwarded_port)`.

qBittorrent settings that matter here (in `/srv/appdata/qbittorrent`, not in git; set via `POST /api/v2/app/setPreferences`, same as fastpi's `qbittorrent_mouse`):

| Setting | Value | Why |
|---|---|---|
| `current_network_interface` | `tun0` | bound to the tunnel only (gluetun's WireGuard interface); without it qBit also listened on `eth0`/bridge and had 0 DHT nodes. Takes effect after a qBit restart |
| `status_bar_external_ip` | `true` | WebUI status bar shows the external IP qBit sees (= VPN IP) |
| ratio 2 / 30 days, queueing, connection limits | on | public trackers; mouse has none of these (private tracker, seeds forever) |

### Recovery (parity with fastpi's mouse stack)

| Case | mouse (fastpi) | here |
|---|---|---|
| Forwarded port changes | port_updater sets it + reannounce (≤10 min) | gluetun hook, at once, + reannounce |
| qBit on the wrong port (restart, drift) | port_updater, ≤15 s | `movie-vpn-status`, ≤30 s, + reannounce |
| No forwarded port | 3 polls at 0 → port_updater restarts the stack | gluetun healthcheck (image check + `GET /v1/portforward` ≠ 0, 20 s × 9) → unhealthy after ~3 min → autoheal restarts gluetun |
| gluetun restarted, namespace members cut off | port_updater restarts qBit + mousehole | their healthcheck needs `tun0` in `/proc/net/dev` → unhealthy → autoheal restarts them into the new namespace (measured 2026-10-23: ~2 min) |
| A container hangs | autoheal-mouse | `movie-autoheal` (label `autoheal-movie=true`: gluetun, qBit, Prowlarr, SABnzbd, FlareSolverr, Radarr, Sonarr, vpn-status) |

- Docker API: `movie-autoheal` → `movie-socket-proxy-heal` (internal network `movie-heal`, `socket-proxy/autoheal-haproxy.cfg`, fastpi pattern with label `autoheal-movie`): default-deny, only the label-scoped unhealthy list and `POST /containers/<64-hex id>/restart`; inspect, names, stop, start, create, exec, kill, logs, images, volumes, networks = 403 (tested 2026-10-25). No app container has docker.sock.
- gluetun's healthcheck reads the vpn-status API key (`secrets/vpn-status.apikey`, mounted read-only).
- Proton without port forwarding for a long time = gluetun restarts every ~4 min, like mouse's stack restart.

## VPN status

`movie-vpn-status`: the status page of fastpi's `port_updater` (mouse stack). It watches, and re-applies the forwarded port (+ reannounce) when qBittorrent's differs; gluetun's hook does the normal updating. No docker access. Code: `vpn-status/web-server.py` (copy of `HomeLab-FastPi` `Docker/mouse/port-updater/web-server.py` plus a poller).

Status pages: https://vpn-status-beefy.holy-grail.ch and https://vpn-status-mouse.holy-grail.ch (fastpi's, Holy Grail, group `homelab-admins`, LAN/VPN only; tiles on sso.holy-grail.ch). The beefy route wakes beefy after the login. The ssh tunnels still work, local port 8187 so both pages can be open at once:

```bash
ssh -L 8187:127.0.0.1:8087 beefy      # beefy: http://localhost:8187/
ssh -L 8087:127.0.0.1:8087 fastpi     # fastpi mouse: http://localhost:8087/
ssh beefy curl -s localhost:8087/api/status   # vpn_ip, real_ip, forwarded_port, qbit_port, match, qbit_external_ip, qbit_connection, qbit_interface, dht_nodes
```

| Card | Source |
|---|---|
| Forwarded Port | gluetun control server `GET /v1/portforward` |
| qBittorrent Port | qBittorrent `listen_port` (`gluetun:8080`, bridge subnet is in qBit's auth whitelist); green ✓ = same as the forwarded port, red ✗ = mismatch |
| VPN IP | gluetun `GET /v1/publicip/ip` |
| Real IP | api.ipify.org from the bridge = beefy's WAN IP (must differ from the VPN IP) |
| qBittorrent External IP | `last_external_address_v4` of `/api/v2/transfer/info`: the IP qBit itself sees, ✓ = VPN IP |
| qBittorrent Connection | `connection_status` · listen interface · DHT nodes; `firewalled` with no torrents is normal (no incoming peers yet) |
| History | `/srv/appdata/vpn-status/port-history.json`: `startup`, `port_update`, `mismatch`, `port_fix` / `port_fix_failed`, `zero_port`, one line per change (polled every 30 s while beefy is awake). `mismatch` + `port_fix` right after a stack start is normal: qBit starts on 6881 |

- `8087` is published on the LAN for fastpi's Traefik (route `HomeLab-FastPi` `Docker/Traefik/traefik/dynamic/vpn-status-beefy.yml`) and is in the `DOCKER-USER` fastpi-only DROP rule (+ the IPv6 INPUT rule). The page has no login of its own, Authentik forward auth is the gate.
- uid 65111, `read_only`, `cap_drop: ALL`, image `python:3.15.2-alpine3.24` (no build).
- gluetun control server auth (since 2026-10-23): `/srv/appdata/gluetun/auth/config.toml` (600, not in git) gives role `vpn-status` (API key, header `X-API-Key`) only `GET /v1/portforward` and `GET /v1/publicip/ip`; every other route is 401, also `PUT /v1/vpn/status` (before, the routes were open to every container on the bridge). Key: `secrets/vpn-status.apikey` (gitignored, 400, uid 65111). New key: write both files, then `docker compose up -d --force-recreate gluetun qbittorrent prowlarr sabnzbd vpn-status` (the others share gluetun's namespace).
- Tests: `python3 tests/test_vpn_status.py`, `tests/test_qbit_port.sh` (the hook).

## Auth: Authentik is the only login

Every UI in this stack sits behind Authentik forward auth on fastpi's Traefik, and the
apps' own logins are turned off — Radarr/Sonarr/Prowlarr auth = `External`, Bazarr = `None`,
SABnzbd username/password empty, qBittorrent bypassed for the proxy subnet. Full
setup, including the Authentik objects that must be created by hand, is in
`HomeLab-FastPi` → `Docker/Traefik/docs/2026-10-06-authentik-sso.md`.

⚠️ **The host ports below are an unauthenticated bypass of all that.** Traefik reaches
these apps at `192.168.1.102:<port>`, so the publications cannot simply be removed —
without them every route breaks. The firewall lets only fastpi reach them: `../_global/docs/container-hardening.md`, Firewall.

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
