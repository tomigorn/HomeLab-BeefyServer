# Movie-Downloads — VPN, torrents, usenet and Radarr on beefy

Everything needed to acquire a movie and file it into the library: **gluetun**
(ProtonVPN) wrapping **qBittorrent** and **Prowlarr**, plus **SABnzbd**, **Radarr**
and **Bazarr** outside the tunnel. Playback is a separate project — see
`../Jellyfin`.

**Not deployed yet.** This folder is scaffolding; the prerequisites below need a
few sudo commands first.

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
