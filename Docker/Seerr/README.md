# Seerr — requests for films & series

[Seerr](https://docs.seerr.dev) (Overseerr and Jellyseerr merged, v3) lets everyone
with Jellyfin access browse TMDB and request a film or a series. Approved requests
go to Radarr/Sonarr (`../Movie-Downloads`), availability comes from Jellyfin.
Public at `https://requests.holy-grail.ch` (wakes beefy, ~45 s).

Jellyseerr itself is frozen at 2.7.3 (`fallenbagel/jellyseerr:2.7.3-deprecated`); the
successor image is `ghcr.io/seerr-team/seerr`, pinned to `v3.5.0` here.

## Deploy

```bash
sudo mkdir -p /srv/appdata/seerr && sudo chown 1000:1000 /srv/appdata/seerr && sudo chmod 2775 /srv/appdata/seerr
cd ~/Projects/Docker/Seerr && sudo docker compose up -d
curl -s http://127.0.0.1:5055/api/v1/status     # {"version":"3.5.0",...}
```

| | |
|---|---|
| Port | `5055` on beefy, reachable from fastpi only (DOCKER-USER rule) |
| Networks | joins `movie-downloads` and `jellyfin` (external): `radarr:7878`, `sonarr:8989`, `jellyfin:8096` |
| Config | `/srv/appdata/seerr` (`settings.json` holds the API key and the Jellyfin token; `db/db.sqlite3`) |
| Traefik / Authentik | `HomeLab-FastPi` -> `Traefik/traefik/dynamic/requests.yml`; Authentik app `requests`, group **`streaming-users`** (`Authentik/scripts/provision-media-sso.py`) |

## How the login works

Seerr 3.5.0 has no OIDC (an `preview-new-oidc` branch exists upstream, not released),
so Holy Grail cannot sign the user into Seerr itself. Two layers:

1. **Holy Grail gate** (Traefik forward auth, group `streaming-users`): nobody who is
   not a streaming user reaches the app at all. Browsers only; Seerr has no native
   apps that would need to bypass it.
2. **Seerr's own session**: sign in with **Jellyfin Quick Connect** (Seerr 3.4+). The
   Seerr login page shows a 6-digit code; approve it in Jellyfin (web or app, already
   signed in with Holy Grail) under *Profile -> Quick Connect*. Passwords are not
   needed anywhere - Jellyfin passwords are disabled. "Enable New Jellyfin Sign-In" is
   on, so a Jellyfin user who was never imported gets a Seerr account with the
   default permissions (`Request`) on first sign-in.

Local (email + password) accounts stay possible for an emergency admin, nothing
else. Both sessions are long-lived, so the double login is rare.

## First login (one-time, by hand) — done 2026-10-19, owner is tomigorn; friends auto-approve (defaultPermissions 160)

Seerr's **first** user becomes the owner and must sign in with a Jellyfin
**username + password** (Quick Connect is refused until a user exists, and the
settings API needs that user). Jellyfin is SSO-only, so open a short password
window for your own account, then close it again. On beefy:

```bash
cd ~/Projects/Docker/Jellyfin && sudo docker compose stop jellyfin
C=/srv/appdata/jellyfin/data
sudo sqlite3 $C/data/jellyfin.db ".backup $C/data/jellyfin.db.bak-pre-seerr"
sudo sqlite3 $C/data/jellyfin.db "SELECT quote(Password), quote(AuthenticationProviderId) FROM Users WHERE Username='tomigorn';" | sudo tee /root/jf-auth.sav
sudo sqlite3 $C/data/jellyfin.db "UPDATE Users SET AuthenticationProviderId='Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider' WHERE Username='tomigorn';"
sudo docker compose start jellyfin
# set a temporary password (API key: secrets/api-key in the Jellyfin project), user id from GET /Users
JK=$(cat ~/Projects/Docker/Jellyfin/secrets/api-key); U=<your user id>
curl -H "Authorization: MediaBrowser Token=$JK" -H 'Content-Type: application/json' -X POST "http://127.0.0.1:8096/Users/$U/Password" -d '{"CurrentPw":"","NewPw":"<temp>","ResetPassword":false}'
```

Then `https://requests.holy-grail.ch/setup`: media server **Jellyfin**, hostname
`jellyfin`, port `8096`, your Jellyfin username + the temporary password. Finish
the wizard or stop after the login and run `scripts/configure.sh` (Jellyfin
libraries, Radarr and Sonarr entries incl. the Sonarr anime profile (`Anime - Bluray +
WEB 1080p`, series type `anime`, same root `/data/media/tv`; see `../Movie-Downloads/README.md`
"Languages"), app URL, initialize - it reads the keys from
`/srv/appdata`). Finally close the window:

```bash
cd ~/Projects/Docker/Jellyfin && sudo docker compose stop jellyfin
PW=$(sudo cut -d'|' -f1 /root/jf-auth.sav); PR=$(sudo cut -d'|' -f2 /root/jf-auth.sav)
sudo sqlite3 $C/data/jellyfin.db "UPDATE Users SET Password=$PW, AuthenticationProviderId=$PR WHERE Username='tomigorn';"
sudo rm /root/jf-auth.sav && sudo docker compose start jellyfin
# every user must show OidcPasswordDisabledProvider again:
curl -s -H "Authorization: MediaBrowser Token=$JK" http://127.0.0.1:8096/Users | grep -o '"AuthenticationProviderId":"[^"]*"'
```

The Seerr session token survives the restore (it is a Jellyfin session, not a
password), and Seerr created its own Jellyfin API key ("Seerr") during the login.

## What `scripts/configure.sh` sets

| Setting | Value |
|---|---|
| Jellyfin | `http://jellyfin:8096`, external `https://jellyfin.holy-grail.ch`, libraries Movies + Shows enabled |
| Radarr | `radarr:7878`, profile **4K HDR - UHD Bluray + WEB (1080p fallback)**, root `/data/media/movies`, min availability released, default |
| Sonarr | `sonarr:8989`, profile **4K HDR - WEB-2160p (1080p fallback)**, root `/data/media/tv`, season folders, default |
| App | URL `https://requests.holy-grail.ch`, title "Requests" |

Permissions: the owner (you) is admin, own requests are auto-approved. New users get
`Request` only, so a friend's request waits for your approval (Seerr -> Requests).
Change under Settings -> Users -> Default Permissions. Notifications (Telegram,
email) are not set up: Settings -> Notifications.

## Related

- `../Movie-Downloads` — Radarr, Sonarr, Prowlarr, download clients, Bazarr, Recyclarr
- `../Jellyfin` — playback; libraries `/media/movies` and `/media/tv`
