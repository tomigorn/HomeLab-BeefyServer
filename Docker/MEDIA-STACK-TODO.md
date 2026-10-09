# Media stack — outstanding work

Tracking for the Jellyfin + Movie-Downloads build on beefy. Started 2026-10-05.
Scaffolding is committed and reconciled with the storage doc; **nothing is deployed**.

Legend: 🔴 blocker · 🟡 needed before real use · ⚪ later / optional · ✅ done

---

## ✅ Blockers — ALL CLEARED 2026-10-09

- ✅ **Docker boot-order drop-in** — `/etc/systemd/system/docker.service.d/10-require-srv-video.conf`.
      Verified three ways: `DropInPaths` loaded, `Requires=`/`After=srv-video.mount`
      both present, and `docker.service` appears under `systemctl list-dependencies
      --reverse srv-video.mount`. Then PROVEN by masking the mount and watching
      Docker refuse: `Failed to start docker.service: Unit srv-video.mount is masked`.
      (Note for future testing: merely *stopping* the mount proves nothing —
      `Requires=` pulls it back up. The dependency must be made unsatisfiable.)
- ✅ **`buntu` in the `docker` group** — `docker ps` works.
- ✅ **Pool tree + ownership** — §13.2 layout, `1000:1000`, `2775` with the setgid
      bit set (that bit is what makes cross-container hardlinks work).
- ✅ **psmisc + attr installed** — `tier-move`'s open-file guard can run.
- ✅ **`tune2fs -m 1 /dev/sda1`** — reclaimed 298G of ext4 root reserve
      (7019.6G → 7317.7G available).

## ✅ Deployed and configured 2026-10-09

Both stacks are live on beefy. All six hostnames serve through Traefik, gated by
Authentik, and are covered by the blackbox probes (10/10 up).

Done automatically via each app's REST API — the keys live in /srv/appdata and are
readable as buntu, so almost none of this needed doing by hand:

- ✅ **ProtonVPN** — key valid, tunnel up, exit IP confirmed different from WAN,
      port forwarding live. `qbit-port.sh` now applies new ports automatically
      (observed it move 54998 -> 63440 on a reconnect with no intervention).
- ✅ **qBittorrent** — save path `/data/torrents/movies`, seed limits ratio 2.0 /
      30 days then pause, bypass-auth for localhost + `172.28.10.0/24`.
- ✅ **Radarr** — root folder `/data/media/movies` (sees 37.3 TB), recycle bin
      `/data/.recyclebin` 7-day, hardlinks ON, both download clients added and
      connection-tested OK.
- ✅ **SABnzbd** — folders set, `movies` category created, host whitelist fixed.
      The whitelist was the blocker: Radarr got 403 because SABnzbd only accepted
      its own container-ID hostname.
- ✅ **Prowlarr** — Radarr linked, fullSync. Note the URL that works is
      `http://gluetun:9696`, not `http://prowlarr:9696` — Prowlarr shares gluetun's
      network namespace, same as qBittorrent.
- ✅ **Bazarr** — connected to Radarr, SignalR feed live.
- ✅ **Hardlinks PROVEN** — a test file linked from `torrents/` into `media/` shared
      one inode with link count 2, both on `ssd-hot`. The storage design holds.

## 🔴 YOUR TASKS — needs sudo or credentials I do not have

- [ ] **ufw rules — do this first.** Radarr and Prowlarr currently run with
      `authenticationMethod=none`, so the LAN ports are open with NO login at all.
      Authentik only protects the Traefik path. This is live right now, not a
      future risk:
      ```bash
      sudo ufw status                     # check it is active first
      sudo ufw allow from 192.168.1.2 to any port 7878,9696,6767,8080,8081,8096 proto tcp
      sudo ufw deny  to any port 7878,9696,6767,8080,8081,8096 proto tcp
      sudo ufw status numbered            # allow MUST sit before deny
      ```
- [ ] **Prowlarr indexers** — needs your tracker accounts. Prowlarr UI → Indexers →
      Add. They sync to Radarr automatically once added.
- [ ] **SABnzbd usenet provider** — needs your provider credentials.
      Config → Servers. Until then usenet finds nothing; torrents are unaffected.
- [ ] **Jellyfin setup wizard** — create the admin account (long random password,
      this is the break-glass credential), add library `/media/movies`, enable
      QSV under Playback → Transcoding.
- [ ] **Jellyfin SSO plugin** — after the wizard. Repo
      `https://raw.githubusercontent.com/9p4/jellyfin-plugin-sso/manifest-release/manifest.json`
      then configure with the Authentik Client ID/Secret you saved.

## 🟡 Jellyfin

- [ ] Verify QSV is genuinely active, not silently software: `intel_gpu_top` Video
      Engine must move during a transcode. `RENDER_GID` (currently 990) is assigned
      dynamically — recheck after any OS upgrade.
- [ ] Disable extract-on-play/scan for cold content; schedule trickplay/BIF,
      embedded-subtitle extraction and Intro-Skipper into the **04:00–06:00** window
      or run them on import while the file is still on SSD (§14.2-I).
- [ ] Turn off aggressive periodic library rescans, or schedule them into the mover
      window — they `stat` cold files and wake the HDD (§13.6).
- [ ] Strong admin password; disable remote connections without authentication.

## 🔴 Authentik SSO — YOUR steps (cannot be done from the repo)

Authentik keeps its config in PostgreSQL, not in files, so none of this is
scriptable from here. Full checklist with exact field values:
`HomeLab-FastPi` → `Docker/Traefik/docs/2026-10-06-authentik-sso.md`.

- ✅ ~~Cloudflare DNS for 6 new hostnames~~ — DONE 2026-10-09, all six verified
      resolving to the Cloudflare proxy IPs with a valid edge cert. NOTE: prowlarr
      and sabnzbd came with a **Cloudflare Access** application attached that the
      other four do not have — decide whether to remove it (Authentik is the SSO) or
      add it everywhere. Do NOT put Access on jellyfin; it breaks native clients.
- [ ] ~~(superseded)~~ radarr, prowlarr, bazarr,
      qbittorrent, sabnzbd, jellyfin (all `.holy-grail.ch`). The tunnel is
      token/dashboard-managed, so ingress rules are in the Cloudflare UI, not the
      repo. If a wildcard `*.holy-grail.ch` → Traefik ingress already exists, only
      the DNS records are needed.
- [ ] **Per arr app: a Proxy Provider (Forward auth, SINGLE application) + an
      Application + a group binding**, in Authentik. Five of them.
- [ ] **Assign each new application to the `authentik Embedded Outpost`.** This is
      the step everyone forgets; without it `/outpost.goauthentik.io/auth/traefik`
      returns 404 and the route is simply dead. Verified 404 today, since no
      provider exists yet.
- [ ] **Jellyfin: an OAuth2/OpenID provider** (not a proxy provider) + the
      `jellyfin-plugin-sso` plugin inside Jellyfin. Jellyfin CANNOT use forward
      auth — its native TV/mobile clients cannot follow an interactive redirect.
      ACCEPTED 2026-10-06: OIDC for all humans, plus one break-glass local admin
      with a long random password kept offline.
- [ ] **Then, and only then**, rename each `dynamic/<app>.yml.disabled` → `.yml` on
      fastpi, one at a time. Doing it before DNS exists makes Traefik fail ACME in a
      loop and risks Let's Encrypt's failed-validation rate limit.
- [ ] **App-side: turn the local logins off** — Radarr/Prowlarr auth = `External`,
      Bazarr = `None`, SABnzbd username/password empty + host whitelist, qBittorrent
      bypass-auth for `172.24.0.0/16`. Post-deploy UI work.
- [ ] **Close the LAN back door — but do NOT remove the host port publications.**
      CORRECTION to earlier advice: Traefik runs on fastpi and reaches these apps at
      `192.168.1.102:<port>`, so removing the publications would break every route.
      The LAN bypass has to be closed with a host firewall instead. ufw is installed
      on beefy; once the stack is deployed:
      ```bash
      sudo ufw allow from 192.168.1.2 to any port 7878,9696,6767,8080,8081,8096 proto tcp
      sudo ufw deny  to any port 7878,9696,6767,8080,8081,8096 proto tcp
      sudo ufw status numbered      # confirm the allow sits BEFORE the deny
      ```
      Check `sudo ufw status` first — if ufw is inactive, enabling it needs care so
      SSH (22) and WoL are not cut off. Keep port 22 allowed from the LAN.
- ✅ ~~Decide public vs LAN-only for the arr UIs~~ — DECIDED 2026-10-06: **public
      behind Authentik**, as built. No change needed.
- [ ] Optional, now unblocked: gate `cup.holy-grail.ch` behind Authentik. It is
      currently public with **no authentication at all**.

## ✅ Alerting — DONE and verified (2026-10-06)

Both channels live and proven by sending synthetic alerts, not by assumption:

- **Email** via the Brevo relay Authentik uses — delivered 07:59 FIRING / 08:04 RESOLVED.
- **Telegram** → group `holy grail alerts` (`@holy_grail_alerts_bot`) — delivered.
- **Routing proven:** `severity=critical` reaches BOTH; `severity=info` reaches
  Telegram ONLY, so Cup's weekly container-update notices never touch the inbox.
- Alertmanager state (silences + notification log) persists across restarts —
  verified by creating a silence, restarting, and confirming it survived.

Remaining, optional:
- [ ] **Create the Authentik admin group** (e.g. `media-admins`) and decide the alert
      recipient list. NOTE Alertmanager cannot query Authentik for group membership,
      so the `to:` list in `alertmanager.yml` is maintained by hand — or point it at
      one alias that fans out. It is currently just your own address.
- [ ] Optional hygiene: the bot token was pasted into a chat transcript. `/revoke` in
      BotFather issues a fresh one; drop it into
      `grafana/alertmanager/secrets/telegram_bot_token` and restart alertmanager.
- [ ] Add the six new media hostnames to the blackbox probe list once their routes
      go live (`grafana/prometheus/prometheus.yml`, `blackbox-public` job).

## 🟡 fastpi side (HomeLab-FastPi repo, not this one)

- ✅ ~~Traefik dynamic config for Jellyfin~~ — written, parked as
      `dynamic/jellyfin.yml.disabled` pending DNS.
- ✅ ~~Attach `beefy-wake` with `?port=8096`~~ — done in that file. Also done for
      all five arr routes with their own ports.
- ✅ ~~Cloudflare DNS + tunnel ingress for jellyfin~~ — DONE 2026-10-09, verified.
- [ ] **Decided: no Authentik forwardAuth** on this route (breaks native TV/mobile
      clients). CrowdSec + rate limiting + Jellyfin's own auth instead.
- [ ] Accepted risk on record: Cloudflare TOS discourages proxying video (§2.8).

---

## ⚪ Storage tiering

✅ **Manual tooling exists** — `Server/3-Storage-Layout-and-Spindown/`:
  - `tier-report` — what is on which tier, spill risk vs `minfreespace`, HDD power
    state. Never wakes the cold HDD by default (verified: drive stayed `SPUN-DOWN`
    across a full run). `--cold` opts in to walking the cold branch.
  - `tier-move demote|promote <relpath>` — guarded copy → fsync → verify-by-content
    → delete. Dry-run by default. Refuses open or hardlinked (still-seeding) files;
    leaves sidecars hot. 35 tests in `test-tier-move`, green on beefy.

Still open:

- [ ] **Interim discipline: keep the hot SSD above `minfreespace=50G`.** Below it,
      `moveonenospc=true` puts new writes **directly on the cold HDD** and nothing
      moves them back. The doc's warning: *"Don't run the download stack unattended
      at scale before the mover exists."* Check with `tier-report`.
- ✅ ~~Build the nightly mover (§5)~~ — BUILT 2026-10-06, 18 tests.
      `Server/3-Storage-Layout-and-Spindown/beefy-mover` + timer. Ships DRY_RUN=1.
      - [ ] **Install it:** `sudo ./install-mover.sh` on beefy (needs sudo)
      - [ ] **DECIDE: schedule vs. sleep.** The 04:00 window assumes the host is up,
            but beefy powers off when idle and is usually OFF at 04:00. Currently
            `Persistent=true` (runs after next boot, possibly outside the window;
            harmless because the free-space check no-ops when the pool is fine).
            Alternative: fastpi WoLs beefy at 04:00 — honours the window, costs a
            nightly wake. **Your call.**
- ✅ ~~Build the promoter engine (§7)~~ — BUILT 2026-10-06, 19 tests + live HTTP
      smoke test. `beefy_promoter.py`. Ships DRY_RUN=1, binds loopback only.
      - [ ] **Install it:** `sudo ./install-promoter.sh` on beefy (needs sudo)
      - [ ] **Trigger still deferred** — the detail-view detector needs tuning against
            Jellyfin's real read behaviour, and Jellyfin is not deployed. Wire it
            once Jellyfin runs. `POST /promote` works by hand meanwhile.
- [ ] Consider an **NVMe scratch dir for SABnzbd `incomplete/`** (§13.4) — optional
      optimisation, not required.
- [ ] Note for the mover's design: the hot SSD uses `relatime`, so "recently
      watched" is **day-granular, not true LRU** (§14.2-J).

## ⚪ Later / open

- ✅ ~~Push~~ — fastpi pushed through e369990; beefy pushed 2026-10-06.
- [ ] Samba shares for `/srv/video` (never raw branches) — none defined yet.
- [ ] Sonarr/TV: out of scope for now; the §13.2 layout already reserves `tv/` paths.
- [ ] `Specs/specs.md` has an unrelated uncommitted local edit — not ours.

## ⚪ Carried over from the m4b work (2026-10-05)

- [ ] Decide on the 2 audiobooks with damaged sources (`Jack Ryan/01 - Patriot
      Games`, `Jack Ryan/04 - Cardinal Of The Kremlin`). Parked in
      `m4b-merge/runs/failures.json`; originals untouched. Re-download un-parks them
      automatically.
- [ ] Design abort-safe shutdown for the merge orchestrator: SIGTERM currently skips
      the `finally` that releases `/run/beefy-keep-awake`, so a `docker stop`
      mid-merge leaves beefy awake forever. Needs a way to cancel in-flight books.
- ✅ ~~Install `beefy-keep-awake`~~ — done 2026-10-05 07:35, verified working.
