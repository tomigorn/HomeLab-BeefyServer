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

```bash
# 1. Let buntu talk to the Docker daemon. The `docker` group currently has no
#    members, which is why `docker info` fails. Log out and back in afterwards.
sudo usermod -aG docker buntu

# 2. The media identity. Deliberately the same uid/gid that `mouse` uses on
#    fastpi, so ownership survives a file moving between boxes.
sudo groupadd -g 1100 media
sudo useradd -u 1101 -g 1100 -M -s /usr/sbin/nologin media

# 3. The directory tree. downloads/ and media/ MUST share one parent so Radarr
#    can hardlink across them (see "Storage" below).
sudo mkdir -p /srv/video/downloads/torrents/{incomplete,complete} \
             /srv/video/downloads/usenet/{incomplete,complete} \
             /srv/video/media/movies \
             /srv/appdata/{gluetun,qbittorrent,prowlarr,sabnzbd,radarr,bazarr}
sudo chown -R 1101:1100 /srv/video /srv/appdata
sudo chmod -R 775 /srv/video /srv/appdata
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

```
/srv/video/                     -> /data in every container
├── downloads/
│   ├── torrents/{incomplete,complete}
│   └── usenet/{incomplete,complete}
└── media/movies/               <- Radarr's root folder; Jellyfin reads this :ro
```

Separate `/downloads` and `/movies` mounts are the classic mistake: Radarr sees
two unrelated filesystems and every import becomes a full byte-for-byte copy
rather than an instant hardlink. Configure the apps with these **container**
paths:

| App | Setting | Value |
|---|---|---|
| qBittorrent | incomplete / complete | `/data/downloads/torrents/incomplete` / `/data/downloads/torrents/complete` |
| SABnzbd | incomplete / complete | `/data/downloads/usenet/incomplete` / `/data/downloads/usenet/complete` |
| Radarr | root folder | `/data/media/movies` |
| Radarr | qBittorrent client | host `gluetun`, port `8080` |
| Radarr | SABnzbd client | host `sabnzbd`, port `8080` |

No remote-path mapping is needed — every container sees the identical tree.

### The mergerfs catch, and why it matters here

`/srv/video` is a mergerfs union of `ssd-hot` (7.3T ext4) and `hdd-cold` (27.3T
xfs) with `category.create=ff`, so new files land on the SSD and downloads and
library start life on the same filesystem. Hardlinks work.

**A hardlink cannot span ext4 and xfs.** The moment a movie is demoted to
`hdd-cold`, its hardlink to the still-seeding download breaks and you are paying
for two full copies. Set qBittorrent to remove torrents at a seed ratio or time
limit so the download side is gone before demotion, or expect the hot tier to
fill with orphans.

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
