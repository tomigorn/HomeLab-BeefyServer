# Container hardening (beefy)

State 2026-10-23, verified from `/proc/<pid>/status` of every process. Same rules as fastpi (`HomeLab-FastPi`, `Docker/_global/docs/container-hardening.md`).

## Rules for every container
| Rule | How |
|---|---|
| Never uid/gid 1000 (`buntu`, NOPASSWD sudo) or 100 (`users`) | dedicated `user: "651NN:…"`, next free in the map below |
| No root where the image allows it | `user:`; linuxserver images in non-root mode (`tmpfs: /run:uid=…,gid=…,exec`) when the readme lists it |
| No capabilities | `cap_drop: [ALL]`, `cap_add` only what a test proved is needed |
| No privilege gain | `security_opt: [no-new-privileges:true]` |
| `/srv/video` (mergerfs) | group `media` (1100) as the **primary** gid: FUSE ignores supplementary groups; `UMASK=002` for hardlinks |
| `/srv/appdata/<app>` | owned by the app's uid, group `buntu` (setgid) so host scripts read the API keys, `o-rwx` |

## uid map
| uid:gid | Container | Extra groups | Caps |
|---|---|---|---|
| 65101:1100 | movie-radarr | 65117 | |
| 65102:1100 | movie-sonarr | 65117 | |
| 65103:65103 | movie-prowlarr | | |
| 65104:1100 | movie-bazarr | | |
| 65105:1100 | movie-qbittorrent | 65118 | |
| 65106:1100 | movie-sabnzbd | 65118 | |
| 65107:65107 | movie-recyclarr | | |
| 65108 | jellyfin (app; s6 PID1 root) | 44 video, 990 render | CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID |
| 65109 | seerr | | |
| 65110 | ryot-go | | |
| 65111 | movie-vpn-status (read_only) | | |
| 1001 | ryot (image user, writes its own /home/ryot) | | NET_BIND_SERVICE (caddy file cap) |
| 70 | ryot-db | | |
| 65112 | clamav | 1100, 65118 | DAC_READ_SEARCH |
| 65113 | cup-agent | | |
| 65114 | cup-socket-proxy-ro | 973 docker | |
| 65115 | cup-socket-proxy-unix | | |
| 65119 | movie-flaresolverr (read_only) | | |
| 65120 | movie-socket-proxy-heal (read_only) | 973 docker | |
| 65121 | movie-autoheal (read_only) | | |
| 65116 | gluetun files (PUID; process root) | | NET_ADMIN, NET_RAW, NET_BIND_SERVICE, DAC_OVERRIDE, CHOWN |
| 0 | portainer_agent | | none (full socket by design, `AGENT_SECRET` required) |

Groups: `media` 1100 (`/srv/video`), `hook-secret` 65117 (`Movie-Downloads/secrets/hook`), `textfile-writers` 65118 (`/var/lib/clamav-scan/textfile`). `buntu` is in `media` and `hook-secret`. Next free uid: 65122+.

## Root by design
| Container | Why |
|---|---|
| jellyfin | image has no non-root mode (tested: s6 service always switches user) |
| movie-gluetun | creates tun0 and iptables rules |
| portainer_agent | needs the full Docker API; the socket is root anyway |

## Images
- `jellyfin-local`: `Jellyfin/Dockerfile` bakes in the Intel OpenCL runtime (Docker Mods are not used).
- `clamav-local`: uid pinned to 65112 (`apk` default 100 is the host's `syslog`).
- Secrets: files owned by the reading uid from this map, not env (`secrets-as-files.md`).
- Base image tracking (Cup `images.extra`) and the rebuild routine of each local image: `Docker/Cup-Agent/README.md`, Locally built images, and the `Rebuild` line in each README.

## Backup of the switch
`~/backups/pre-uid-20261022-230855/`: `appdata.tgz`, `jellyfin.tgz`, `seerr.tgz`, `ryot-dumpall.sql`, `perms.before` (all 600).

## Host access (2026-10-24)
| What | Setting |
|---|---|
| `~buntu/.ssh/authorized_keys` | every key has `from=`: automation keys (pi@fastpi, poweroff, history) only from `192.168.1.2`, personal devices only from `192.168.1.0/24`; backup `authorized_keys.bak-20261024` |
| `/etc/ssh/sshd_config.d/20-access.conf` | no X11/agent forwarding, `MaxAuthTries 3` (TCP forwarding stays for `ssh -L`) |
| SSH beefy → fastpi | removed; only fastpi → beefy |

## Firewall (2026-10-24)
Script: `_global/scripts/docker-user-guard.sh` (idempotent; `--persist` writes `/etc/iptables/rules.v4` / `rules.v6` with only its two chains, loaded at boot by netfilter-persistent). Never `netfilter-persistent save`: it freezes Docker's per-bridge rules, which return at boot for deleted bridges and drop traffic to reused container IPs.

| Direction | Rule |
|---|---|
| LAN → published container ports | allowlist: only `192.168.1.2` (fastpi: Traefik, Prometheus, Portainer, Cup). A newly published port is closed by default |
| containers → LAN | dropped, except `jellyfin` net (Jellyfin, Seerr) and `ryot` net → `192.168.1.2:443` (sso + tracker via `extra_hosts`). Container → beefy's own published ports is unaffected (DNAT, bridge to bridge) |
| gluetun namespace (qBit, SAB, Prowlarr) | same DOCKER-USER drop, and `FIREWALL_OUTBOUND_SUBNETS` = bridge only; replies to fastpi still leave via eth0 |
| IPv6 | chain `BEEFY-INPUT6` on `enp6s0`: only tcp/22 accepts new connections (docker-proxy listens on `[::]` for every published port) |
| DNS | not needed: containers resolve through dockerd in the host namespace (`127.0.0.53`) |

Check: `sudo iptables -S DOCKER-USER; sudo ip6tables -S BEEFY-INPUT6`. Test: `sudo docker exec movie-sonarr nc -zw2 192.168.1.2 22` must fail. fastpi side: `HomeLab-FastPi` `_global/scripts/docker-user-lan-guard.sh` (VPN clients reach beefy only on :22, :8096).
