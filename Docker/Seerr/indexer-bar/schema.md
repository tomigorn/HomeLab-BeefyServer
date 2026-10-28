# Shared bar: config and data

One codebase for the bar above Seerr (beefy) and AudioBookRequest (fastpi).

| | Where |
|---|---|
| canonical | HomeLab-BeefyServer `Docker/Seerr/indexer-bar/www/bar.js`, `bar.css`, this file |
| vendored copy (identical) | HomeLab-FastPi `Docker/Audiobook-Requests/books-proxy/www/bar/bar.{js,css}`, `abr-bar/indexer-bar-schema.md` |
| sync | fastpi `Docker/Audiobook-Requests/scripts/sync-bar.sh` (copy), `--check` (hash compare, post-update check `books.bar-shared`) |

Rule: change the bar only here (beefy), run both test suites, then `sync-bar.sh` on fastpi. App wording lives in each collector, never in `bar.js`.

## Config: attributes on the `<script src=".../bar.js">` tag

| Attribute | Required | Seerr | ABR |
|---|---|---|---|
| `data-ib-data` | yes (else no bar) | `/_indexer-bar/usage.json` | `/_hg/bar/usage.json` |
| `data-ib-quota` | no | – | `/_hg/bar/my-quota.json` |
| `data-ib-no-bar` | no | `^/(login\|setup\|resetpassword)\b` | `^/(login\|auth\|init)\b` |
| `data-ib-lang-hook` | no | `hgNightLabel` (global with `lang()`) | – |
| `data-ib-lang-probe`, `data-ib-lang-de` | no | `a[href="/discover/tv"]`, `Serien` | – |

Language: hook, else probe text contains `data-ib-lang-de`, else browser language (`de*` = German).

Auth: the bar exists only after a 200 from `data-ib-data`; 401, 403 or a redirect (fetch with `redirect: "manual"`) hides it and retries every 4 s; never on `data-ib-no-bar` paths. The data paths need an app session (proxy `auth_request`).

## usage.json

| Field | Type | Meaning |
|---|---|---|
| `ok` | bool | false = Prowlarr not reachable ("usage unavailable"), rest still valid |
| `generated` | ISO UTC | older than 10 min = shown as unavailable |
| `indexers[]` | list | `name`, `limited`, `window` (`rolling`/`day`), `level`, `searches`/`downloads` = {`used`, `limit` (null = ∞), `level`, `resetsAt`} |
| `showUnlimited` | bool, optional | unlimited indexers also in the bar (not only under more) |
| `subscriptions[]` | list | `name`, `kind`, `lifetime`, `expires`, `daysLeft`, `expired`, `level` |
| `storage[]` | list | `name`, `id` (optional, chip key), `total`/`used`/`free` bytes, `pct`, `level`, `yellow`/`red` %, optional `parts` (% per member) + `partsLabel`, `label`/`sub`/`hint` = {`en`, `de`} |
| `note` | {`en`, `de`} or string, optional | last line of the panel |

Levels: `ok`, `yellow`, `warn`, `orange`, `full` (green → red).

## Quota (optional)

`{used, limit, days, freesAt}`: the viewer's own count only; `freesAt` (ISO UTC) when used up.
