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
| 65111 | movie-vpn-status (read_only) | | |
| 70 | dt5-db (v4 dt-db until the rollback window ends) | | |
| 65112 | clamav | 1100, 65118 | none |
| 65113 | cup-agent | | |
| 65114 | cup-socket-proxy-ro | 973 docker | |
| 65115 | cup-socket-proxy-unix | | |
| 65119 | movie-flaresolverr (read_only) | | |
| 65120 | movie-socket-proxy-heal (read_only) | 973 docker | |
| 65121 | movie-autoheal (read_only) | | |
| 65122 | dt5-apiserver (read_only; v4 dt-apiserver) | | |
| 65123 | dt5-frontend (read_only; v4 dt-frontend) | | |
| 65124 | seerr-proxy (nginx, read_only) | | |
| 65125 | floppy (app processes `abc` via PUID/PGID; supervisord + nginx master root) | | CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, KILL |
| 65126 | floppy-redis (read_only) | | |
| 65127 | seerr-download-now (read_only; group 65127 reads `Seerr/secrets/download-now`) | | |
| 65128 | jellyfin-popular (read_only; group 65128 reads `Jellyfin/secrets/popular`, writes `/srv/appdata/jellyfin-popular-state`) | | |
| 65116 | gluetun files (PUID; process root) | | NET_ADMIN, NET_RAW, NET_BIND_SERVICE, DAC_OVERRIDE, CHOWN |
| 0 | portainer_agent | | none (full socket by design, `AGENT_SECRET` required) |

Groups: `media` 1100 (`/srv/video`), `hook-secret` 65117 (`Movie-Downloads/secrets/hook`), `textfile-writers` 65118 (`/var/lib/clamav-scan/textfile`). `buntu` is in `media` and `hook-secret`. Next free uid: 65129+.

## Root by design
| Container | Why |
|---|---|
| jellyfin | image has no non-root mode (tested: s6 service always switches user) |
| movie-gluetun | creates tun0 and iptables rules |
| portainer_agent | needs the full Docker API; the socket is root anyway |
| floppy | no non-root mode: the entrypoint runs `usermod`/`chown`, supervisord switches to `abc` (65125); root fs writable for the same reason |
| clamav-hostscan | caps DAC_READ_SEARCH, DAC_OVERRIDE, CHOWN: weekly host scan reads everything (`/:/host:ro`); non-root cannot use DAC_READ_SEARCH. Safe because `network_mode: none`, read-only root fs, writes only state + textfile; the always-on `clamav` no longer mounts the host root |

## Memory limits (2026-10-29)
Every container has `mem_limit` (swap = same again, Docker default). Sized from `memory.stat` anon + `memory.peak` of the cgroup since the last start, plus headroom: the peak includes page cache (Jellyfin and SABnzbd ~50 GB), which a limit reclaims instead of killing. Applied live with `docker update --memory X --memory-swap 2X`, persisted in compose.

| Container | Limit | Anon at sizing |
|---|---|---|
| jellyfin | 8g | 0.4 GB (QuickSync transcodes, .NET sizes its heap from the limit) |
| seerr | 2g | 0.36 GB |
| movie-flaresolverr | 2g | 0.1 GB (Chromium per solve) |
| movie-gluetun | 512m | 28 MB |
| movie-recyclarr | 512m | 4 MB |
| movie-vpn-status | 128m | 10 MB |
| portainer_agent | 256m | 17 MB |

## Images
- `jellyfin-local`: `Jellyfin/Dockerfile` bakes in the Intel OpenCL runtime (Docker Mods are not used).
- `clamav-local`: uid pinned to 65112 (`apk` default 100 is the host's `syslog`).
- Secrets: files owned by the reading uid from this map, not env (`secrets-as-files.md`).
- Base image tracking (Cup `images.extra`) and the rebuild routine of each local image: `Docker/Cup-Agent/README.md`, Locally built images, and the `Rebuild` line in each README.

## Backup of the switch
`~/backups/pre-uid-20261022-230855/`: `appdata.tgz`, `jellyfin.tgz`, `seerr.tgz`, `perms.before` (all 600).

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
| containers → LAN | dropped, except `jellyfin` net (Jellyfin, Seerr) and `dependency-track` net → `192.168.1.2:443` (sso via `extra_hosts`). Container → beefy's own published ports is unaffected (DNAT, bridge to bridge) |
| `floppy` net `172.28.16.0/24` | internet only, tcp 80/443 (rest dropped + logged `FLOPPY-EGRESS drop`): 10/8, 172.16/12 (other Docker networks, also via published ports), 192.168/16, 100.64/10, 169.254/16 dropped (user-supplied integration URLs = SSRF) |
| `movie-downloads` net `172.28.10.0/24` inbound | only its own members and fastpi (S-01). `MOVIE_INGRESS=log` (since 2026-10-28): other bridges are only logged `MOVIE-INGRESS seen`; `=drop`: logged `MOVIE-INGRESS drop` and dropped |
| gluetun namespace (qBit, SAB, Prowlarr) | same DOCKER-USER drop, and `FIREWALL_OUTBOUND_SUBNETS` = bridge only; replies to fastpi still leave via eth0 |
| IPv6 | chain `BEEFY-INPUT6` on `enp6s0`: only tcp/22 accepts new connections (docker-proxy listens on `[::]` for every published port) |
| DNS | not needed: containers resolve through dockerd in the host namespace (`127.0.0.53`) |

Check: `sudo iptables -S DOCKER-USER; sudo ip6tables -S BEEFY-INPUT6`. Test: `sudo docker exec movie-sonarr nc -zw2 192.168.1.2 22` must fail. fastpi side: `HomeLab-FastPi` `_global/scripts/docker-user-lan-guard.sh` (VPN clients reach beefy only on :22, :8096).
