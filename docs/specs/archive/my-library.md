# My library (beefy, Seerr + Movie-Downloads)

Backlog item "My library page in Seerr (beefy)", rules OK'd by the owner 2026-11-27 ("build push").

## Goal
A Seerr sidebar entry **My library** lists the downloaded titles with their state and the Keep forever / Delete early actions. One rule set for every place: title page, My library, API, delete runner, nightly retention, Jellyfin pop-ups.

## Rules (owner 2026-11-27, unchanged)
- Same rules everywhere (title page, My library, API, delete runner, nightly retention, Jellyfin pop-ups): one shared rules module, no view with own rules.
- K1 Automatic Keep forever (old >= 12 y, niche, goated IMDb >= 8.0 + >= 25k votes): always on, nobody removes it, never deletable early.
- K2 Fan favourite: any streaming-requesters member marks any title, any time (title page, My library, the request box).
- K2b At most 5 fan-favourite marks per person per 7 days; the request box counts; removing a mark does not free the slot.
- K3 Only the person who set a fan-favourite mark removes it; nobody else, admin included.
- K4 Keep forever: still SSD -> HDD, retention never deletes it.
- D1 Delete early: any group member, any title (film, series, season).
- D2 Never deletable: Nosferatu, keep-test, automatic Keep forever.
- D3 Fan favourite -> request for OK to the person who set the mark.
- D4 Someone else mid-watch (started <= 30 days, unfinished) -> request to that viewer; own watching doesn't block; viewer without Seerr account -> owner answers.
- D5 Requests: Telegram where possible, Jellyfin pop-up, Approve/Deny box; no answer in 7 days = denied.
- D6 10 deletions per person per 7 days, pending counts, denied doesn't.
- D7 Bulk in My library up to the remaining limit, one confirm listing every title (which go to the bin now, which become requests).
- D8 Recycle bin 7 days, placeholder stays in Seerr, restore by deleter, requester or admin. D9 requester told with undo link, audit log.
- My library: new Seerr sidebar entry listing downloaded titles with state (Temporary / Keep forever / Leaving soon / In recycle bin) and the actions above, EN/DE, works on phone width. Reuse retention_api.py, retention.js, delete-early.py, retention.py.

## Design
| Part | What |
|---|---|
| Rules | `Seerr/download-now/retention_rules.py` (already shared by the backend, `retention.py` and `delete-early.py`): automatic keep can't be overridden (K1), fan marks per person with the 5-per-7-days count (K2, K2b, K3), `delete_plan()` = refused or the list of asks (D2, D3, D4), the limits (D6, ask and bin days) |
| Store | `keep.json` per title `marks` `{uid: {at, name}}` + `keep` (= any mark, read by `retention.py`), top-level `marked` log for K2b. Old records: `keep: true` → a mark by `by` (the requester who ticked the box); `keep: false` → no mark |
| Backend | `retention_api.py` POST `keep` = set / remove my mark (group + request right); `delete_early.py` refuses automatic Keep forever, asks setters, POST `bulk`; new `my_library.py` GET `/_download-now/library/` (one list for the page) |
| Runner | `delete-early.py` asks the setters (fan) instead of requesters, refuses automatic Keep forever, writes `delete-early-watching.json` (who is mid-watch, for the confirm preview; checked again when it runs) |
| UI | `my-library.js`: page `/my-library` (like Collections); `retention.js`: title page row (⭐ Fan favourite / Remove my mark), request box = my mark, confirm with the plan; `menu-links.js` sidebar entry |

## Done-list
- [x] 1 One shared rules module decides keep, marks, refusals, asks and limits; backend, runner, retention and UI use it (no own rules in views) — ✅ `retention_rules.py` used by backend, runner, retention; UI only shows server answers
- [x] 2 K1 automatic Keep forever can't be switched off or deleted early (API 403/409, runner refuses) — ✅ API 403 kept / locked, runner refused (unit), retention ignores old "let it leave" (test fails without the rule); live: Nosferatu and Keep forever rows without buttons
- [x] 3 K2/K3 fan mark set from title page, My library and request box; only the setter removes it (admin too refused) — ✅ API + JS tests (title page, My library, request box); admin refused (403 not your mark)
- [x] 4 K2b 6th mark in 7 days refused; removing doesn't free the slot; request box counts — ✅ API tests (6th 429 mark-quota, removing keeps slot, 7-day window)
- [x] 5 K4 fan / automatic titles still move SSD → HDD, never deleted by retention — ✅ retention tests (tier unchanged with a mark, never deleted)
- [x] 6 D3 deleting a fan favourite → request to the setter(s); own mark doesn't block — ✅ runner + API tests (each setter, own mark free, old record → its `by`)
- [x] 7 D4/D5/D6/D8/D9 unchanged behaviour still passes (tests) — ✅ existing tests pass unchanged
- [x] 8 My library sidebar entry (EN/DE), list with state Temporary / Keep forever / Leaving soon / In recycle bin, filters, actions; phone width 390 px without horizontal scroll — ✅ JS + browser 1280/390; live canary list (223 titles), verifier passed
- [x] 9 D7 bulk delete up to the remaining limit, one confirm listing bin-now vs request titles — ✅ API + JS + browser; verifier passed (stand-in data: no deletable public-domain title exists, all are automatic Keep forever)
- [x] 10 Asked-of-you box (Approve / Deny) on My library — ✅ JS + browser; verifier passed (stand-in data)
- [x] 11 Tests (Python, JS, browser layout) pass; deploy; Seerr + Movie-Downloads post-update checks pass — ✅ Python all, JS 459, browser; deployed; Seerr 104/104, Movie-Downloads 135/136 (md.qbit-categories, pre-existing)
- [x] 12 Real path with the canary on a public-domain title through https://requests.holy-grail.ch; canary disabled and stripped after — ✅ canary through requests.holy-grail.ch: list, Mine, Nosferatu locked, Potemkin bin + Restore, POST refusals 403/409 on the test film; canary disabled, no groups
- [x] 13 Verifier screenshots: My library, title page row, bulk confirm, pending request box — ✅ passed (stand-in + live shots)
- [x] 14 READMEs, CUSTOMIZATIONS.md rows and checks updated — ✅ Seerr + Movie-Downloads READMEs, CUSTOMIZATIONS rows, checks seerr.library-*, md.delete-early-watching

## Untouched
Retention dates and the nightly delete run, recycle-bin cleanup, Jellyfin, fastpi (Traefik, Authentik).

## Decisions (2026-11-27, not covered by the rules: taken as stated, listed for the owner)
- Setter of an existing per-request Keep forever mark: the requester who ticked the box (`by`). Live store had none; its only record was a "Let it leave" on Black Clover (automatic, goated), which K1 makes Keep forever again.
- The deleter's own fan mark doesn't block (as own watching, D4); several setters: each must OK (as each viewer, D4).
- A fan mark on an automatic Keep forever title is not offered (already kept, would use a slot).
- My library lists every downloaded title (D1, K2: any title), filter **Mine** = requested or marked by me.
- Bulk deletes whole titles; single seasons stay on the title page.
- Titles deleted by the nightly retention are not restorable from My library (as before: only Delete early bin items).
- Migrated marks count for K2b with their original date.
