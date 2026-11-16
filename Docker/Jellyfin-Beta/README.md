# Jellyfin-Beta: try → compare → promote to prod

A copy of prod Jellyfin (`../Jellyfin`) to try plugins, home rows and settings before prod. Same image, same users, same library (read-only), own DB copy.

| | Prod | Beta |
|---|---|---|
| URL | https://jellyfin.holy-grail.ch | https://jellyfin-beta.holy-grail.ch |
| Container / port | `jellyfin` / 8096 | `jellyfin-beta` / 8196 (firewall: fastpi only, like 8096) |
| Data | `/srv/appdata/jellyfin` | `/srv/appdata/jellyfin-beta` (no cache, no trickplay) |
| uid / network | 65108 / `jellyfin` 172.28.11.0/24 | 65132 / `jellyfin-beta` 172.28.20.0/24 (no path to the arrs, Floppy, popular, LAN) |
| Limits | 16 GB RAM | 4 GB RAM, CPU weight 256, lowest I/O weight |
| Night jobs, webhooks, scans | yes | none (`scripts/sanitize.py`, `scripts/beta_tasks.py`) |
| Image | local build, notify | prod's tag; not in `auto-update.conf`; `refresh-from-prod.sh` warns when prod moved on |

## Sign in

| Who | How |
|---|---|
| Household member, web | "Sign In with Holy Grail" once the beta's Authentik app exists (HomeLab-FastPi `Docker/Authentik/scripts/provision-jellyfin-beta-sso.sh`); until then Quick Connect below |
| Household member, any device | press **Use Quick Connect**, read the 6-digit code, then on beefy: `python3 scripts/beta_login.py <code> <username>` (`--users` lists them) |
| Local admin | `beta-admin`, password in `secrets/beta-admin.password` (never printed). Password sign-in is blocked by Traefik: only through `ssh -L 8196:192.168.1.102:8196 fastpi` → http://localhost:8196 |

Moonfin: add the server `https://jellyfin-beta.holy-grail.ch` (own server id). Moonfin web: `/Moonfin/Web/`.
Playback over the internet: not yet (the waker's stream check knows only prod's tokens); LAN/VPN plays.

## What differs from prod (`scripts/sanitize.py`, run by every refresh)
- No timed scheduled tasks (startup ones stay), no webhooks (Tracker, Trending requests), no Seerr in Moonbase, no library monitor / trickplay / chapter images / NFO writes.
- Own server id (`secrets/server-id`) and name "Holy Grail Beta", a BETA note on the sign-in page.
- OIDC: the beta's own Authentik client from `secrets/oidc.json` (off without it); password sign-in off for SSO users, on for `beta-admin`.

## Experiments in the beta now

| Experiment | Files | Run |
|---|---|---|
| Home rows plan (Moonfin): fixed + per-user + seasonal + 3 rotating rows | `scripts/rows.py` | `python3 scripts/rows.py` (`--dry-run`, `--counts`, `--month YYYY-MM`); timer `jellyfin-beta-rows.timer` daily 12:17 |
| "Not downloaded yet" band in each poster's complement colour (TV images + web) | `scripts/band_colour.py`, `band_render.py`, `band-web.js` | `sudo python3 scripts/band_render.py` (`--check`, `--style classic` reverts) |

Rows: Continue Watching, Next Up, Favourite movies/series, Recently added (built in); Top Picks for You, Hidden Gems (≥ 7.5, unplayed), Watch Again (played + ≥ 7 or ♥) per user (one collection each, others' hidden by `BlockedTags`); Awards Season (Wikidata: Oscar Best Picture, Golden Globe Best Picture, Palme d'Or, won or nominated, or tag `award winner`), Sagas & Franchises, Based on a True Story (TMDB keywords), Christmas (Dec) / Halloween (Oct), Binge in a Weekend (≤ 2 seasons or `miniseries`); rotating: Director Spotlight, Starring, World Cinema, CH/DE/AT, Mind-Benders, Decade Night, Animation for Grown-ups, Under 100 Minutes, 4K HDR.

## Refresh from prod

```bash
bash scripts/refresh-from-prod.sh                 # ~1 min, beta down meanwhile; prod is only read
bash scripts/refresh-from-prod.sh --keep-plugins  # keep beta-only plugins and their configs
../_global/scripts/post-update-check.sh Jellyfin-Beta
```

Reset by a refresh: beta watch history and user settings, beta-only collections and Moonfin layouts (rebuilt by `rows.py`), beta-only plugins (unless `--keep-plugins`). Kept: `secrets/`, the band originals (taken again from prod's images). Not in the night window.

## Promote to prod
1. Write the change down here (what, why, the check).
2. Move the code into `../Jellyfin` (prod's scripts), with tests, as its own commit; prod paths and port 8096.
3. `bash ../Jellyfin/scripts/backup-config.sh <label>`, apply on prod, add the `CUSTOMIZATIONS.md` row and its check.
4. `../_global/scripts/post-update-check.sh Jellyfin`, then a real look on web, phone and TV.
5. Remove the experiment from the beta (or `refresh-from-prod.sh`).

## Setup (done 2026-11-17)
`.env` from `.env.example`; copy as in `refresh-from-prod.sh`; `sudo docker compose up -d`; `python3 scripts/beta_admin.py`; timer: `cp systemd/jellyfin-beta-rows.* ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now jellyfin-beta-rows.timer`. Route: HomeLab-FastPi `Docker/Traefik/traefik/dynamic/jellyfin-beta.yml`.

## Remove
`sudo docker compose down`, `systemctl --user disable --now jellyfin-beta-rows.timer`, `sudo rm -r /srv/appdata/jellyfin-beta` (asks first), the Traefik file, the Authentik app (`provision-jellyfin-beta-sso.sh --rollback`), the Cloudflare hostname.

Tests: `cd scripts && python3 -m unittest test_sanitize test_rows test_beta_tasks test_beta_login test_band_colour`.
