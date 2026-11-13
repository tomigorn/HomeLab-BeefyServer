# Public-domain test titles

For end-to-end tests of the download pipeline (request → download → import → metadata → Jellyfin → Seerr). Public domain in Switzerland: director, writer and composer dead more than 70 years. Not US-only public domain (e.g. *Night of the Living Dead*).

## Permanent test film — never delete

**Nosferatu (1922, TMDB 653)** stays in the library for good (owner 2026-11-12).

| | |
|---|---|
| Depends on it | `jf.continue-remove-api` (Jellyfin post-update check, `../Jellyfin/scripts/continue-remove-check.py`), the end-to-end pipeline tests (request, download, import, metadata, subtitles, Jellyfin, Seerr) |
| Radarr | tag `keep-test`, profile `4K preferred · older versions allowed` (silent film, [README "Silent films"](README.md#silent-films)) |
| Protected | Seerr Clear Data / My request delete → `scripts/clear-data-wipe.sh` → `title-wipe-hook.sh` wipes nothing for a `keep-test` title (log `kept (tag keep-test …)`). No other script deletes library titles (checked 2026-11-12: queue watchdog and grab guard remove queue downloads only, request-tags only `bz-*` tags, no import list sync) |
| Not protected | Seerr **Remove from Radarr**, a delete in Radarr's or Jellyfin's UI: by hand only |
| Missing | queue watchdog digest line `test film missing` / `test film untagged` (morning digest). The Jellyfin check then uses another film (`ok fallback film …`) and never fails, so a missing film does not roll back updates |
| Delete request | the owner asks to delete old films or test titles: leave Nosferatu out, pause, tell him it is the test film and what depends on it. Only after he confirms this one film: remove the `keep-test` tag, delete, then fix the checks above |

### Status

| Date | What |
|---|---|
| 2026-11-12 | requested again in Seerr (owner account, request 162) → Radarr movie 148, profile 11 (`old-rare`), tag `keep-test`; grabbed `Nosferatu.A.Symphony.of.Horror.1922.1080p.BluRay.GERMAN.DTS.x264-MAJO` (Treasure Maps, usenet, 6.5 GB download, CF 2000750) into `SABnzbd night` |
| 2026-11-12 | imported the same day (Download now, owner request): Radarr movie 148 has the file, tag `keep-test`; Jellyfin item present; `jf.continue-remove-api` prints `ok Nosferatu: …`; post-update check Jellyfin 103/103 |
| 2026-11-13 | file: 1080p h264, **one DTS 5.1 audio track**, subtitles English (ASS) + Czech; runtime 95:11. No track title names the score; the 5.1 track and ~95 min runtime match the 2006 Murnau-Stiftung restoration with the reconstructed Hans Erdmann score (Blu-ray releases), not confirmed from the file itself |

## Test titles (deleted after the test)

**Delete them only once the whole test is done** (night jobs, subtitles, transcodes, the nightly checks: this can take several days). Then: (Seerr → title → Advanced → Clear Data; series without a Seerr entry: Sonarr → delete with files). Nothing from a test stays in the library, **except Nosferatu** (above).

### Films

| Title | TMDB | Last test | Notes |
|---|---|---|---|
| Nosferatu (1922) | 653 | 2026-11-12 | **permanent, never delete**; usenet, German 1080p Blu-ray |
| Battleship Potemkin (1925) | 643 | 2026-10-31 | usenet, no 4K exists |
| The Cabinet of Dr. Caligari (1920) | 234 | 2026-10-31 | usenet, German DL 2160p |

### Series

| Title | ID | Last test | Notes |
|---|---|---|---|
| The Vampires / Les Vampires (1915) | TVDB 106811 | 2026-10-31 | not requestable in Seerr (TMDB 327121 has no TVDB id): add in Sonarr directly |
| Fantômas (1913) | TMDB 331714 | — | same TVDB problem |

## Cleanup log

| Date | Removed |
|---|---|
| 2026-11-09 | Nosferatu, Battleship Potemkin, The Cabinet of Dr. Caligari (Clear Data), The Vampires (Sonarr, with files). Nosferatu was needed by `jf.continue-remove-api`: every Jellyfin auto-update then rolled back; requested again 2026-11-12 and made permanent |
