# Subtitle-Translate: translated German/English subtitles as a fallback

When a film or episode has a human subtitle in some language but none in German or English, and Bazarr found none after 48 h, a translated one is made at night. A human subtitle always wins: when Bazarr finds one later, the translation is deleted. Everything runs locally (LibreTranslate + Lingarr), nothing leaves beefy.

| Piece | What |
|---|---|
| `subtitle-libretranslate` | LibreTranslate v1.9.6, offline Argos models; internal network only (no egress, no port) |
| `subtitle-lingarr` | Lingarr 1.4.1, translation queue in front of LibreTranslate (`/api/translate/content`); port `127.0.0.1:9876` only, login + API key on, no Traefik route, no library mount |
| `scripts/subtitle-translate.py` | picks the titles and the source, extracts embedded tracks, calls Lingarr, writes the file, removes replaced ones, refreshes the Jellyfin item |
| `systemd/subtitle-translate.{service,timer}` | daily 04:46 (after the maintenance window), no new title after 05:50, one title at a time, holds `beefy-keep-awake` while running |

## Rule (owner, 2026-10-31)

| Situation | Translated into | Source, first found wins (never Forced, never a translated file) |
|---|---|---|
| German missing | German | English HI › English › original HI › original › any other (HI first) |
| English missing | English | German HI › German › original HI › original › any other (HI first) |
| both missing | both, from one source | original HI › original › any other (HI first) |
| only Forced subtitles, or none | nothing | - |

- "Missing" = no human subtitle in that language that is not Forced, external or embedded, as Bazarr lists them.
- Waits until the video file is 48 h old (Radarr/Sonarr `dateAdded`, ≈ 2 nightly searches; `ST_WAIT_HOURS` in the unit). An upgrade starts the wait again.
- Within a rung: external before embedded (Bazarr already synced it, and the video is not read).
- Embedded source: text tracks only (SRT, ASS); `mkvextract` reads the whole video once (HDD spin-up for cold titles). A bitmap track is remembered and not read again.
- Original language: Radarr/Sonarr `originalLanguage`. Tamil has no Argos model: Tamil-only titles get nothing.

## Output

| Source | File | Jellyfin shows |
|---|---|---|
| normal | `<video>.German (translated).de.srt` | `German (translated) - SUBRIP - External` |
| HI / SDH | `<video>.German (translated).de.hi.srt` | `German (translated) - Hearing Impaired - SUBRIP - External` (checked live) |

- Cue by cue: index and timing lines are copied unchanged, only the text is translated (line breaks, `<i>` kept); a cue without letters (`♪ ♪`) stays as it is. Written only when every timestamp matches the source.
- Bazarr does not index the file (strict name match: `<video>.German (translated)` is not the video name), so German stays "missing" and Bazarr keeps searching.
- Replacement: each run, a translation whose language now has a human subtitle in Bazarr is deleted and the Jellyfin item refreshed (`replaced by a human subtitle` in the log).
- Jellyfin re-reads the item after every write and removal (`POST /Items/{id}/Refresh`), and again (up to 3×) until it lists exactly the translated files: one refresh was skipped in the live test.
- ALL-CAPS cues (`(DRUMROLL PLAYING)`) go out in sentence case and come back in caps: the models mangle caps.

## Run

```bash
scripts/subtitle-translate.py --dry-run                  # what would be translated, from what
scripts/subtitle-translate.py --only "Kill Bill" --max 1 # one title now (no deadline by hand)
sudo systemctl start subtitle-translate.service; journalctl -u subtitle-translate -n 30
cat /srv/appdata/subtitle-translate/status.json          # last run: translated, replaced, failed, seconds per title
```

| Path | What |
|---|---|
| `/srv/appdata/subtitle-translate/state.json`, `status.json` | written translations (file → video, language, source), failures, bitmap tracks; last run |
| `/srv/appdata/lingarr` | Lingarr DB (sqlite) and key ring |
| `/srv/appdata/libretranslate` | Argos models (~4 GB) |
| `secrets/lingarr.apikey`, `secrets/lingarr.password` | 600, gitignored; user `admin` |

## Measured (2026-10-31)

| Title | Source | Cues | Extract | Translate | Total |
|---|---|---|---|---|---|
| Kill Bill: The Whole Bloody Affair (4 h, 91 GB, SSD) | embedded English SDH | 2456 | ~3 min | ~13 min | 16 min |
| I'm Still Here (2024, Portuguese) | external English HI | 1710 | - | ~10.5 min | 10.5 min |
| Curb Your Enthusiasm S01E10 (timer unit, sandboxed) | embedded English SDH | 847 | <1 min | ~4.5 min | 5.3 min |

About 2.7 cues/s on 3 cores: 4-10 films or ~10 episodes a night. Backlog 2026-10-31: ~180 titles (mostly episodes without German), so a few weeks.

## CPU

LibreTranslate: `cpus: 3` (`TRANSLATE_CPUS`), `cpu_shares: 128`, one worker; the script `Nice=19`, `IOSchedulingClass=idle`, `CPUWeight=20`. Jellyfin transcodes on the iGPU, so playback is not affected.

## Languages

`LT_LANGUAGES` in `.env` (Argos codes): en, de, fr, es, it, pt, ru, sv, da, nb, nl, pl, ja, ko, zh, zt, fa, hi (library originals 2026-10-31: en, de, es, ru, pt, fa, ja, it, da, zh, ta). Add one:

```bash
scripts/install-models.sh --list                       # codes Argos has
# append the code to LT_LANGUAGES in .env and .env.example, then:
scripts/install-models.sh                              # downloads (edge network), recreates LibreTranslate
```

## Deploy

```bash
sudo install -d -o 65130 -g buntu -m 2770 /srv/appdata/libretranslate
sudo install -d -o 65129 -g buntu -m 2770 /srv/appdata/lingarr
install -d -m 2770 /srv/appdata/subtitle-translate
cp .env.example .env && chmod 600 .env
scripts/install-models.sh          # models, starts LibreTranslate
sudo docker compose up -d
scripts/lingarr-setup.sh           # onboarding, admin user, API key, login on (idempotent)
sudo install -m 0644 systemd/subtitle-translate.service systemd/subtitle-translate.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now subtitle-translate.timer
python3 tests/test_subtitle_translate.py
```

## Security

- uids 65129 (Lingarr), 65130 (LibreTranslate); `cap_drop: ALL`, `no-new-privileges`, non-root, `mem_limit`.
- Network `subtitle-translate-internal` is `internal: true`: LibreTranslate has no egress; Lingarr's second network exists only for the loopback port. Telemetry off, `LT_UPDATE_MODELS=false`.
- Lingarr gets no Radarr/Sonarr keys and no media mount: the script sends text lines, not paths.
- The script runs sandboxed (`systemd-analyze security subtitle-translate`): buntu, loopback only, writes only its state dir and `/srv/video/media`.
- Lingarr UI if ever needed: `ssh -L 9876:127.0.0.1:9876 beefy`, then `http://localhost:9876` (admin, `secrets/lingarr.password`).

## Update

Tier notify (`_global/auto-update.conf`): Lingarr migrates its DB. `Docker/_global/scripts/update-project.sh Docker/Subtitle-Translate pull /srv/appdata/lingarr`, then `post-update-check.sh Subtitle-Translate`.

## Tests

`python3 tests/test_subtitle_translate.py`: source order (German only, English only, both from French, Forced only → nothing, no chaining), Bazarr's strict name match, timing-exact rewrite, ASS input, wait, replacement, dry run, deadline.
