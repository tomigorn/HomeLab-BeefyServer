# Network ip_range (prepared, not applied)

Bug hunt BB-09 (2026-11-24): pinned `ipv4_address` values sit inside the dynamic range. A dynamic member (a `compose run`, a new service) gets the lowest free address first, so start order at boot decides who gets a pinned IP (same class as fastpi's Traefik outage 2026-11-24).

**Status:** prepared 2026-11-25, not applied. Docker cannot change a network's IPAM in place: each change below needs the network recreated (all its members down), so it is an attended change. The compose files are unchanged until then, so no nightly `up` recreates a network on its own.

## Change

Add `ip_range` to the network's `ipam.config` entry. No pinned address changes.

| Network (compose project) | Subnet | Pins now | New `ip_range` (dynamic only) |
|---|---|---|---|
| `movie-downloads` (Movie-Downloads) | 172.28.10.0/24 | .2–.6, .201–.205 | `172.28.10.64/26` (was `.0/25`, holds .2–.6) |
| `clamd` (Movie-Downloads) | 172.28.18.0/24 | .2–.4 | `172.28.18.128/25` |
| `movie-heal` (Movie-Downloads) | 172.28.13.0/24 | .2–.3 | `172.28.13.128/25` |
| `jellyfin` (Jellyfin) | 172.28.11.0/24 | .2–.6 | `172.28.11.128/25` |
| `jellyfin-beta` (Jellyfin-Beta) | 172.28.20.0/24 | .2 | `172.28.20.128/25` |
| `cup-agent` (Cup-Agent) | 172.28.12.0/24 | .2 | `172.28.12.128/25` |
| `dependency-track-v5` (Dependency-Track) | 172.28.14.0/24 | .2–.4 | `172.28.14.128/25` |
| `floppy` / `floppy-redis` (Floppy) | 172.28.16.0/24, 172.28.17.0/24 | .2–.3 | `.128/25` of each |
| `egress` (Antivirus) | 172.28.19.0/24 | .2 | `172.28.19.128/25` |
| `seerr-front` (Seerr) | 172.20.0.0/16 | .0.2–.0.4 | `172.20.128.0/17` |
| `translate-edge` / `translate-internal` (Subtitle-Translate) | 172.21.0.0/16, 172.22.0.0/16 | .0.2–.0.3 | `172.21.128.0/17`, `172.22.128.0/17` |

Example (Jellyfin):
```yaml
      config:
        - subnet: 172.28.11.0/24
          ip_range: 172.28.11.128/25
```

## Apply (attended, outside 03:55–04:45, nobody streaming)

Networks other projects join as `external` go last-in, first-out: stop the joiners first.

| Network | Stop first | Then |
|---|---|---|
| `jellyfin` | Seerr, Activity-Gate | Jellyfin |
| `movie-downloads`, `clamd` | Seerr, Activity-Gate, Antivirus | Movie-Downloads |
| the rest | – | its own project |

Per project: edit compose, then `cd ~/Projects/Docker/<Project> && sudo docker compose down && sudo docker compose up -d` (down removes the project's networks, never volumes without `-v`), start the joiners again, then `bash ~/Projects/Docker/_global/scripts/post-update-check.sh <Project>` for each project touched. Check: `sudo docker network inspect <net> --format '{{range .IPAM.Config}}{{.IPRange}}{{end}}'`.

Rollback: `git revert` the compose change, same down/up order.
