# Delete early (beefy, Seerr + Movie-Downloads)

Backlog item "Delete early button for requesters", owner decisions 2026-11-22. Pre-approved (batch).

## Goal
A `streaming-requesters` member deletes a film, a whole series or one season before the 3-month retention ends, from the Seerr title page. Deleted files go to the 7-day recycle bin and can be restored; the title stays a placeholder in Seerr.

## Design
| Part | What |
|---|---|
| Backend | `Seerr/download-now/delete_early.py`, `/_download-now/delete-early/` (GET `state`, POST `delete` / `answer` / `restore`): Seerr session + `X-authentik-groups` contains `streaming-requesters` (set by fastpi Traefik's Authentik forward auth, overwritten per request), Nosferatu refused, quota, Origin + JSON. Writes job files only (no keys for deleting) |
| Jobs | `/srv/appdata/seerr-delete-early/*.req` (65127:buntu 2770), like Scout requests |
| Runner | `Movie-Downloads/scripts/delete-early.py` (buntu, `delete-early.path` on new jobs, `delete-early.timer` every 30 min for the 7-day expiry): checks Keep forever, mid-watch (Jellyfin), quota; deletes the way retention does (unmonitor + tag `retention-deleted`, Radarr/Sonarr file delete into the bin, phone versions with `recycle()`, `retention-gone.json`), restores from the bin, notifies |
| State | `~/.local/state/movie-downloads/delete-early.json` (read by the backend through the existing `/run/scouts` mount), audit `delete-early-audit.jsonl` |
| Notify | Jellyfin pop-up on the person's live sessions (`/Sessions/{id}/Message`, as phone-fetch); Telegram: owner (Seerr user 1) through `oob-alert.sh`, others only if listed in `Movie-Downloads/secrets/delete-early-telegram.json` (none today) |
| UI | `retention.js`: fact row **Delete early** on the title page (button, season choice for series, inline confirm, pending / approve-deny box, bin + Restore), EN/DE |

## Done-list
- [x] 1 Film, whole series, single season can be deleted early from the title page — ✅ film live (canary, real path); series/season: unit + JS tests only
- [x] 2 Only `streaming-requesters` members: checked server-side in the API (403 without the group header), not only hidden — ✅ API refuses without the header (post-update check, admin key → 403); live canary with the group → allowed
- [x] 3 Group check reliable or fail-closed (no header = refused for everyone) — ✅ fail-closed (no header = refused); header from Traefik forward auth
- [x] 4 Keep forever title, deleter not its requester → pending request to the requester (no requester: owner) — ✅ unit tests (runner + API)
- [x] 5 Someone else mid-watch (started in last 30 days, not finished) → pending request to that viewer — ✅ unit tests (film versions, series, season, own/finished/old excluded)
- [x] 6 Pending: Telegram where reachable + Jellyfin pop-up + Approve/Deny box on the title page; no answer in 7 days = denied — ✅ unit + JS tests; Telegram path checked against an invalid chat (400 = token ok); no live approval (only one test account)
- [x] 7 Deleted files go to the 7-day recycle bin; Restore button puts them back — ✅ live: bin `_2` copy, restore moved it back, Radarr rescan hasFile
- [x] 8 Title stays a placeholder in Seerr (no Seerr media row deleted, no clear-data-wipe), requestable again — ✅ live: Seerr row + request kept, status unknown → Request button visible; Radarr entry kept unmonitored
- [x] 9 Requester told (Telegram / Jellyfin pop-up) who deleted their title, with an undo link while in the bin — ✅ unit tests (Notifier: pop-up DE/EN, Telegram); live not triggered (canary deleted its own title)
- [x] 10 At most 10 deletions per user per 7 days (server-side, backend and runner) — ✅ unit tests backend + runner; live quota counter 4/10
- [x] 11 Nosferatu (TMDB 653) and `keep-test`/protected titles can never be deleted — ✅ live real path: protected; post-update check 403
- [x] 12 Confirm step before every delete — ✅ live screenshot confirm.png + JS test
- [x] 13 Audit log of requests, deletions, approvals, denials, expiries, restores — ✅ live delete-early-audit.jsonl lines
- [x] 14 UI consistent with retention.js, EN/DE — ✅ JS tests EN/DE, screenshots 1280/390
- [x] 15 CUSTOMIZATIONS.md row + post-update checks; Seerr + Movie-Downloads READMEs — ✅ rows + 5 checks, READMEs
- [x] 16 Tests (unit Python, JS) pass; deploy; post-update check Seerr + Movie-Downloads all-pass — ✅ Python + 440 JS pass; post-update Seerr 101/101, Movie-Downloads 134/135 (md.qbit-categories, pre-existing)
- [x] 17 Real path through https://requests.holy-grail.ch with the canary on one public-domain test title: delete → bin + placeholder → restore → delete; left in the bin — ✅ Battleship Potemkin (TMDB 643), left in the bin until 2026-11-29

## Untouched
Retention rules and the nightly delete run, Seerr's own Manage buttons / Clear Data, Jellyfin (no restart), fastpi (Traefik, Authentik), recycle-bin cleanup, keep.json format.

## Decisions (batch, 2026-11-22)
- Approval for Keep forever: any one requester of the title; none → owner (Seerr user 1).
- Mid-watch: each other viewer must OK; a viewer without a Seerr account → owner answers for them.
- Restore: the deleter, a requester of the title, or a Seerr admin / manager.
- Quota counts deletions done + pending (denied ones do not count).
- Group trust: the header comes from Traefik's Authentik forward auth; port 5055 is reachable only from fastpi. Hardening (shared secret like report.holy-grail.ch) is a fastpi change: reported, not made.
- Seerr has no `deleted` status in `POST /media/<id>/<status>`: a whole-title delete sets *unknown* (film requestable at once; a series' seasons after the nightly availability sync), restore sets *available*.
- Side files (`<video>.de.srt`) are recorded and restored with the video; one the arr leaves behind goes to the bin through `recycle()`.
- Commit prefix for the runner: `Movie-Downloads:` (repo CLAUDE.md: the project folder), the backend/UI `Seerr:`.
