# Recently watched: include unfinished

## Goal
The report site sort "Recently watched by you" also lists films and series you started but did not finish. That is where broken files show up, since you usually stop watching partway. Newest play first.

## What it does
Jellyfin popular `/watched` asks Jellyfin for the user's films and episodes sorted by last play (no `IsPlayed` filter) and keeps every item with a `LastPlayedDate`: finished, partly played, or stopped after seconds.

## Done-list
- [x] Short plays leave a `LastPlayedDate` in Jellyfin. Verified on real data (2026-11-27): every user has items with a play date that are neither played nor resumable; sorting by play date puts all dated items first.
- [x] `/watched` returns finished, partly played and short-played items; never played stays out. Unit tests in `Docker/Jellyfin/popular/test_app.py`.
- [x] Newest play first, finished and unfinished mixed; a started episode ranks its series. Unit tests.
- [x] Limits unchanged: scan 1000, at most 200 films and 200 series. Unit test.
- [x] Real check: `/watched?user=canary-test` after a short-play state on Nosferatu (TMDB 653) returns 653 first.
- [x] Report site: the sort "Recently watched by you" shows a film stopped partway. Checked by the owner (2026-11-27); `shot.sh` cannot pick a dropdown option.
- [x] Seerr README "Problem reports" and the popular `watched` docstring say "played or started".

## Untouched
- `report-www/report.js`, the label, the API shape `{"movie": [...], "tv": [...]}`, `watched_client` in download-now.
- The trending placeholder hooks (MarkPlayed, unmark) in popular.
- Secret, user matching, port 8100, 1-min cache.

## Decisions (2026-11-27)
- "Started" = any play attempt, even a few seconds, not Jellyfin's 5 % resume rule: broken files often fail in the first seconds.
- How far back: same as today (last 1000 plays, max 200 + 200), no time window.
- A started episode puts its series in the list, same as a finished one.
- No "not finished" badge: the list and label stay as they are, and only the backend changes.
