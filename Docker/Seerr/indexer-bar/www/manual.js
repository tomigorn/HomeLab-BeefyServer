// Manual interaction page /manual inside Seerr's layout (README "Manual interaction"): Seerr shows its 404 there, this script (injected by seerr-proxy) puts the page in its place. Every download problem that needs a hand, for every Seerr user; data and actions from download-now's /_download-now/manual/. All text goes in as textContent, never as markup.
(function () {
  "use strict";
  var API = "/_download-now/manual/", ROUTE = "/manual", POLL = 3000;
  var CSS = "#hg-manual { max-width: 56rem; margin: 0 auto; padding: 1rem 1rem 7rem; color: #e5e7eb; font-size: 15px; line-height: 1.5; }" +
    "main[data-hg-manual] > :not(#hg-manual) { display: none !important; }" +
    "#hg-manual * { box-sizing: border-box; }" +
    "#hg-manual .hg-m-intro { margin: 0 0 1rem; color: #9ca3af; }" +
    "#hg-manual a { color: #a5b4fc; }" +
    "#hg-manual h1 { margin: .5rem 0 .25rem; font-size: 1.75rem; font-weight: 700; color: #fff; }" +
    "#hg-manual h2 { margin: 0; font-size: 1.1rem; color: #fff; overflow-wrap: anywhere; }" +
    "#hg-manual h2 a { color: #fff; text-decoration: none; }" +
    "#hg-manual h2 a:hover { text-decoration: underline; }" +
    ".hg-m-tabs { display: flex; gap: .5rem; margin: 1rem 0; }" +
    ".hg-m-tab { background: #1f2937; color: #d1d5db; border: 1px solid #374151; border-radius: .5rem; padding: .4rem .9rem; font: inherit; cursor: pointer; }" +
    ".hg-m-tab.hg-m-on { background: linear-gradient(to bottom right, #4f46e5, #9333ea); color: #fff; border-color: transparent; }" +
    ".hg-m-card { background: #1f2937; border: 1px solid #374151; border-radius: .75rem; padding: 1rem; margin: 0 0 1rem; }" +
    ".hg-m-item { border-top: 1px solid #374151; margin-top: .75rem; padding-top: .5rem; }" +
    ".hg-m-type { display: inline-block; margin: .4rem 0; padding: .1rem .6rem; border-radius: 999px; background: #78350f; color: #fde68a; font-size: .85rem; }" +
    ".hg-m-meta, .hg-m-details, .hg-m-hint, .hg-m-resolved { margin: .25rem 0; color: #d1d5db; overflow-wrap: anywhere; }" +
    ".hg-m-hint { color: #9ca3af; font-size: .9rem; }" +
    ".hg-m-actions { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-top: .75rem; }" +
    ".hg-m-actions [data-list] { flex-basis: 100%; }" +
    ".hg-m-btn { background: #374151; color: #fff; border: 1px solid #4b5563; border-radius: .375rem; padding: .45rem .9rem; font: inherit; cursor: pointer; }" +
    ".hg-m-btn:hover:not(:disabled) { background: #4b5563; }" +
    ".hg-m-btn:disabled { opacity: .5; cursor: default; }" +
    ".hg-m-primary { background: #4f46e5; border-color: #6366f1; }" +
    ".hg-m-primary:hover:not(:disabled) { background: #6366f1; }" +
    ".hg-m-danger, .hg-m-armed { background: #b91c1c; border-color: #dc2626; }" +
    ".hg-m-input, select { background: #111827; color: #fff; border: 1px solid #4b5563; border-radius: .375rem; padding: .4rem .6rem; font: inherit; max-width: 100%; }" +
    ".hg-m-input { flex: 1 1 14rem; }" +
    ".hg-m-rows { list-style: none; margin: .5rem 0; padding: 0; }" +
    ".hg-m-rows li { border-top: 1px solid #374151; padding: .5rem 0; display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }" +
    ".hg-m-rows label { display: flex; flex-wrap: wrap; gap: .25rem .6rem; align-items: baseline; flex: 1 1 20rem; cursor: pointer; }" +
    ".hg-m-name { color: #fff; overflow-wrap: anywhere; }" +
    ".hg-m-facts { color: #9ca3af; font-size: .85rem; }" +
    ".hg-m-why { color: #fca5a5; font-size: .85rem; flex-basis: 100%; }" +
    ".hg-m-rejected { opacity: .6; }" +
    ".hg-m-probable, .hg-m-suggest { color: #86efac; font-size: .85rem; }" +
    ".hg-m-suggest { flex-basis: 100%; margin: 0; }" +
    ".hg-m-warning { flex-basis: 100%; margin: 0; padding: .5rem .75rem; border-radius: .375rem; background: #450a0a; color: #fecaca; }" +
    ".hg-m-msg { margin: .5rem 0 0; color: #a5b4fc; min-height: 1.2em; }" +
    ".hg-m-bad, .hg-m-error { color: #fca5a5; }" +
    ".hg-m-flash { padding: .6rem .9rem; border-radius: .5rem; background: #14532d; color: #bbf7d0; }" +
    ".hg-m-empty { color: #9ca3af; }" +
    ".hg-m-history { margin-top: .5rem; color: #9ca3af; font-size: .9rem; }" +
    ".hg-m-history ul { margin: .25rem 0; padding-left: 1.2rem; }" +
    ".hg-m-item > * + * { margin-top: .6rem; }" +
    ".hg-m-section { margin: .6rem 0 0; }" +
    ".hg-m-section p { margin: .15rem 0; }" +
    ".hg-m-head { margin: 0 0 .15rem; font-size: .75rem; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; color: #9ca3af; }" +
    ".hg-m-file { display: flex; flex-direction: column; gap: .1rem; }" +
    ".hg-m-label { font-size: .8rem; color: #9ca3af; }" +
    ".hg-m-filename { font-size: .8rem; color: #d1d5db; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }" +
    ".hg-m-media, .hg-m-virus { font-size: .85rem; color: #d1d5db; }" +
    ".hg-m-virus-malware { color: #fca5a5; font-weight: 600; }" +
    ".hg-m-virus-flagged, .hg-m-virus-partial, .hg-m-virus-runtime { color: #fde68a; font-weight: 600; }" +
    ".hg-m-virus-clean { font-weight: 600; }" +
    ".hg-m-virus-src { margin: 0; padding-left: 1.5rem; font-size: .8rem; color: #9ca3af; }" +
    ".hg-m-checks { list-style: none; margin: .3rem 0 0; padding: 0; font-size: .85rem; }" +
    ".hg-m-checks li { padding: .1rem 0; border: 0; display: block; }" +
    ".hg-m-check-bad { color: #fca5a5; }" +
    ".hg-m-check-warn { color: #fde68a; }" +
    ".hg-m-check-none { color: #9ca3af; }" +
    ".hg-m-info { color: #9ca3af; }" +
    ".hg-m-scout { font-size: .85rem; color: #d1d5db; }" +
    ".hg-m-lines { margin: .15rem 0 0; padding-left: 1rem; color: #9ca3af; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .8rem; overflow-wrap: anywhere; }" +
    ".hg-m-raw { font-size: .8rem; color: #9ca3af; }" +
    ".hg-m-raw summary { cursor: pointer; }" +
    ".hg-m-quote { font-size: .85rem; color: #d1d5db; border-left: 3px solid #4b5563; padding-left: .5rem; overflow-wrap: anywhere; }" +
    ".hg-m-count { margin: .2rem 0; font-size: .85rem; color: #fde68a; }" +
    ".hg-m-wide { flex-basis: 100%; }" +
    ".hg-m-rows .hg-m-checks, .hg-m-rows .hg-m-virus-block, .hg-m-virus-block .hg-m-virus { flex-basis: 100%; margin: 0; }" +
    "#hg-manual h3 { line-height: 1.2; }";
  var TEXT = {
    en: {
      locale: "en-GB", title: "Manual interaction", intro: "Downloads that need a hand. Everyone can help, also with other people's requests.", back: "← Back to Seerr",
      open: "Open", done: "Done", empty: "Nothing needs a hand right now.", emptyDone: "Nothing done yet.", signin: "Sign in to Seerr first", loadError: "Could not load the list. Try again later.",
      types: { removed: "Removed from Radarr/Sonarr", unmatched: "Not matched — Seerr could not hand it over", "import": "Downloaded, but not importable", "wrong-film": "Wrong film downloaded", failed: "Download failed", "no-release": "Nothing found for days" },
      hints: {
        removed: "The request is still open in Seerr, but the title is gone from Radarr/Sonarr, so nothing searches for it.",
        unmatched: "Pick the right title; it is added to Radarr/Sonarr with the usual quality rules.",
        "import": "Check which file is which episode or film, then import.",
        "wrong-film": "The download is a different film (other year). Block it and search again.",
        failed: "Block this release and search for another one.",
        "no-release": "Pick a release by hand, or allow older versions (DVD, SD) for old or rare titles."
      },
      reasons: {
        "id-match": "The release name doesn't match the title (e.g. a foreign-language name), so it isn't imported automatically. Check the episode or film, then import.",
        "no-eligible": "The download holds no file that can be imported (no video file, or only extras).",
        "not-upgrade": "The library already has this in the same or a better quality, so it isn't imported.",
        sample: "The file is only a sample (a short excerpt), not the full film or episode.",
        unparsable: "Sonarr/Radarr can't tell from the file name which episode or film it is.",
        "not-in-release": "The download holds a different episode or film than expected.",
        "wrong-film": "The download is a different film: its year doesn't match.",
        "dub-only": "The download has no audio in the original language (dubbed only).",
        failed: "The download failed (broken or incomplete).",
        "no-release": "No release has been found for days.",
        removed: "The request is still open in Seerr, but the title is gone from Radarr/Sonarr, so nothing searches for it.",
        unmatched: "Seerr could not hand the request over to Radarr/Sonarr (the title was not found there).",
        unknown: "Sonarr/Radarr stopped this download for a reason this page doesn't know yet:"
      },
      marks: { ok: "✅", warn: "⚠️", bad: "❌", none: "–" },
      checks: {
        original: function (x) { return ["Original language" + (x.language ? " (" + x.language + ")" : ""), { ok: "present", bad: "missing, dubbed only", none: "unknown" }[x.mark]]; },
        english: function (x) { return ["English audio", { ok: "present", warn: "missing", none: "unknown" }[x.mark]]; },
        german: function (x) { return ["German audio or subtitles", { ok: "present", none: "none" }[x.mark]]; },
        quality: function (x) { return ["Quality" + (x.quality ? " " + x.quality : ""), { ok: "allowed by the profile", bad: "not allowed by the profile", none: "unknown" }[x.mark], x.upgrade ? "may upgrade to " + x.upgrade + " later" : ""]; },
        hdr: function (x) { return [String(x.hdr || "SDR"), { ok: "allowed by the profile", bad: "Dolby Vision without HDR fallback: the profile rejects it", none: "" }[x.mark], x.upgrade ? "may upgrade to 4K HDR later" : ""]; },
        runtime: function (x) { return ["Running time " + x.minutes + " min (expected " + x.expected + ")", { ok: "fits", warn: "10–25 % off", bad: "more than 25 % off: wrong file, sample or extras?" }[x.mark]]; },
        size: function (x) { return [gb(x.size) + (x.gbh ? " · " + x.gbh + " GB/h" : ""), x.mark === "warn" ? "very big (over " + x.limit + " GB/h)" : ""]; }
      },
      checksOk: "checks", problemsShort: function (n) { return n + (n === 1 ? " problem" : " problems"); }, notesShort: function (n) { return n + (n === 1 ? " note" : " notes"); },
      virusShort: { clean: "virus scan clean", pending: "virus scan pending", infected: "malware found", unknown: "not virus-scanned yet", "parts-clean": "parts scanned clean", "parts-error": "only partly scannable" },
      tooBig: "Too big for the full virus scan",
      bigHead: "Large file",
      bigWhy: "ClamAV scans at most 2 GiB, so the video stream itself is not scanned.", bigParts: "Attachments and subtitles:", bigClean: "scanned clean", bigVideo: "Video:", bigGenuine: "genuine video stream",
      partsError: function (why) { return "parts scan not possible" + (why ? " (" + why + ")" : "") + ": admins only"; },
      verdict: { clean: "✅ No virus found", malware: "❌ Malware — import blocked", flagged: "⚠️ Flagged — admins only", partial: "⚠️ Not fully scannable — admins only", runtime: "⚠️ Running time doesn't match — admins only", checking: "⏳ Still being checked" },
      clamRun: function (m, e) { return "ClamAV: large file: attachments and subtitles clean, genuine video, but running time " + (m ? m + " min" : "unknown") + (e ? " vs expected " + e + " min" : ", expected unknown"); },
      bigRun: "Running time:", runWhy: function (m, e) { return (m ? m + " min" : "unknown") + ", expected " + (e ? e + " min" : "unknown") + (m && e ? " — more than 25 % off" : "") + ", so only an admin can import it"; },
      clam: { clean: "ClamAV: clean", "parts-clean": "ClamAV: clean (large file: attachments and subtitles scanned, genuine video)", pending: "ClamAV: scan queued, usually a few minutes",
        infected: "ClamAV: malware found", unknown: "ClamAV: not scanned yet", "parts-error": "ClamAV: too big for the full scan, parts scan not possible" },
      vt: {
        queued: function () { return "VirusTotal: lookup queued, a few minutes"; }, unchecked: function () { return "VirusTotal: not checked yet (retried later), no effect on the import"; },
        unknown: function () { return "VirusTotal: no reports — this exact file isn't known there yet, normal for new downloads, not a warning"; }, clean: function () { return "VirusTotal: known file, no scanner flags it"; },
        flagged: function (v) { return "VirusTotal: flagged by " + v.malicious + (v.malicious === 1 ? " scanner" : " scanners") + ", often a false alarm"; },
        malware: function (v) { return "VirusTotal: known malware (" + v.malicious + " of " + v.engines + " scanners)"; }
      },
      vtShort: { flagged: "flagged by VirusTotal", malware: "known malware" },
      vtHead: "VirusTotal fingerprint", vtChecked: "Checked:", vtHow: "Only the file's SHA-256 fingerprint is looked up; the file is never uploaded.",
      vtWhy: { queued: "Looked up within a few minutes (at most 4 a minute, 500 a day).", unchecked: "The lookup failed or the daily limit is used up; it is retried later. No effect on the import.",
        unknown: "Nobody has submitted this exact file yet: normal for a fresh video download. No effect on the import.", clean: "Known to VirusTotal; no scanner flags it.",
        flagged: "1–2 scanners flag it, often a false alarm: only an admin can import it.", malware: "3 or more scanners flag it as malware: never imported, admins included." },
      detailsLink: "Details ↗", account: "🔒 account needed", scoutNfo: "NFO", scoutDesc: "Description", scoutNone: "no language named",
      scoutReading: "Reading the description… (a minute or two)", scoutRead: "Read description",
      noMatch: "No release found for this title — the indexers don't know a version yet.", olderHint: "For an old or rare title, try “Allow older versions” above.",
      noneAllowed: "Releases were found, but none the profile allows.", notAllowed: function (n) { return n + " more, not allowed — show why"; },
      releaseReasons: { "cf-score": "Its score by our rules is too low (e.g. wrong language, poor source).", "required-terms": "The name lacks a term the profile requires (e.g. the language).",
        quality: "The profile doesn't allow this quality.", existing: "The library already has an equal or better version.", seeders: "Too few seeders.",
        blocklisted: "Blocked earlier (a failed or wrong download).", size: "Too big or too small for the profile.", "wrong-title": "Another title." },
      errorCodes: { "search queue full": "Too many searches are waiting right now: try again in a few minutes.", "too many searches": "You already have a search waiting: this one can start when it is done.",
        malware: "Malware found: this download is never imported.", "virus scan pending": "Wait for the virus scan (usually a few minutes); an admin can import before.",
        flagged: "VirusTotal: some scanners flag this file — only an admin can import it.",
        "rejected file needs an admin": "Sonarr/Radarr rejected this file: only an admin can import it.", "another title needs an admin": "Another title than the requested one: only an admin can link it." },
      whatsWrong: "What's wrong", whatToDo: "What to do", download: "Download", fileLabel: "File name of the download:", raw: "Details from Sonarr/Radarr",
      audio: "Audio", subtitles: "Subtitles", min: "min",
      langs: { por: "Portuguese", eng: "English", ger: "German", deu: "German", gsw: "Swiss German", fre: "French", fra: "French", ita: "Italian", spa: "Spanish", jpn: "Japanese", kor: "Korean", chi: "Chinese", zho: "Chinese", rus: "Russian", dut: "Dutch", nld: "Dutch", pol: "Polish", tur: "Turkish", swe: "Swedish", nor: "Norwegian", dan: "Danish", fin: "Finnish", hin: "Hindi", ara: "Arabic" },
      requestedBy: "Requested by", nobody: "no Seerr request", since: "Since", lastSearch: "Last search", never: "never", profile: "Profile", release: "Release",
      years: function (a, b) { return "Release year " + a + ", film " + b; }, episodes: function (n) { return n + (n === 1 ? " episode" : " episodes"); },
      readd: "Add again", del: "Delete request", delAgain: "Tap again to delete",
      delWarn: "Deletes only the Seerr request(s) of this title, nothing gets downloaded for it any more; the title data in Seerr and any files stay.",
      block: "Block this release and search again", blockAgain: "Tap again to block it", profile2: function (p) { return "Allow older versions (" + p + ")"; },
      suggest: "Suggested: an old title, often only out as DVD or SD.", releases: "Show releases", hideReleases: "Hide releases", loading: "Loading…", searching: "Searching the indexers… (up to 2 min)",
      queued: function (title) { return title ? "Waiting for the search for “" + title + "” to finish, then this starts by itself." : "Waiting for other searches to finish, then this starts by itself."; }, season: "Season",
      override: "Download anyway", overrideHint: "Nothing found for a week: anyone may download a not allowed release anyway. A version the profile allows replaces it as soon as one turns up (searched every night).",
      overriddenBy: function (u) { return "Downloaded anyway by " + (u || "someone"); },
      overrideState: { waiting: "⬇️ Downloading anyway (0 seeders — waits for a seeder, checked every night for a better version)", seeded: function (n) { return "⬇️ Downloading anyway (" + n + " seeders — checked every night for a better version)"; },
        downloading: "⬇️ Downloading anyway (checked every night for a better version)", finished: "✅ Downloaded anyway — checked every night for a better version",
        replacing: "🔄 A version the profile allows was found — it downloads instead", replaced: "✅ Replaced by a version the profile allows" },
      grab: "Download", noReleases: "No releases found.", pickOne: "Pick one first.", rejected: "not allowed: ", seeders: "seeders", score: "score",
      lookup: "Search", link: "Use this one", probable: "Most likely", inLibrary: "already in the library", noResults: "Nothing found.",
      files: "Check files to finish importing", hideFiles: "Hide files", "import": "Import", detected: "as detected", noEpisode: "—", needEpisode: "Pick an episode for every checked file.",
      history: "What happened", gone: "Went away by itself", imported: "Imported", found: "Found and downloaded",
      removedBy: function (u) { return "Removed by admin" + (u ? " (" + u + ")" : ""); }, fixedBy: function (u, a) { return "Fixed by " + (u || "someone") + " (" + a + ")"; },
      actions: { readd: "added again", "delete": "deleted", block: "blocked, searched again", profile: "older versions allowed", grab: "release picked", link: "matched", "import": "imported", reopened: "back again",
        override: "downloaded anyway", "override-downloading": "downloading", "override-finished": "download finished", "override-replacing": "better version found", "override-replaced": "replaced by a version the profile allows" },
      errors: { 429: "Too many tries — wait a moment and try again.", 403: "Not allowed — an admin can do this.", 400: "That does not fit any more — reload the page.", 404: "Already solved — reload the page.", other: "Radarr/Sonarr not reachable — try again later." },
      ok: "Done: ", working: "Working…", problems: function (n) { return n + " problems"; }
    },
    de: {
      locale: "de-CH", title: "Manuell beheben", intro: "Downloads, die Hilfe brauchen. Alle können helfen, auch bei Anfragen von anderen.", back: "← Zurück zu Seerr",
      open: "Offen", done: "Erledigt", empty: "Gerade braucht nichts Hilfe.", emptyDone: "Noch nichts erledigt.", signin: "Zuerst bei Seerr anmelden", loadError: "Liste nicht ladbar. Später nochmals versuchen.",
      types: { removed: "Aus Radarr/Sonarr entfernt", unmatched: "Nicht zugeordnet — Seerr konnte es nicht übergeben", "import": "Heruntergeladen, aber nicht importierbar", "wrong-film": "Falscher Film heruntergeladen", failed: "Download fehlgeschlagen", "no-release": "Seit Tagen nichts gefunden" },
      hints: {
        removed: "Die Anfrage ist in Seerr noch offen, der Titel fehlt aber in Radarr/Sonarr, darum sucht nichts danach.",
        unmatched: "Den richtigen Titel wählen; er kommt mit den üblichen Qualitätsregeln in Radarr/Sonarr.",
        "import": "Prüfen, welche Datei welche Folge oder welcher Film ist, dann importieren.",
        "wrong-film": "Der Download ist ein anderer Film (anderes Jahr). Sperren und neu suchen.",
        failed: "Dieses Release sperren und ein anderes suchen.",
        "no-release": "Von Hand ein Release wählen, oder bei alten oder seltenen Titeln ältere Versionen (DVD, SD) erlauben."
      },
      reasons: {
        "id-match": "Der Release-Name passt nicht zum Titel (z. B. ein Name in einer anderen Sprache), darum wird er nicht automatisch importiert. Folge bzw. Film prüfen, dann importieren.",
        "no-eligible": "Der Download enthält keine importierbare Datei (keine Videodatei oder nur Extras).",
        "not-upgrade": "Die Bibliothek hat das schon in gleicher oder besserer Qualität, darum wird es nicht importiert.",
        sample: "Die Datei ist nur ein Sample (kurzer Ausschnitt), nicht der ganze Film oder die ganze Folge.",
        unparsable: "Sonarr/Radarr erkennt am Dateinamen nicht, welche Folge oder welcher Film es ist.",
        "not-in-release": "Der Download enthält eine andere Folge bzw. einen anderen Film als erwartet.",
        "wrong-film": "Der Download ist ein anderer Film: das Jahr passt nicht.",
        "dub-only": "Der Download hat keine Tonspur in der Originalsprache (nur synchronisiert).",
        failed: "Der Download ist fehlgeschlagen (defekt oder unvollständig).",
        "no-release": "Seit Tagen wurde kein Release gefunden.",
        removed: "Die Anfrage ist in Seerr noch offen, der Titel fehlt aber in Radarr/Sonarr, darum sucht nichts danach.",
        unmatched: "Seerr konnte die Anfrage nicht an Radarr/Sonarr übergeben (der Titel wurde dort nicht gefunden).",
        unknown: "Sonarr/Radarr hat diesen Download aus einem Grund angehalten, den diese Seite noch nicht kennt:"
      },
      marks: { ok: "✅", warn: "⚠️", bad: "❌", none: "–" },
      checks: {
        original: function (x) { return ["Originalsprache" + (x.language ? " (" + x.language + ")" : ""), { ok: "vorhanden", bad: "fehlt, nur synchronisiert", none: "unbekannt" }[x.mark]]; },
        english: function (x) { return ["Englischer Ton", { ok: "vorhanden", warn: "fehlt", none: "unbekannt" }[x.mark]]; },
        german: function (x) { return ["Deutscher Ton oder Untertitel", { ok: "vorhanden", none: "keine" }[x.mark]]; },
        quality: function (x) { return ["Qualität" + (x.quality ? " " + x.quality : ""), { ok: "vom Profil erlaubt", bad: "vom Profil nicht erlaubt", none: "unbekannt" }[x.mark], x.upgrade ? "wird evtl. später auf " + x.upgrade + " verbessert" : ""]; },
        hdr: function (x) { return [String(x.hdr || "SDR"), { ok: "vom Profil erlaubt", bad: "Dolby Vision ohne HDR-Rückfall: das Profil lehnt das ab", none: "" }[x.mark], x.upgrade ? "wird evtl. später auf 4K HDR verbessert" : ""]; },
        runtime: function (x) { return ["Laufzeit " + x.minutes + " Min. (erwartet " + x.expected + ")", { ok: "passt", warn: "10–25 % daneben", bad: "mehr als 25 % daneben: falsche Datei, Sample oder Extras?" }[x.mark]]; },
        size: function (x) { return [gb(x.size) + (x.gbh ? " · " + x.gbh + " GB/h" : ""), x.mark === "warn" ? "sehr gross (über " + x.limit + " GB/h)" : ""]; }
      },
      checksOk: "Prüfungen", problemsShort: function (n) { return n + (n === 1 ? " Problem" : " Probleme"); }, notesShort: function (n) { return n + (n === 1 ? " Hinweis" : " Hinweise"); },
      virusShort: { clean: "Virenscan sauber", pending: "Virenscan läuft", infected: "Schadsoftware gefunden", unknown: "noch nicht auf Viren geprüft", "parts-clean": "Teile sauber", "parts-error": "nur teilweise prüfbar" },
      tooBig: "Zu gross für den vollen Virenscan",
      bigHead: "Grosse Datei",
      bigWhy: "ClamAV prüft höchstens 2 GiB, darum wird der Videostrom selbst nicht gescannt.", bigParts: "Anhänge und Untertitel:", bigClean: "sauber", bigVideo: "Video:", bigGenuine: "echter Videostrom",
      partsError: function (why) { return "Teil-Scan nicht möglich" + (why ? " (" + why + ")" : "") + ": nur Admins"; },
      verdict: { clean: "✅ Kein Virus gefunden", malware: "❌ Schadsoftware — Import gesperrt", flagged: "⚠️ Gemeldet — nur Admins", partial: "⚠️ Nicht vollständig prüfbar — nur Admins", runtime: "⚠️ Laufzeit passt nicht — nur Admins", checking: "⏳ Wird noch geprüft" },
      clamRun: function (m, e) { return "ClamAV: grosse Datei: Anhänge und Untertitel sauber, echtes Video, aber Laufzeit " + (m ? m + " Min." : "unbekannt") + ", erwartet " + (e ? e + " Min." : "unbekannt"); },
      bigRun: "Laufzeit:", runWhy: function (m, e) { return (m ? m + " Min." : "unbekannt") + ", erwartet " + (e ? e + " Min." : "unbekannt") + (m && e ? " — mehr als 25 % daneben" : "") + ", darum kann nur ein Admin importieren"; },
      clam: { clean: "ClamAV: sauber", "parts-clean": "ClamAV: sauber (grosse Datei: Anhänge und Untertitel geprüft, echtes Video)", pending: "ClamAV: Scan in der Warteschlange, meist ein paar Minuten",
        infected: "ClamAV: Schadsoftware gefunden", unknown: "ClamAV: noch nicht geprüft", "parts-error": "ClamAV: zu gross für den vollen Scan, Teil-Scan nicht möglich" },
      vt: {
        queued: function () { return "VirusTotal: Abfrage in der Warteschlange, ein paar Minuten"; }, unchecked: function () { return "VirusTotal: noch nicht geprüft (wird wiederholt), kein Einfluss auf den Import"; },
        unknown: function () { return "VirusTotal: keine Meldungen — diese genaue Datei ist dort noch nicht bekannt, normal bei neuen Downloads, keine Warnung"; }, clean: function () { return "VirusTotal: bekannte Datei, kein Scanner meldet sie"; },
        flagged: function (v) { return "VirusTotal: von " + v.malicious + (v.malicious === 1 ? " Scanner" : " Scannern") + " gemeldet, oft ein Fehlalarm"; },
        malware: function (v) { return "VirusTotal: bekannte Schadsoftware (" + v.malicious + " von " + v.engines + " Scannern)"; }
      },
      vtShort: { flagged: "von VirusTotal gemeldet", malware: "bekannte Schadsoftware" },
      vtHead: "VirusTotal-Fingerabdruck", vtChecked: "Geprüft:", vtHow: "Nur der SHA-256-Fingerabdruck der Datei wird abgefragt; die Datei wird nie hochgeladen.",
      vtWhy: { queued: "Wird in ein paar Minuten abgefragt (höchstens 4 pro Minute, 500 pro Tag).", unchecked: "Die Abfrage ist fehlgeschlagen oder das Tageslimit ist aufgebraucht; sie wird später wiederholt. Kein Einfluss auf den Import.",
        unknown: "Diese genaue Datei hat noch niemand eingereicht: normal bei einem frischen Video-Download. Kein Einfluss auf den Import.", clean: "VirusTotal bekannt; kein Scanner meldet sie.",
        flagged: "1–2 Scanner melden sie, oft ein Fehlalarm: nur ein Admin kann sie importieren.", malware: "3 oder mehr Scanner melden sie als Schadsoftware: wird nie importiert, auch nicht von Admins." },
      detailsLink: "Details ↗", account: "🔒 Konto nötig", scoutNfo: "NFO", scoutDesc: "Beschreibung", scoutNone: "keine Sprache genannt",
      scoutReading: "Liest die Beschreibung… (ein, zwei Minuten)", scoutRead: "Beschreibung lesen",
      noMatch: "Kein Release für diesen Titel gefunden — die Indexer kennen noch keine Version.", olderHint: "Bei einem alten oder seltenen Titel oben «Ältere Versionen erlauben» versuchen.",
      noneAllowed: "Es gibt Releases, aber keines, das das Profil erlaubt.", notAllowed: function (n) { return n + " weitere, nicht erlaubt — Gründe zeigen"; },
      releaseReasons: { "cf-score": "Nach unseren Regeln zu tiefe Punktzahl (z. B. falsche Sprache, schlechte Quelle).", "required-terms": "Dem Namen fehlt ein Begriff, den das Profil verlangt (z. B. die Sprache).",
        quality: "Das Profil erlaubt diese Qualität nicht.", existing: "Die Bibliothek hat schon eine gleich gute oder bessere Version.", seeders: "Zu wenige Seeder.",
        blocklisted: "Früher gesperrt (fehlgeschlagener oder falscher Download).", size: "Zu gross oder zu klein für das Profil.", "wrong-title": "Ein anderer Titel." },
      errorCodes: { "search queue full": "Gerade warten zu viele Suchen: in ein paar Minuten nochmals versuchen.", "too many searches": "Du hast schon eine Suche in der Warteschlange: diese kann starten, wenn sie fertig ist.",
        malware: "Schadsoftware gefunden: dieser Download wird nie importiert.", "virus scan pending": "Auf den Virenscan warten (meist ein paar Minuten); ein Admin kann vorher importieren.",
        flagged: "VirusTotal: einige Scanner melden diese Datei — nur ein Admin kann sie importieren.",
        "rejected file needs an admin": "Sonarr/Radarr hat diese Datei abgelehnt: nur ein Admin kann sie importieren.", "another title needs an admin": "Ein anderer Titel als der angefragte: nur ein Admin kann ihn zuordnen." },
      whatsWrong: "Was nicht stimmt", whatToDo: "Was tun", download: "Download", fileLabel: "Dateiname des Downloads:", raw: "Details von Sonarr/Radarr",
      audio: "Ton", subtitles: "Untertitel", min: "Min.",
      langs: { por: "Portugiesisch", eng: "Englisch", ger: "Deutsch", deu: "Deutsch", gsw: "Schweizerdeutsch", fre: "Französisch", fra: "Französisch", ita: "Italienisch", spa: "Spanisch", jpn: "Japanisch", kor: "Koreanisch", chi: "Chinesisch", zho: "Chinesisch", rus: "Russisch", dut: "Niederländisch", nld: "Niederländisch", pol: "Polnisch", tur: "Türkisch", swe: "Schwedisch", nor: "Norwegisch", dan: "Dänisch", fin: "Finnisch", hin: "Hindi", ara: "Arabisch" },
      requestedBy: "Angefragt von", nobody: "keine Seerr-Anfrage", since: "Seit", lastSearch: "Letzte Suche", never: "nie", profile: "Profil", release: "Release",
      years: function (a, b) { return "Release-Jahr " + a + ", Film " + b; }, episodes: function (n) { return n + (n === 1 ? " Folge" : " Folgen"); },
      readd: "Wieder hinzufügen", del: "Anfrage löschen", delAgain: "Nochmals tippen zum Löschen",
      delWarn: "Löscht nur die Seerr-Anfrage(n) dieses Titels, danach wird nichts mehr dafür heruntergeladen; die Titeldaten in Seerr und allfällige Dateien bleiben.",
      block: "Release sperren und neu suchen", blockAgain: "Nochmals tippen zum Sperren", profile2: function (p) { return "Ältere Versionen erlauben (" + p + ")"; },
      suggest: "Empfohlen: ein alter Titel, oft nur als DVD oder SD erhältlich.", releases: "Releases zeigen", hideReleases: "Releases ausblenden", loading: "Lädt…", searching: "Sucht in den Indexern… (bis 2 Min.)",
      queued: function (title) { return title ? "Wartet, bis die Suche für «" + title + "» fertig ist, dann startet diese von selbst." : "Wartet, bis andere Suchen fertig sind, dann startet diese von selbst."; }, season: "Staffel",
      override: "Trotzdem herunterladen", overrideHint: "Seit einer Woche nichts gefunden: alle dürfen ein nicht erlaubtes Release trotzdem herunterladen. Eine vom Profil erlaubte Version ersetzt es, sobald es eine gibt (jede Nacht gesucht).",
      overriddenBy: function (u) { return "Trotzdem heruntergeladen von " + (u || "jemandem"); },
      overrideState: { waiting: "⬇️ Wird trotzdem heruntergeladen (0 Seeder — wartet auf einen Seeder, jede Nacht wird nach einer besseren Version gesucht)", seeded: function (n) { return "⬇️ Wird trotzdem heruntergeladen (" + n + " Seeder — jede Nacht wird nach einer besseren Version gesucht)"; },
        downloading: "⬇️ Wird trotzdem heruntergeladen (jede Nacht wird nach einer besseren Version gesucht)", finished: "✅ Trotzdem heruntergeladen — jede Nacht wird nach einer besseren Version gesucht",
        replacing: "🔄 Eine vom Profil erlaubte Version wurde gefunden — sie wird stattdessen heruntergeladen", replaced: "✅ Ersetzt durch eine vom Profil erlaubte Version" },
      grab: "Herunterladen", noReleases: "Keine Releases gefunden.", pickOne: "Zuerst eines wählen.", rejected: "nicht erlaubt: ", seeders: "Seeder", score: "Punkte",
      lookup: "Suchen", link: "Diesen verwenden", probable: "Am wahrscheinlichsten", inLibrary: "schon in der Bibliothek", noResults: "Nichts gefunden.",
      files: "Dateien prüfen und Import abschliessen", hideFiles: "Dateien ausblenden", "import": "Importieren", detected: "wie erkannt", noEpisode: "—", needEpisode: "Für jede gewählte Datei eine Folge wählen.",
      history: "Verlauf", gone: "Hat sich von selbst erledigt", imported: "Importiert", found: "Gefunden und heruntergeladen",
      removedBy: function (u) { return "Von Admin entfernt" + (u ? " (" + u + ")" : ""); }, fixedBy: function (u, a) { return "Behoben von " + (u || "jemandem") + " (" + a + ")"; },
      actions: { readd: "wieder hinzugefügt", "delete": "gelöscht", block: "gesperrt, neu gesucht", profile: "ältere Versionen erlaubt", grab: "Release gewählt", link: "zugeordnet", "import": "importiert", reopened: "wieder da",
        override: "trotzdem heruntergeladen", "override-downloading": "lädt herunter", "override-finished": "Download fertig", "override-replacing": "bessere Version gefunden", "override-replaced": "ersetzt durch eine vom Profil erlaubte Version" },
      errors: { 429: "Zu viele Versuche — kurz warten und nochmals versuchen.", 403: "Nicht erlaubt — das kann ein Admin.", 400: "Das passt nicht mehr — Seite neu laden.", 404: "Schon gelöst — Seite neu laden.", other: "Radarr/Sonarr nicht erreichbar — später nochmals versuchen." },
      ok: "Erledigt: ", working: "Läuft…", problems: function (n) { return n + " Probleme"; }
    }
  };

  function el(doc, tag, attrs) {
    var e = doc.createElement(tag);
    for (var k in attrs || {}) if (Object.prototype.hasOwnProperty.call(attrs, k) && attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) e.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    for (var i = 3; i < arguments.length; i++) {
      var c = arguments[i];
      if (c === null || c === undefined) continue;
      e.appendChild(typeof c === "string" || typeof c === "number" ? doc.createTextNode(String(c)) : c);
    }
    return e;
  }

  function when(t, iso) {
    if (!iso) return t.never;
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    try { return d.toLocaleString(t.locale, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); } catch (e) { return String(iso); }
  }

  function overrideState(t, o) {
    var s = t.overrideState;
    if (o.state === "finished" || o.state === "replacing" || o.state === "replaced") return s[o.state];
    var n = o.seeds != null ? o.seeds : o.seeders;
    if (o.protocol !== "torrent" || n == null) return s.downloading;
    return Number(n) > 0 ? s.seeded(Number(n)) : s.waiting;
  }

  function gb(n) { return (Number(n || 0) / 1e9).toFixed(1) + " GB"; }

  function langOf(win, me) {
    var q = /[?&]lang=([a-z]+)/.exec(win.location.search || "");
    if (q) return q[1] === "de" ? "de" : "en";
    var tv = win.document.querySelector('a[href="/discover/tv"]:not([data-hg-link])');
    if (tv && /\bSerien\b/.test(tv.textContent)) return "de";
    var locale = me && me.settings && me.settings.locale;
    return typeof locale === "string" && /^de/.test(locale) ? "de" : "en";
  }

  function call(win, path, body) {
    var opts = { credentials: "same-origin", headers: { Accept: "application/json" } };
    if (body) { opts.method = "POST"; opts.headers = { "Content-Type": "application/json", Accept: "application/json" }; opts.body = JSON.stringify(body); }
    return win.fetch(path, opts).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, body: j }; }, function () { return { status: r.status, body: {} }; });
    }, function () { return { status: 0, body: {} }; });
  }

  function errorText(t, status, body) {
    var code = body && body.error;
    return (code && t.errorCodes[code]) || t.errors[status] || t.errors.other;
  }

  // ✅ ⚠️ ❌ – per property, the reason spelled out for warnings and violations
  function checkList(doc, t, checks) {
    if (!Array.isArray(checks) || !checks.length) return null;
    var ul = el(doc, "ul", { "class": "hg-m-checks" });
    checks.forEach(function (x) {
      var f = x && t.checks[x.rule], mark = x && t.marks[x.mark] ? x.mark : "none";
      if (!f) return;
      var parts = f(x), why = parts[1] || "", info = parts[2] ? el(doc, "span", { "class": "hg-m-info", "data-info": true }, " · " + parts[2]) : null;
      var li = el(doc, "li", { "data-check": x.rule, "data-mark": mark, "class": "hg-m-check-" + mark }, t.marks[mark] + " " + parts[0] + (why ? ": " + why : ""), info);
      if (why && (mark === "warn" || mark === "bad")) tip(li, { title: t.marks[mark] + " " + parts[0], rows: [why.charAt(0).toUpperCase() + why.slice(1)].concat(parts[2] ? [parts[2]] : []) });
      ul.appendChild(li);
    });
    return ul;
  }

  // the card's one line: resolution · audio languages · checks · virus scan; everything else is in the file list (owner 2026-11-15)
  function summaryLine(t, x) {
    var m = x.media && typeof x.media === "object" ? x.media : null, parts = [];
    if (m && m.resolution) parts.push(String(m.resolution));
    if (m) {
      var langs = [];
      (m.audio || []).forEach(function (a) { var l = a && a.language ? lang(t, a.language) : null; if (l && langs.indexOf(l) < 0) langs.push(l); });
      if (langs.length) parts.push(langs.join(", "));
    }
    if (Array.isArray(x.checks) && x.checks.length) {
      var bad = x.checks.filter(function (c) { return c && c.mark === "bad"; }).length, warn = x.checks.filter(function (c) { return c && c.mark === "warn"; }).length;
      parts.push(bad ? "❌ " + t.problemsShort(bad) : warn ? "⚠️ " + t.notesShort(warn) : "✅ " + t.checksOk);
    }
    if (x.virus && typeof x.virus === "object") {
      var v = x.virus;
      parts.push(v.state === "clean" || v.state === "parts-clean" ? "✅ " + t.virusShort[v.state] : v.state === "infected" ? "❌ " + t.virusShort.infected + (v.signature ? " (" + String(v.signature) + ")" : "")
        : v.state === "parts-error" ? "⚠️ " + t.virusShort["parts-error"] : "⏳ " + (t.virusShort[v.state] || t.virusShort.unknown));
      var vs = v.vt && typeof v.vt === "object" ? v.vt.state : null;
      if (vs === "malware") parts.push("❌ " + t.vtShort.malware);
      else if (vs === "flagged") parts.push("⚠️ " + t.vtShort.flagged);
    }
    return parts.join(" · ");
  }

  function tip(e, content) {
    var h = e.ownerDocument.defaultView && e.ownerDocument.defaultView.hgTip;
    if (h) h.set(e, content);
  }

  // overall verdict of one file, the same order the import gates use: malware blocks, flagged and a partial scan need an admin, a running scan waits
  function verdictOf(v) {
    var vs = v.vt && typeof v.vt === "object" ? v.vt.state : null;
    if (v.state === "infected" || vs === "malware") return "malware";
    if (vs === "flagged") return "flagged";
    if (v.state === "parts-error") return "partial";
    if (v.state === "parts-clean" && v.runtimeFits === false) return "runtime";
    if (v.state === "clean" || v.state === "parts-clean") return "clean";
    return "checking";
  }

  // ClamAV (../Antivirus) and the VirusTotal fingerprint (virustotal.py, hash lookups only) of one file: verdict first, then each source in plain words
  function virusBlock(doc, t, v) {
    if (!v || typeof v !== "object") return null;
    var verdict = verdictOf(v);
    var block = el(doc, "div", { "class": "hg-m-virus-block", "data-virus": true, "data-verdict": verdict },
      el(doc, "p", { "class": "hg-m-virus hg-m-virus-" + verdict, "data-virus-verdict": true }, t.verdict[verdict]));
    var state = t.clam[v.state] ? v.state : "unknown", clam = t.clam[state];
    if (state === "infected" && v.signature) clam += " (" + String(v.signature) + ")";
    if (state === "clean" && v.at) clam += " (" + when(t, v.at) + ")";
    if (state === "parts-error" && v.why) clam += " (" + String(v.why) + ")";
    var m = Number(v.minutes) || 0, e = Number(v.expected) || 0;
    if (verdict === "runtime") clam = t.clamRun(m, e);
    var c = el(doc, "p", { "class": "hg-m-virus-src", "data-virus-src": "clamav", "data-clam": state }, clam);
    if (state === "parts-clean" || state === "parts-error") {
      var ok = state === "parts-clean", off = verdict === "runtime";
      tip(c, { title: off ? "⚠️ " + t.bigHead : ok ? "✅ " + t.bigHead : "⚪ " + t.tooBig, rows: [t.bigWhy].concat(ok ? [[t.bigParts, t.bigClean], [t.bigVideo, t.bigGenuine + (v.minutes ? " (" + v.minutes + " " + t.min + ")" : "")]] : [t.partsError(v.why ? String(v.why) : null)])
        .concat(off ? [[t.bigRun, t.runWhy(m, e)]] : []) });
    }
    block.appendChild(c);
    var x = v.vt && typeof v.vt === "object" && t.vt[v.vt.state] ? v.vt : null;
    if (x) {
      var icon = { queued: "⏳", unchecked: "⚪", unknown: "⚪", clean: "✅", flagged: "⚠️", malware: "❌" }[x.state];
      var n = { malicious: Number(x.malicious) || 0, engines: Number(x.engines) || 0 };
      var line = el(doc, "p", { "class": "hg-m-virus-src", "data-virus-src": "vt", "data-vt": x.state }, t.vt[x.state](n));
      var rows = [t.vtWhy[x.state], t.vtHow];
      if (x.checked) rows.push([t.vtChecked, when(t, x.checked)]);
      tip(line, { title: icon + " " + t.vtHead, rows: rows });
      block.appendChild(line);
    }
    return block;
  }

  function App(win, root) {
    this.win = win; this.doc = win.document; this.root = root; this.t = TEXT.en; this.tab = "open"; this.data = null;
  }

  App.prototype.start = function () {
    var self = this;
    return Promise.all([call(this.win, "/api/v1/auth/me"), call(this.win, API + "problems")]).then(function (r) {
      self.t = TEXT[langOf(self.win, r[0].status === 200 ? r[0].body : null)];
      self.doc.documentElement.setAttribute("lang", self.t === TEXT.de ? "de" : "en");
      self.doc.title = self.t.title + " - Seerr";
      self.show(r[1]);
    });
  };

  App.prototype.reload = function (flash) {
    var self = this;
    return call(this.win, API + "problems").then(function (r) { self.show(r, flash); });
  };

  App.prototype.show = function (r, flash) {
    var t = this.t, d = this.doc, self = this;
    while (this.root.firstChild) this.root.removeChild(this.root.firstChild);
    this.root.appendChild(el(d, "h1", null, t.title));
    this.root.appendChild(el(d, "p", { "class": "hg-m-intro" }, t.intro));
    if (r.status === 401 || r.status === 403) {
      this.root.appendChild(el(d, "p", null, el(d, "a", { href: "/login" }, t.signin)));
      return;
    }
    if (r.status !== 200 || !r.body || !Array.isArray(r.body.open)) {
      this.root.appendChild(el(d, "p", { "class": "hg-m-error" }, t.loadError));
      return;
    }
    this.data = r.body;
    if (flash) this.root.appendChild(el(d, "p", { "class": "hg-m-flash", "data-flash": true, role: "status" }, flash));
    var tabs = el(d, "div", { "class": "hg-m-tabs", role: "tablist" });
    ["open", "done"].forEach(function (name) {
      var b = el(d, "button", { type: "button", role: "tab", "data-tab": name, "aria-selected": String(self.tab === name), "class": self.tab === name ? "hg-m-tab hg-m-on" : "hg-m-tab" }, t[name] + " (" + (r.body[name] || []).length + ")");
      b.addEventListener("click", function () { self.tab = name; self.show(r); });
      tabs.appendChild(b);
    });
    this.root.appendChild(tabs);
    var list = r.body[this.tab] || [];
    if (!list.length) {
      this.root.appendChild(el(d, "p", { "class": "hg-m-empty", "data-empty": true }, this.tab === "open" ? t.empty : t.emptyDone));
      return;
    }
    groups(list).forEach(function (g) { self.root.appendChild(self.card(g, self.tab === "open")); });
  };

  // one card per title (movie|tv/<tmdb>): several downloads of a series are items inside it
  function groups(list) {
    var out = [], at = {};
    (list || []).forEach(function (p) {
      var k = String(p.key || p.id);
      if (!(k in at)) { at[k] = out.length; out.push({ key: k, items: [] }); }
      out[at[k]].items.push(p);
    });
    return out;
  }

  App.prototype.card = function (g, open) {
    var t = this.t, d = this.doc, self = this, p0 = g.items[0];
    var c = el(d, "section", { "class": "hg-m-card", "data-card": g.key });
    var title = String(p0.title || p0.key || "") + (p0.year ? " (" + p0.year + ")" : "");
    var link = /^(movie|tv)\/\d+$/.test(g.key) ? el(d, "a", { href: "/" + g.key }, title) : title;
    c.appendChild(el(d, "h2", { "data-title": true }, link));
    if (g.items.length > 1) c.appendChild(el(d, "p", { "class": "hg-m-count", "data-count": true }, t.problems(g.items.length)));
    var names = [], first = null;
    g.items.forEach(function (p) {
      (p.requesters || []).forEach(function (n) { if (names.indexOf(String(n)) < 0) names.push(String(n)); });
      if (p.firstSeen && (!first || p.firstSeen < first)) first = p.firstSeen;
    });
    c.appendChild(el(d, "p", { "class": "hg-m-meta", "data-requesters": true }, t.requestedBy + ": " + (names.length ? names.join(", ") : t.nobody) + " · " + t.since + " " + when(t, first)));
    g.items.forEach(function (p) { c.appendChild(self.item(p, open)); });
    return c;
  };

  // a long release name with break points after dots, dashes and underscores (no giant unbroken run on a phone)
  function breakable(doc, cls, name, attrs) {
    var s = el(doc, "span", Object.assign({ "class": cls }, attrs || {}));
    String(name || "").split(/(?<=[._-])/).forEach(function (part, i) {
      if (i) s.appendChild(doc.createElement("wbr"));
      s.appendChild(doc.createTextNode(part));
    });
    return s;
  }

  function lang(t, code) { return code ? t.langs[code] || String(code).toUpperCase() : "?"; }

  // "1080p · H.264 · HDR10 · Audio: Portuguese E-AC3 5.1, English AAC 2.0 · Subtitles: Portuguese · 1.6 GB · 45 min"
  function mediaLine(t, m) {
    if (!m || typeof m !== "object") return null;
    var parts = [m.resolution, m.video, m.hdr].filter(Boolean).map(String);
    if ((m.audio || []).length) parts.push(t.audio + ": " + m.audio.map(function (a) { return [lang(t, a.language), a.codec, a.channels].filter(Boolean).join(" "); }).join(", "));
    if ((m.subtitles || []).length) parts.push(t.subtitles + ": " + m.subtitles.map(function (x) { return lang(t, x); }).join(", "));
    if (m.size) parts.push(gb(m.size));
    if (m.minutes) parts.push(m.minutes + " " + t.min);
    return parts.join(" · ");
  }

  var TYPE_REASON = { removed: "removed", unmatched: "unmatched", "no-release": "no-release", "wrong-film": "wrong-film", failed: "failed" };

  App.prototype.section = function (head, attrs) {
    var d = this.doc, s = el(d, "div", Object.assign({ "class": "hg-m-section" }, attrs || {}));
    s.appendChild(el(d, "h3", { "class": "hg-m-head" }, head));
    return s;
  };

  App.prototype.item = function (p, open) {
    var t = this.t, d = this.doc, x = p.details || {};
    var c = el(d, "div", { "class": "hg-m-item", "data-problem": p.id });
    c.appendChild(el(d, "p", { "class": "hg-m-type", "data-type": true }, t.types[p.type] || String(p.type)));
    var code = x.reason && t.reasons[x.reason] ? x.reason : TYPE_REASON[p.type] || "unknown";
    var what = this.section(t.whatsWrong, { "data-what": true });
    what.appendChild(el(d, "p", null, t.reasons[code]));
    if (code === "unknown" && x.message) what.appendChild(el(d, "p", { "class": "hg-m-quote" }, String(x.message)));
    c.appendChild(what);
    c.appendChild(this.details(p));
    if (x.release) {
      var dl = this.section(t.download, { "data-download": true });
      dl.appendChild(el(d, "p", { "class": "hg-m-file" }, el(d, "span", { "class": "hg-m-label", "data-file-label": true }, t.fileLabel), breakable(d, "hg-m-filename", x.release, { "data-file-name": true })));
      var sl = summaryLine(t, x);
      if (sl) dl.appendChild(el(d, "p", { "class": "hg-m-media", "data-summary": true }, sl));
      c.appendChild(dl);
    }
    if (x.message && code !== "unknown") c.appendChild(el(d, "details", { "class": "hg-m-raw", "data-raw": true }, el(d, "summary", null, t.raw), el(d, "p", null, String(x.message))));
    if (open) {
      var todo = this.section(t.whatToDo, { "data-todo": true });
      todo.appendChild(el(d, "p", { "class": "hg-m-hint" }, t.hints[p.type] || ""));
      c.appendChild(todo);
      var acts = el(d, "div", { "class": "hg-m-actions" });
      this.actions(p, acts);
      c.appendChild(acts);
      c.appendChild(el(d, "p", { "class": "hg-m-msg", "data-msg": true, role: "status" }));
    } else {
      var res = p.resolved || {};
      var by = (p.actions || []).filter(function (a) { return a.action === res.how; }).pop() || {};
      var how = res.how === "gone" || res.how === "imported" || res.how === "found" ? t[res.how] : res.how === "delete" ? t.removedBy(by.user ? String(by.user) : null)
        : res.how === "override" ? t.overriddenBy(by.user ? String(by.user) : null) : t.fixedBy(by.user ? String(by.user) : null, t.actions[res.how] || String(res.how));
      c.appendChild(el(d, "p", { "class": "hg-m-resolved", "data-resolved": true }, how + " · " + when(t, res.at)));
    }
    if (p.override && typeof p.override === "object") c.appendChild(el(d, "p", { "class": "hg-m-media", "data-override": true }, overrideState(t, p.override)));
    if ((p.actions || []).length) {
      var h = el(d, "details", { "class": "hg-m-history", open: !open }, el(d, "summary", null, t.history));
      var ul = el(d, "ul");
      p.actions.forEach(function (a) {
        ul.appendChild(el(d, "li", null, when(t, a.at) + " · " + (a.user ? String(a.user) + ": " : "") + (t.actions[a.action] || String(a.action)) + (a.result ? " — " + String(a.result) : "")));
      });
      h.appendChild(ul);
      c.appendChild(h);
    }
    return c;
  };

  App.prototype.details = function (p) {
    var t = this.t, d = this.doc, x = p.details || {}, parts = [];
    if (x.releaseYear && x.year) parts.push(t.years(x.releaseYear, x.year));
    if (x.episodes) parts.push(t.episodes(x.episodes));
    if (p.type === "no-release") {
      parts.push(t.lastSearch + ": " + when(t, x.lastSearch));
      if (x.profile) parts.push(t.profile + ": " + String(x.profile));
    }
    return el(d, "p", { "class": "hg-m-details", "data-details": true }, parts.join(" · "));
  };

  App.prototype.button = function (name, label, cls) {
    return el(this.doc, "button", { type: "button", "data-act": name, "class": "hg-m-btn" + (cls ? " " + cls : "") }, label);
  };

  // Show ↔ Hide; disabled with "Loading…" while the list loads (no double calls); load() resolves true when a list is shown
  App.prototype.toggle = function (btn, show, hide, box, load) {
    var self = this, shown = false;
    btn.addEventListener("click", function () {
      if (btn.disabled) return;
      if (shown) {
        shown = false;
        self.list(box);
        btn.textContent = show;
        return;
      }
      btn.disabled = true;
      btn.textContent = self.t.loading;
      Promise.resolve(load()).then(function (ok) {
        btn.disabled = false;
        shown = !!ok;
        btn.textContent = shown ? hide : show;
      });
    });
  };

  App.prototype.say = function (card, text, bad) {
    var m = card.querySelector("[data-msg]");
    if (!m) return;
    m.textContent = text;
    m.className = "hg-m-msg" + (bad ? " hg-m-bad" : "");
  };

  App.prototype.act = function (card, p, body) {
    var self = this, t = this.t;
    body = Object.assign({ id: p.id }, body);
    this.say(card, t.working);
    card.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    return call(this.win, API + "action", body).then(function (r) {
      if (r.status === 200) return self.reload(t.ok + String((r.body && r.body.result) || ""));
      card.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
      self.say(card, errorText(t, r.status, r.body), true);
    });
  };

  // a button that needs a second tap: the first one arms it (and shows the warning)
  App.prototype.twice = function (btn, again, warning, box, go) {
    var d = this.doc, armed = false;
    btn.addEventListener("click", function () {
      if (armed) return go();
      armed = true;
      btn.textContent = again;
      btn.classList.add("hg-m-armed");
      if (warning) box.appendChild(el(d, "p", { "class": "hg-m-warning", "data-warning": true, role: "alert" }, warning));
    });
  };

  App.prototype.actions = function (p, box) {
    var self = this, t = this.t, d = this.doc, x = p.details || {};
    var card = function () { return box.parentNode; };
    if (p.type === "removed") {
      var re = this.button("readd", t.readd, "hg-m-primary");
      re.addEventListener("click", function () { self.act(card(), p, { action: "readd" }); });
      box.appendChild(re);
      if (this.data && this.data.admin) {
        var del = this.button("delete", t.del, "hg-m-danger");
        this.twice(del, t.delAgain, t.delWarn, box, function () { self.act(card(), p, { action: "delete", confirm: "delete" }); });
        box.appendChild(del);
      }
    } else if (p.type === "wrong-film" || p.type === "failed") {
      var bl = this.button("block", t.block, "hg-m-primary");
      this.twice(bl, t.blockAgain, null, box, function () { self.act(card(), p, { action: "block" }); });
      box.appendChild(bl);
    } else if (p.type === "no-release") {
      if (x.older) {
        if (x.old) box.appendChild(el(d, "p", { "class": "hg-m-suggest", "data-suggest": true }, t.suggest));
        var pr = this.button("profile", t.profile2(String(x.older)), x.old ? "hg-m-primary" : "");
        pr.addEventListener("click", function () { self.act(card(), p, { action: "profile" }); });
        box.appendChild(pr);
      }
      var seasons = Array.isArray(x.seasons) ? x.seasons : [];
      var pick = null;
      if (seasons.length > 1) {
        pick = el(d, "select", { "data-season": true, "aria-label": t.season });
        seasons.forEach(function (s) { pick.appendChild(el(d, "option", { value: String(s) }, t.season + " " + s)); });
        box.appendChild(pick);
      }
      var rl = this.button("releases", t.releases);
      this.toggle(rl, t.releases, t.hideReleases, box, function () { return self.releases(card(), p, pick ? pick.value : seasons.length ? String(seasons[0]) : null); });
      box.appendChild(rl);
      box.appendChild(el(d, "div", { "data-list": true }));
    } else if (p.type === "unmatched") {
      var term = el(d, "input", { type: "search", "data-term": true, "class": "hg-m-input", "aria-label": t.lookup, maxlength: "100" });
      term.value = String(p.title || "");
      var lk = this.button("lookup", t.lookup);
      lk.addEventListener("click", function () {
        if (lk.disabled) return;
        lk.disabled = true;
        lk.textContent = t.loading;
        self.lookup(card(), p, term.value).then(function () { lk.disabled = false; lk.textContent = t.lookup; });
      });
      box.appendChild(term);
      box.appendChild(lk);
      box.appendChild(el(d, "div", { "data-list": true }));
    } else if (p.type === "import") {
      var fl = this.button("files", t.files);
      this.toggle(fl, t.files, t.hideFiles, box, function () { return self.files(card(), p); });
      box.appendChild(fl);
      box.appendChild(el(d, "div", { "data-list": true }));
    }
  };

  App.prototype.list = function (card) {
    var l = card.matches && card.matches("[data-list]") ? card : card.querySelector("[data-list]");
    while (l.firstChild) l.removeChild(l.firstChild);
    return l;
  };

  // a search behind others is queued by the backend: ask again every POLL ms until the list is there (5 min at most)
  App.prototype.releases = function (card, p, season, tries, watch) {
    var self = this, t = this.t, d = this.doc;
    var l = watch ? card.querySelector("[data-list]") : this.list(card);
    if (!tries && !watch) this.say(card, t.searching);
    return call(this.win, API + "releases?id=" + encodeURIComponent(p.id) + (season ? "&season=" + encodeURIComponent(season) : "")).then(function (r) {
      if (r.status === 200 && r.body && (r.body.state === "queued" || r.body.state === "running")) {
        self.say(card, r.body.state === "queued" ? t.queued(r.body.waitingFor ? String(r.body.waitingFor) : null) : t.searching);
        if ((tries || 0) > 300000 / POLL) { self.say(card, t.errors.other, true); return false; }
        return new Promise(function (ok) { self.win.setTimeout(ok, POLL); }).then(function () { return self.releases(card, p, season, (tries || 0) + 1); });
      }
      if (r.status !== 200) return self.say(card, errorText(t, r.status, r.body), true);
      if (watch) { if (!l.isConnected || !l.firstChild) return false; self.list(card); }
      self.say(card, "");
      var rel = (r.body && r.body.releases) || [];
      if (!rel.length) {
        var none = el(d, "p", { "data-no-releases": true }, t.noMatch);
        if (p.details && p.details.older) none.appendChild(d.createTextNode(" " + t.olderHint));
        l.appendChild(none);
        return true;
      }
      var row = function (x) {
        var radio = el(d, "input", { type: "radio", name: "release-" + p.id, value: String(x.n), disabled: !!x.rejected });
        var facts = [x.quality, gb(x.size), x.gbh ? x.gbh + " GB/h" : null, (x.languages || []).join(", "), x.protocol === "torrent" && x.seeders != null ? x.seeders + " " + t.seeders : null, x.score != null ? t.score + " " + x.score : null, x.indexer].filter(Boolean).map(String).join(" · ");
        var reasons = (x.rejections || []).map(function (raw, k) { var c = (x.reasons || [])[k]; return c && t.releaseReasons[c] ? t.releaseReasons[c] : String(raw); });
        var why = x.rejected && reasons.length ? el(d, "span", { "class": "hg-m-why" }, reasons.filter(function (v, k) { return reasons.indexOf(v) === k; }).join(" ")) : null;
        var extra = null;
        if (typeof x.link === "string" && /^https?:\/\//.test(x.link)) {
          extra = el(d, "span", { "class": "hg-m-facts hg-m-wide" });
          extra.appendChild(el(d, "a", { href: x.link, target: "_blank", rel: "noopener noreferrer", "data-details-link": true }, t.detailsLink));
          if (x.private) extra.appendChild(d.createTextNode(" · " + t.account));
        }
        var anyway = null;
        if (x.rejected && r.body.override) {
          anyway = self.button("override", t.override);
          anyway.addEventListener("click", function () { self.act(card, p, { action: "override", release: x.n, token: r.body.token }); });
        }
        return el(d, "li", { "data-release": true, "class": x.rejected && !anyway ? "hg-m-rejected" : null }, el(d, "label", null, radio, breakable(d, "hg-m-name", x.title), el(d, "span", { "class": "hg-m-facts" }, facts), why), extra, scoutBlock(x), anyway);
      };
      // what the NFO / description scout read, or the button to have it read now (beefy-side, within the scouts' limits)
      var scoutBlock = function (x) {
        var sc = x.scout && typeof x.scout === "object" ? x.scout : null;
        if (sc && sc.state === "read") {
          var langs = (sc.langs || []).map(String);
          var box = el(d, "div", { "class": "hg-m-scout hg-m-wide", "data-scout": true }, "📄 " + (sc.source === "nfo" ? t.scoutNfo : t.scoutDesc) + ": " + (langs.length ? langs.join(", ") : t.scoutNone));
          if ((sc.lines || []).length) {
            var ul = el(d, "ul", { "class": "hg-m-lines" });
            sc.lines.slice(0, 5).forEach(function (line) { ul.appendChild(el(d, "li", null, String(line))); });
            box.appendChild(ul);
          }
          return box;
        }
        if (sc && sc.state === "pending") return el(d, "div", { "class": "hg-m-scout hg-m-wide", "data-scout": true }, "📄 " + t.scoutReading);
        var out = el(d, "div", { "class": "hg-m-scout hg-m-wide", "data-scout": true });
        if (sc && (sc.state === "refused" || sc.state === "failed")) out.appendChild(el(d, "span", { "class": "hg-m-why" }, "📄 " + String(sc.why || t.errors.other) + " "));
        if (x.readable) {
          var b = self.button("describe", t.scoutRead);
          b.addEventListener("click", function () {
            if (b.disabled) return;
            b.disabled = true;
            b.textContent = t.loading;
            call(self.win, API + "action", { id: p.id, action: "describe", release: x.n, token: r.body.token }).then(function (res) {
              if (res.status !== 200) { b.disabled = false; b.textContent = t.scoutRead; return self.say(card, errorText(t, res.status, res.body), true); }
              out.textContent = "📄 " + t.scoutReading;
              self.watchScouts(card, p, season, 0);
            });
          });
          out.appendChild(b);
        }
        return out.firstChild ? out : null;
      };
      var ok = rel.filter(function (x) { return !x.rejected; }), no = rel.filter(function (x) { return x.rejected; });
      if (ok.length) {
        var ul = el(d, "ul", { "class": "hg-m-rows" });
        ok.forEach(function (x) { ul.appendChild(row(x)); });
        l.appendChild(ul);
      } else {
        l.appendChild(el(d, "p", { "data-no-releases": true }, t.noneAllowed));
      }
      if (no.length) {
        var fold = el(d, "details", { "class": "hg-m-raw", "data-not-allowed": true, open: !!r.body.override }, el(d, "summary", null, t.notAllowed(no.length)));
        if (r.body.override) fold.appendChild(el(d, "p", { "class": "hg-m-hint", "data-override-hint": true }, t.overrideHint));
        var ul2 = el(d, "ul", { "class": "hg-m-rows" });
        no.forEach(function (x) { ul2.appendChild(row(x)); });
        fold.appendChild(ul2);
        l.appendChild(fold);
      }
      if (rel.some(function (x) { return x.scout && x.scout.state === "pending"; })) self.watchScouts(card, p, season, (watch || 0));
      if (!ok.length) return true;
      var go = self.button("grab", t.grab, "hg-m-primary");
      go.addEventListener("click", function () {
        var c = l.querySelector('input[name="release-' + p.id + '"]:checked');
        if (!c) return self.say(card, t.pickOne, true);
        self.act(card, p, { action: "grab", release: Number(c.value), token: r.body.token });
      });
      l.appendChild(go);
      return true;
    });
  };

  // while a read is pending, the list is asked again every POLL ms (3 min at most) and redrawn with what arrived
  App.prototype.watchScouts = function (card, p, season, n) {
    var self = this;
    if (n > 180000 / POLL || card.__hgWatch) return;
    card.__hgWatch = true;
    self.win.setTimeout(function () {
      card.__hgWatch = false;
      self.releases(card, p, season, 0, n + 1);
    }, POLL);
  };

  App.prototype.lookup = function (card, p, term) {
    var self = this, t = this.t, d = this.doc;
    var l = this.list(card);
    this.say(card, t.working);
    return call(this.win, API + "lookup?id=" + encodeURIComponent(p.id) + "&term=" + encodeURIComponent(String(term || "").trim())).then(function (r) {
      if (r.status !== 200) return self.say(card, errorText(t, r.status, r.body), true);
      self.say(card, "");
      var res = (r.body && r.body.results) || [];
      if (!res.length) return l.appendChild(el(d, "p", null, t.noResults));
      var ul = el(d, "ul", { "class": "hg-m-rows" });
      res.forEach(function (x) {
        var radio = el(d, "input", { type: "radio", name: "pick-" + p.id, value: String(x.n) });
        radio.checked = !!x.probable;
        var id = x.tvdbId ? "TVDB " + x.tvdbId : x.tmdbId ? "TMDB " + x.tmdbId : null;
        var facts = [x.year, id, x.inLibrary ? t.inLibrary : null].filter(Boolean).map(String).join(" · ");
        ul.appendChild(el(d, "li", { "data-result": true }, el(d, "label", null, radio, el(d, "span", { "class": "hg-m-name" }, String(x.title || "")),
          x.probable ? el(d, "span", { "class": "hg-m-probable" }, t.probable) : null, el(d, "span", { "class": "hg-m-facts" }, facts),
          x.overview ? el(d, "span", { "class": "hg-m-why" }, String(x.overview)) : null)));
      });
      l.appendChild(ul);
      var go = self.button("link", t.link, "hg-m-primary");
      go.addEventListener("click", function () {
        var c = l.querySelector('input[name="pick-' + p.id + '"]:checked');
        if (!c) return self.say(card, t.pickOne, true);
        self.act(card, p, { action: "link", pick: Number(c.value), token: r.body.token });
      });
      l.appendChild(go);
    });
  };

  App.prototype.files = function (card, p) {
    var self = this, t = this.t, d = this.doc;
    var l = this.list(card);
    this.say(card, t.working);
    return call(this.win, API + "files?id=" + encodeURIComponent(p.id)).then(function (r) {
      if (r.status !== 200) return self.say(card, errorText(t, r.status, r.body), true);
      self.say(card, "");
      var tv = r.body.kind === "tv", eps = r.body.episodes || [], label = {};
      eps.forEach(function (e) { label[e.id] = e.label; });
      var ul = el(d, "ul", { "class": "hg-m-rows" });
      (r.body.files || []).forEach(function (f) {
        var box = el(d, "input", { type: "checkbox", "data-file": String(f.n) });
        box.checked = !!f.probable;
        var row = el(d, "li", null, el(d, "label", null, box, breakable(d, "hg-m-name", f.name), el(d, "span", { "class": "hg-m-facts" }, [gb(f.size), f.quality].filter(Boolean).map(String).join(" · "))));
        var fm = mediaLine(t, f.media);
        if (fm) row.appendChild(el(d, "span", { "class": "hg-m-facts hg-m-wide", "data-file-media": true }, fm));
        var fv = virusBlock(d, t, f.virus);
        if (fv) row.appendChild(fv);
        var fc = checkList(d, t, f.checks);
        if (fc) row.appendChild(fc);
        if (tv) {
          var sel = el(d, "select", { "data-episode": String(f.n), "aria-label": String(f.name || "") });
          sel.appendChild(el(d, "option", { value: "" }, t.noEpisode));
          var guess = (f.episodeIds || []).join(",");
          if ((f.episodeIds || []).length > 1) sel.appendChild(el(d, "option", { value: guess }, f.episodeIds.map(function (i) { return label[i] || i; }).join("+") + " (" + t.detected + ")"));
          eps.forEach(function (e) { sel.appendChild(el(d, "option", { value: String(e.id) }, String(e.label) + (e.title ? " · " + String(e.title) : ""))); });
          sel.value = guess;
          row.appendChild(sel);
        }
        if ((f.rejections || []).length) row.appendChild(el(d, "span", { "class": "hg-m-why" }, f.rejections.map(function (raw, k) { var c = (f.reasons || [])[k]; return c && c !== "unknown" && t.reasons[c] ? t.reasons[c] : String(raw); }).join(" ")));
        ul.appendChild(row);
      });
      l.appendChild(ul);
      var go = self.button("import", t["import"], "hg-m-primary");
      go.addEventListener("click", function () {
        var out = [], missing = false;
        l.querySelectorAll("input[data-file]").forEach(function (b) {
          if (!b.checked) return;
          var n = Number(b.getAttribute("data-file"));
          if (!tv) return out.push({ n: n });
          var s = l.querySelector('select[data-episode="' + n + '"]');
          if (!s || !s.value) { missing = true; return; }
          out.push({ n: n, episodeIds: s.value.split(",").map(Number) });
        });
        if (missing) return self.say(card, t.needEpisode, true);
        if (!out.length) return self.say(card, t.pickOne, true);
        self.act(card, p, { action: "import", files: out, token: r.body.token });
      });
      l.appendChild(go);
      return true;
    });
  };

  // On /manual: our page as the first child of Seerr's <main> (its 404 hidden by CSS), the same node and state put back when React re-renders; gone again on any other route.
  var app = null;
  function sync(win) {
    var doc = win.document, main = doc.querySelector("main");
    var here = win.location.pathname.replace(/\/+$/, "") === ROUTE;
    if (!here) {
      doc.documentElement.removeAttribute("data-hg-early");
      if (app) { if (app.root.parentNode) app.root.parentNode.removeChild(app.root); app = null; }
      var m = doc.querySelector("main[data-hg-manual]");
      if (m) m.removeAttribute("data-hg-manual");
      return;
    }
    if (!main || !win.fetch) return;
    if (!doc.getElementById("hg-manual-style")) {
      var st = doc.createElement("style");
      st.id = "hg-manual-style";
      st.textContent = CSS;
      (doc.head || doc.documentElement).appendChild(st);
    }
    if (!main.hasAttribute("data-hg-manual")) main.setAttribute("data-hg-manual", "");
    if (!app) {
      var root = doc.createElement("div");
      root.id = "hg-manual";
      app = new App(win, root);
      app.start();
    }
    if (app.root.parentNode !== main || main.firstChild !== app.root) main.insertBefore(app.root, main.firstChild);
    doc.documentElement.removeAttribute("data-hg-early");
    var title = app.t.title + " - Seerr";
    if (app.data && doc.title !== title) doc.title = title;
  }

  if (typeof window === "undefined") return;
  window.hgManual = { TEXT: TEXT, langOf: langOf, overrideState: overrideState, setPoll: function (ms) { POLL = ms; } };
  // React must hydrate Seerr's server-rendered 404 first: our page put in before makes it throw error #418 and redraw everything (as in menu-links.js)
  function hydrated(doc) {
    var root = doc.getElementById("__next");
    if (!root) return true;
    var first = root.firstElementChild;
    if (!first) return false;
    for (var k in first) if (k.indexOf("__reactFiber$") === 0) return true;
    return false;
  }

  try {
    if (!window.MutationObserver) return;
    var go = function () {
      document.documentElement.removeAttribute("data-hg-manual-wait");
      sync(window);
      new MutationObserver(function () {
        try { sync(window); } catch (e) { /* never break Seerr */ }
      }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    };
    if (hydrated(document)) go();
    else {
      if (window.location.pathname.replace(/\/+$/, "") === ROUTE) {
        var st = document.createElement("style");
        st.id = "hg-manual-wait";
        st.textContent = "html[data-hg-manual-wait] main { visibility: hidden; }";
        (document.head || document.documentElement).appendChild(st);
        document.documentElement.setAttribute("data-hg-manual-wait", "");
      }
      var frame = window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function (f) { setTimeout(f, 16); };
      var until = Date.now() + 10000;
      var wait = function () {
        try { if (hydrated(document) || Date.now() > until) go(); else frame(wait); } catch (e) { go(); }
      };
      frame(wait);
    }
  } catch (e) { /* never break Seerr */ }
})();
