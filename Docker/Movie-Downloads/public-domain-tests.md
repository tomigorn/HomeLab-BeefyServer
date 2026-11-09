# Public-domain test titles

For end-to-end tests of the download pipeline (request → download → import → metadata → Jellyfin → Seerr). Public domain in Switzerland: director, writer and composer dead more than 70 years. Not US-only public domain (e.g. *Night of the Living Dead*).

**Delete the test titles only once the whole test is done** (night jobs, subtitles, transcodes, the nightly checks: this can take several days). Then: (Seerr → title → Advanced → Clear Data; series without a Seerr entry: Sonarr → delete with files). Nothing from a test stays in the library.

## Films

| Title | TMDB | Last test | Notes |
|---|---|---|---|
| Nosferatu (1922) | 653 | 2026-10-31 | usenet, German DL 1080p |
| Battleship Potemkin (1925) | 643 | 2026-10-31 | usenet, no 4K exists |
| The Cabinet of Dr. Caligari (1920) | 234 | 2026-10-31 | usenet, German DL 2160p |

## Series

| Title | ID | Last test | Notes |
|---|---|---|---|
| The Vampires / Les Vampires (1915) | TVDB 106811 | 2026-10-31 | not requestable in Seerr (TMDB 327121 has no TVDB id): add in Sonarr directly |
| Fantômas (1913) | TMDB 331714 | — | same TVDB problem |

## Cleanup log

| Date | Removed |
|---|---|
| 2026-11-09 | Nosferatu, Battleship Potemkin, The Cabinet of Dr. Caligari (Clear Data), The Vampires (Sonarr, with files) |
