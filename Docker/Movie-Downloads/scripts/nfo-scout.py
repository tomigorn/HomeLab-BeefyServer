#!/usr/bin/env python3
"""NFO scout (README "NFO scout"): Radarr and Sonarr read languages only from the release name, so a usenet release named just "MULTi" counts as the original language only. For every monitored Radarr movie and Sonarr series below the cutoff or missing, this searches the usenet indexers through Prowlarr, reads the audio languages of MULTi-only releases from the indexer (NzbPlanet, Treasure Maps: the NFO via t=getnfo; NZBgeek: the language attributes of t=details) and hands such a release to Radarr/Sonarr (release push) with the languages written into its name plus `.NFO`. The app then decides as usual. Run by systemd/nfo-scout.timer.
Usage: scripts/nfo-scout.py [--dry-run] [--tmdb ID | --tvdb ID]   (--dry-run fetches within budget and caches what it read, never pushes)"""
import argparse
import calendar
import html
import importlib.util
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("description_scout", HERE / "description-scout.py")
ds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds)
ApiError = ds.ApiError

STATE_DIR = ds.STATE_DIR
MARKER = "NFO"
MAX_ERRORS = 3
TM = "Treasure Maps"
SOURCES = {"NZBgeek": "details", "NzbPlanet": "getnfo", TM: "getnfo"}
USER_AGENT = "nfo-scout/1.0 (Movie-Downloads)"
SWISS_TAG = "audio-swiss-german"
NOT_LANGUAGES = {"Unknown", "Original", "Any"}
DAY = 86400
DUB_DAYS = 30

LEXICON = {}
for _name, _words in {
    "English": "english englisch englische eng anglais anglaise inglés ingles inglese engelsk engels angielski",
    "German": "german deutsch deutsche ger deu allemand alemán aleman tedesco tysk duits niemiecki",
    "French": "french französisch franzoesisch franzosisch français francais fre fra vf vff vfq vfi vf2 truefrench francés frances francese fransk frans",
    "Spanish": "spanish spanisch spa esp español espanol castellano castilian espagnol spagnolo spansk spaans",
    "Spanish (Latino)": "latino",
    "Italian": "italian italienisch ita italiano italien italienne italiensk",
    "Portuguese": "portuguese portugiesisch português portugues portugais portoghese",
    "Portuguese (Brazil)": "brazilian brasileiro brasilianisch",
    "Japanese": "japanese japanisch jpn jap japonais japonés giapponese japansk",
    "Korean": "korean koreanisch kor coréen coreen coreano",
    "Chinese": "chinese chinesisch chi zho mandarin cantonese kantonesisch chinois chino",
    "Russian": "russian russisch rus russe ruso russo",
    "Polish": "polish polnisch pol polski polonais",
    "Dutch": "dutch niederländisch niederlaendisch holländisch nld dut nederlands néerlandais",
    "Flemish": "flemish flämisch vlaams",
    "Danish": "danish dänisch daenisch dansk",
    "Swedish": "swedish schwedisch swe svenska suédois",
    "Norwegian": "norwegian norwegisch norsk nob",
    "Finnish": "finnish finnisch suomi",
    "Turkish": "turkish türkisch tuerkisch tur türkçe",
    "Czech": "czech tschechisch cze ces čeština",
    "Hungarian": "hungarian ungarisch hun magyar",
    "Greek": "greek griechisch gre ell",
    "Hebrew": "hebrew hebräisch heb",
    "Arabic": "arabic arabisch ara",
    "Hindi": "hindi hin",
    "Persian": "persian persisch farsi fas",
    "Thai": "thai tha",
    "Ukrainian": "ukrainian ukrainisch ukr",
    "Romanian": "romanian rumänisch ron",
    "Bulgarian": "bulgarian bulgarisch bul",
    "Vietnamese": "vietnamese vietnamesisch vie",
    "Icelandic": "icelandic isländisch isl",
    "Catalan": "catalan català",
    "Croatian": "croatian kroatisch", "Serbian": "serbian serbisch", "Slovak": "slovak slowakisch", "Slovenian": "slovenian slowenisch",
    "Lithuanian": "lithuanian", "Latvian": "latvian", "Estonian": "estonian", "Indonesian": "indonesian",
    "Tamil": "tamil", "Telugu": "telugu", "Malayalam": "malayalam", "Kannada": "kannada", "Bengali": "bengali",
}.items():
    for _w in _words.split():
        LEXICON[_w.casefold()] = _name
CODES2 = {"DE": "German", "EN": "English", "FR": "French", "IT": "Italian"}
TOKENS = {"Portuguese (Brazil)": "Brazilian", "Spanish (Latino)": "Latino"}

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
KEY = re.compile(r"^(?P<key>[^\W\d_][^:=]{0,30}?)[\s._·-]*[:=](?!//)\s*(?P<val>.*)$")
MEDIAINFO_HEADER = re.compile(r"(general|video|audio|text|menu|chapters?|image|other)(\s*#\s*\d+)?")
SUB_KEY = re.compile(r"(subs?|subtitles?|subtitulos|subtítulos|subtiles|subpacks?|untertitel|ut|st|sous|titres?|sottotitoli|ondertitels?|undertekster|napisy|forced|text)")
AUDIO_KEY = re.compile(r"(audio|audios|sound|ton|tonspur|tonspuren|tonformat|audiospur|audiospuren|dub|dubbing|spoken|synchro|synchronisation|tracks?)")
LANG_KEY = re.compile(r"(languages?|lang|langs|langues?|sprache|sprachen|lingua|lingue|idioma|idiomas)")
SUB_WORD = re.compile(r"\b(sub\w*|untertitel\w*|ut|vostfr|vost|sous[- ]?titr\w*|subt[ií]tul\w*|sottotitol\w*|ondertitel\w*|forced)\b", re.I)
BRAZIL = re.compile(r"\b(portugu\w*\s*[(\[]?\s*(brazil\w*|br)\b[)\]]?|pt[-_ ]?br)\b", re.I)
LATINO = re.compile(r"\bspanish\s*[(\[]?\s*(latin\w*|lat|la)\b[)\]]?", re.I)
MULTI = re.compile(r"(?<![A-Za-z0-9])multi(?:[ ._-]?audio)?(?![A-Za-z0-9])", re.I)


class Unsupported(Exception):
    pass


class Refused(Exception):
    pass


def words_to_languages(text):
    text = LATINO.sub(" Latino ", BRAZIL.sub(" Brazilian ", text))
    out = []
    for w in re.findall(r"[^\W\d_]+", text):
        name = CODES2.get(w) if len(w) == 2 and w.isupper() else LEXICON.get(w.casefold())
        if name and name not in out:
            out.append(name)
    return out


def without_subtitles(text):
    m = SUB_WORD.search(text)
    if not m:
        return text
    start = max((text.rfind(c, 0, m.start()) for c in ",;|/([{"), default=-1)
    return text[:max(start, 0)]


def key_kind(key, block):
    words = re.findall(r"[^\W\d_]+", key.casefold())
    if any(SUB_KEY.fullmatch(w) for w in words):
        return "sub"
    if any(AUDIO_KEY.fullmatch(w) for w in words):
        return "audio"
    if any(LANG_KEY.fullmatch(w) for w in words):
        return block if block in ("audio", "sub") else ("other" if block else "audio")
    return "other"


def audio_languages(text):
    """Audio languages an NFO names (Radarr/Sonarr names, in order): values of audio/language keys, their indented continuation lines and MediaInfo audio blocks; subtitle parts are dropped."""
    found, block, section, section_indent = [], None, None, -1

    def collect(s):
        for name in words_to_languages(without_subtitles(s)):
            if name not in found:
                found.append(name)

    for raw in (text or "").splitlines():
        line = ANSI.sub("", raw).replace("\t", "    ")
        first = re.search(r"[^\W_]|#", line)
        if not first:
            section = None
            continue
        indent, body = first.start(), line[first.start():].rstrip()
        head = MEDIAINFO_HEADER.fullmatch(body.casefold())
        if head:
            block = "audio" if head.group(1) == "audio" else "sub" if head.group(1) == "text" else "other"
            section = None
            continue
        km = KEY.match(body)
        if km and section == "audio" and indent > section_indent and words_to_languages(km.group("key")):
            collect(body)
            continue
        if km:
            section, section_indent = key_kind(km.group("key"), block), indent
            if section == "audio":
                collect(km.group("val"))
            continue
        if section == "audio" and indent > section_indent:
            collect(body)
        elif section and indent <= section_indent:
            section = None
    return found


def newznab_error(body):
    m = re.search(r'<error\s+code="(\d+)"(?:\s+description="([^"]*)")?', body[:2000])
    if not m:
        return None
    code, desc = int(m.group(1)), html.unescape(m.group(2) or "")
    if code == 300:
        return "none"
    if code in (202, 203):
        raise Unsupported(f"newznab error {code} {desc}")
    if code in (200, 201):
        raise ApiError(f"newznab error {code} {desc}")
    raise Refused(f"newznab error {code} {desc}")


def own_item(body, gid):
    items = re.findall(r"<item>(.*?)</item>", body, re.S)
    if not items:
        return None
    for item in items:
        g = re.search(r"<guid[^>]*>(.*?)</guid>", item, re.S)
        if g and gid.lower() in g.group(1).lower():
            return item
    raise Unsupported("answered with other releases, not this one")


def nfo_text(body, gid):
    """The NFO in a t=getnfo answer: plain text, or an RSS item's description; None = no NFO."""
    s = (body or "").lstrip("﻿ \r\n\t")
    if not s.startswith("<"):
        return body or None
    if newznab_error(s) == "none":
        return None
    item = own_item(s, gid)
    if item is None:
        return None
    d = re.search(r"<description>(.*?)</description>", item, re.S)
    if not d:
        return None
    t = re.sub(r"^\s*<!\[CDATA\[(.*)\]\]>\s*$", r"\1", d.group(1), flags=re.S)
    return html.unescape(t) or None


def details_languages(body, gid):
    """The newznab `language` attributes of a t=details answer (NZBgeek), as Radarr/Sonarr names."""
    s = (body or "").lstrip()
    if newznab_error(s) == "none":
        return []
    item = own_item(s, gid)
    if item is None:
        return []
    out = []
    names = {n.casefold(): n for n in LEXICON.values()}
    for v in re.findall(r'name="language"\s+value="([^"]*)"', item):
        v = html.unescape(v).strip()
        name = names.get(v.casefold()) or LEXICON.get(v.casefold())
        if name and name not in out:
            out.append(name)
    return out


def guid_id(guid):
    m = re.search(r"(?:guid=|/details/|/getnzb/|[?&]id=)([0-9a-fA-F]{16,64})\b", guid or "")
    return m.group(1).lower() if m else None


def has_original(langs, original):
    return original in langs or any(l.startswith(original + " (") for l in langs)


def multi_marker(title):
    return MULTI.search(title or "")


def token(name):
    return TOKENS.get(name) or re.sub(r"[^A-Za-z]", "", name)


def amend_title(title, langs):
    m = multi_marker(title)
    return f"{title[:m.end()]}.{'.'.join(token(l) for l in langs)}.{MARKER}{title[m.end():]}"


def parsed_info(p):
    return (p or {}).get("parsedMovieInfo") or (p or {}).get("parsedEpisodeInfo") or {}


def parsed_languages(p):
    langs = (p or {}).get("languages") or parsed_info(p).get("languages") or []
    return [l["name"] for l in langs if l.get("name") not in NOT_LANGUAGES]


def quality_of(p):
    info = parsed_info(p)
    q = ((info.get("quality") or {}).get("quality") or {})
    return q.get("name"), (info.get("releaseGroup") or "").casefold()


def verified_title(title, langs, original, parse):
    """The amended name whose parse gives exactly these languages (extras the parser cannot name are dropped), same quality and group; else (None, None, why)."""
    base = quality_of(parse(title))
    want = list(langs)
    for _ in range(len(langs) + 1):
        t = amend_title(title, want)
        p = parse(t)
        if quality_of(p) != base:
            return None, None, f"quality/group {quality_of(p)} differs from {base} after renaming"
        got = parsed_languages(p)
        if set(got) - set(want):
            return None, None, f"parses as {got}, not {want}"
        missing = [l for l in want if l not in got]
        if not missing:
            if len(want) < 2:
                return None, None, f"only {want} can be written into the name"
            return t, want, ""
        if set(missing) & {original, "English", "German"}:
            return None, None, f"{', '.join(missing)} cannot be written into the name"
        want = [l for l in want if l not in missing]
    return None, None, "no stable name"


def parse_time(s):
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


class Budget:
    """API calls per indexer: max_fetch per run each; Treasure Maps also tm_day per rolling 24 h and none once its bar window (searches used + these calls) is at tm_pct %."""

    def __init__(self, calls, now, tm_day, tm_pct, usage, max_fetch, max_age=900):
        self.now, self.tm_day, self.tm_pct, self.max_fetch = now, tm_day, tm_pct, max_fetch
        self.calls = {k: [t for t in v if t > now - DAY] for k, v in (calls or {}).items()}
        self.run = {}
        self.tm = None
        if usage and usage.get("ok") and (parse_time(usage.get("generated")) or 0) > now - max_age:
            self.tm = next((i for i in usage.get("indexers") or [] if i.get("name") == TM), None)

    def tm_window_ok(self, extra=0):
        if not self.tm:
            return False
        limit = (self.tm.get("searches") or {}).get("limit")
        if not limit:
            return True
        used = ((self.tm.get("searches") or {}).get("used") or 0) + len(self.calls.get(TM, [])) + extra
        return used * 100 < self.tm_pct * limit

    def allowed(self, name):
        if self.run.get(name, 0) >= self.max_fetch:
            return False
        if name == TM:
            return len(self.calls.get(TM, [])) < self.tm_day and self.tm_window_ok()
        return True

    def spend(self, name):
        self.run[name] = self.run.get(name, 0) + 1
        self.calls.setdefault(name, []).append(self.now)


class Scout:
    def __init__(self, radarr, sonarr, prowlarr, fetch, state_path, log, dry_run=False, max_push=3, usage=None, now=None,
                 max_fetch=200, tm_day=50, tm_pct=70, tm_search_day=100, retry_unsupported_days=30, call_delay=0, dub_paths=None):
        self.radarr, self.sonarr, self.prowlarr, self.fetch, self.log = radarr, sonarr, prowlarr, fetch, log
        self.state_path = pathlib.Path(state_path)
        self.dry_run, self.max_push, self.tm_search_day = dry_run, max_push, tm_search_day
        self.now = now or time.time()
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.releases = self.state.setdefault("releases", {})
        self.unsupported = self.state.setdefault("unsupported", {})
        for name, u in list(self.unsupported.items()):
            if u.get("at", 0) < self.now - retry_unsupported_days * DAY:
                del self.unsupported[name]
        self.budget = Budget(self.state.get("calls"), self.now, tm_day, tm_pct, usage, max_fetch)
        self.tm_searches = [t for t in self.state.get("tm_searches", []) if t > self.now - DAY]
        self.refused = {k: v for k, v in self.state.get("refused", {}).items() if v > self.now - DAY}
        self.call_delay, self.last_call = call_delay, {}
        self.pushes = self.candidates = 0
        self.dub_paths, self.dub = {k: pathlib.Path(v) for k, v in (dub_paths or {}).items()}, {}

    def dub_load(self, app_name):
        if app_name not in self.dub:
            try:
                entries = json.loads(self.dub_paths[app_name].read_text())
                entries = [e for e in entries if e.get("at", 0) > self.now - DUB_DAYS * DAY]
            except (OSError, ValueError, AttributeError):
                entries = []
            self.dub[app_name] = {e.get("guid"): e for e in entries}
        return self.dub[app_name]

    def dub_add(self, app_name, item, r, langs, original):
        if app_name not in self.dub_paths or not original or original not in set(LEXICON.values()) or len(langs) < 2 or has_original(langs, original):
            return
        title_id = item.get("tvdbId" if app_name == "Sonarr" else "tmdbId")
        entries = self.dub_load(app_name)
        entries.setdefault(r["guid"], {"guid": r["guid"], "title": r["title"], "indexer": r["indexer"], "langs": langs, "original": original,
                                       "titleId": title_id, "at": int(self.now)})

    def dub_save(self):
        for app_name, path in self.dub_paths.items():
            entries = list(self.dub_load(app_name).values())
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entries, indent=1))
            tmp.chmod(0o664)
            tmp.replace(path)

    def save(self):
        self.state["calls"] = self.budget.calls
        self.state["tm_searches"] = self.tm_searches
        self.state["refused"] = self.refused
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True))
        tmp.replace(self.state_path)

    def run(self, tmdb=None, tvdb=None):
        try:
            self.pindexers = [i for i in self.prowlarr("GET", "/indexer") if i.get("protocol") == "usenet" and i.get("enable") and i["name"] in SOURCES]
        except ApiError as e:
            self.log(f"Prowlarr API error, run skipped: {e}")
            return 1
        rc = 0
        if self.radarr and tvdb is None:
            rc |= self.run_app("Radarr", self.radarr, tmdb)
        if self.sonarr and tmdb is None:
            rc |= self.run_app("Sonarr", self.sonarr, tvdb)
        self.save()
        self.dub_save()
        fetched = ", ".join(f"{k} {v}" for k, v in sorted(self.budget.run.items())) or "none"
        self.log(f"run done: {self.candidates} MULTi-only candidate(s), indexer calls {fetched}, pushes {self.pushes}"
                 f", Treasure Maps {len(self.budget.calls.get(TM, []))}/{self.budget.tm_day} NFO calls in 24 h")
        return rc

    def wanted(self, app, extra=None):
        ids = set()
        for kind in ("missing", "cutoff"):
            page = 1
            while True:
                r = app("GET", f"/wanted/{kind}", dict({"page": page, "pageSize": 1000, "monitored": "true"}, **(extra or {}))) or {}
                ids |= {(x.get("seriesId"), x["id"]) if extra is not None else x["id"] for x in r.get("records") or []}
                if page * 1000 >= (r.get("totalRecords") or 0):
                    break
                page += 1
        return ids

    def run_app(self, app_name, app, only_id):
        try:
            swiss = {t["id"] for t in app("GET", "/tag") if t["label"] == SWISS_TAG}
            app_indexers = {i["name"]: i for i in app("GET", "/indexer") if i.get("protocol") == "usenet"}
            if app_name == "Radarr":
                wanted = self.wanted(app)
                titles = [(m, None) for m in app("GET", "/movie") if m["id"] in wanted and m.get("monitored")
                          and (only_id is None or m.get("tmdbId") == only_id)]
            else:
                eps = {}
                for sid, eid in self.wanted(app, {}):
                    eps.setdefault(sid, set()).add(eid)
                titles = [(s, eps[s["id"]]) for s in app("GET", "/series") if s["id"] in eps and s.get("monitored")
                          and (only_id is None or s.get("tvdbId") == only_id)]
        except ApiError as e:
            self.log(f"{app_name} API error, skipped: {e}")
            return 1
        rc = 0
        for item, wanted_eps in titles:
            if set(item.get("tags") or []) & swiss:
                continue
            name = f"{item['title']} ({item.get('year')}{', series' if wanted_eps is not None else ''})"
            try:
                self.title(name, item, wanted_eps, app_name, app, app_indexers)
            except ApiError as e:
                self.log(f"{name}: API error, skipped: {e}")
                rc = 1
        return rc

    def search(self, item, series):
        ids = [i for i in self.pindexers if i["name"] not in self.unsupported]
        if any(i["name"] == TM for i in ids):
            if len(self.tm_searches) >= self.tm_search_day or not self.budget.tm_window_ok():
                ids = [i for i in ids if i["name"] != TM]
            else:
                self.tm_searches.append(self.now)
        param, key, kind = ("tvSearchParams", "tvdbId", "tvsearch") if series else ("movieSearchParams", "imdbId", "movie")
        by_id = [i["id"] for i in ids if key in ((i.get("capabilities") or {}).get(param) or []) and item.get(key)]
        by_name = [i["id"] for i in ids if i["id"] not in by_id]
        queries = []
        if by_id:
            queries.append((f"{{TvdbId:{item['tvdbId']}}}" if series else f"{{ImdbId:{item['imdbId']}}}", by_id))
        if by_name:
            queries.append((ds.search_title(item["title"]) if series else f"{item['title']} {item.get('year')}", by_name))
        seen, results = set(), []
        for q, idx in queries:
            for r in self.prowlarr("GET", "/search", {"query": q, "type": kind, "categories": [5000 if series else 2000], "indexerIds": idx}) or []:
                if r.get("guid") not in seen:
                    seen.add(r.get("guid"))
                    results.append(r)
        return results

    def title(self, name, item, wanted_eps, app_name, app, app_indexers):
        series = wanted_eps is not None
        parse = lambda t: app("GET", "/parse", {"title": t})
        mapping = (lambda p, imdb: ds.mapping_ok_series(item, p, imdb, wanted_eps)) if series else (lambda p, imdb: ds.mapping_ok(item, p, imdb))
        results = self.search(item, series)
        original = (item.get("originalLanguage") or {}).get("name")
        cands = []
        for r in results:
            idx = r.get("indexer")
            gid = guid_id(r.get("guid"))
            if r.get("protocol") != "usenet" or idx not in SOURCES or not gid or not multi_marker(r.get("title")):
                continue
            st = self.releases.get(f"{idx}|{gid}", {})
            earlier_dub = st.get("verdict") == "skip" and bool(original) and not has_original(st.get("langs") or [], original)
            if (st.get("pushed") or st.get("verdict") in ("none", "failed", "skip")) and not earlier_dub:
                continue
            p = parse(r["title"])
            if len(parsed_languages(p)) >= 2 or not mapping(p, r.get("imdbId") or None)[0]:
                continue
            if earlier_dub:
                self.dub_add(app_name, item, r, st["langs"], original)
                continue
            res = ((parsed_info(p).get("quality") or {}).get("quality") or {}).get("resolution") or 0
            cands.append((res, r.get("size") or 0, r, gid))
        cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
        self.log(f"{name}: {len(results)} usenet results, {len(cands)} MULTi-only to check")
        self.candidates += len(cands)
        for _, _, r, gid in cands:
            idx, key = r["indexer"], f"{r['indexer']}|{gid}"
            langs = self.languages(name, r, gid, key)
            if langs is None:
                continue
            st = self.releases[key]
            if original not in langs:
                st.update(verdict="skip", why=f"no {original} audio in the NFO")
                self.log(f"{name}: '{r['title']}' ({idx}): audio {', '.join(langs)} has no {original} (original), not pushed")
                self.dub_add(app_name, item, r, langs, original)
                continue
            title, used, why = verified_title(r["title"], langs, original, parse)
            if not title:
                st.update(verdict="skip", why=why)
                self.log(f"{name}: '{r['title']}' ({idx}): {why}, not pushed")
                continue
            p = parse(title)
            ok, why = mapping(p, r.get("imdbId") or None)
            if not ok:
                st.update(verdict="skip", why=why)
                self.log(f"{name}: '{title}': {why} after renaming, not pushed")
                continue
            payload = self.payload(r, item, title, app_indexers, series)
            if not payload:
                self.log(f"{name}: '{r['title']}': indexer {idx} not in {app_name} or download link not from Prowlarr, not pushed")
                continue
            before, after = parse(r["title"]).get("customFormatScore") or 0, p.get("customFormatScore") or 0
            score = f"score {before} -> {after}"
            if after < 0 or after <= before:
                st.update(verdict="skip", why=score)
                self.log(f"{name}: '{title}' ({idx}, audio {', '.join(used)}): {score}, {'rejected anyway' if after < 0 else 'no gain'}, not pushed")
                continue
            if self.pushes >= self.max_push:
                self.log(f"{name}: '{title}': push cap ({self.max_push}) reached, next run")
                continue
            self.pushes += 1
            if self.dry_run:
                self.log(f"{name}: dry run, would push '{title}' ({idx}, audio {', '.join(used)}, {score}) to {app_name}")
                continue
            try:
                answer = app("POST", "/release/push", body=payload)
            except ApiError as e:
                st["errors"] = st.get("errors", 0) + 1
                if st["errors"] >= MAX_ERRORS:
                    st["verdict"] = "failed"
                self.save()
                self.log(f"{name}: '{title}': push failed ({st['errors']}/{MAX_ERRORS}): {e}")
                continue
            d = (answer or [{}])[0]
            st.update(pushed=time.strftime("%Y-%m-%dT%H:%M:%S"), approved=bool(d.get("approved")), pushed_title=title)
            self.save()
            rej = "; ".join(x.get("reason", str(x)) if isinstance(x, dict) else str(x) for x in d.get("rejections") or [])
            self.log(f"{name}: pushed '{title}' ({idx}, audio {', '.join(used)}, {score}) to {app_name}: "
                     f"{'approved, ' + app_name + ' grabs it' if d.get('approved') else 'rejected: ' + rej}")

    def languages(self, name, r, gid, key):
        idx = r["indexer"]
        st = self.releases.get(key, {})
        if st.get("verdict") == "langs":
            return st["langs"]
        if idx in self.unsupported or idx in self.refused:
            return None
        if not self.budget.allowed(idx):
            self.log(f"{name}: '{r['title']}' ({idx}): indexer call budget reached, next run")
            return None
        self.budget.spend(idx)
        source = SOURCES[idx]
        if idx in self.last_call:
            time.sleep(max(0.0, self.last_call[idx] + self.call_delay - time.monotonic()))
        self.last_call[idx] = time.monotonic()
        try:
            body = self.fetch(idx, source, gid)
            langs = details_languages(body, gid) if source == "details" else audio_languages(nfo_text(body, gid))
        except Unsupported as e:
            self.unsupported[idx] = {"at": self.now, "why": str(e), "function": source}
            self.save()
            self.log(f"{idx}: t={source} not supported ({e}), indexer skipped for {idx} until retried")
            return None
        except Refused as e:
            self.refused[idx] = self.now
            self.save()
            self.log(f"{idx}: refused ({e}), no NFO calls to it for 24 h")
            return None
        except ApiError as e:
            errors = st.get("errors", 0) + 1
            self.releases[key] = dict(st, errors=errors, title=r["title"], **({"verdict": "failed"} if errors >= MAX_ERRORS else {}))
            self.save()
            self.log(f"{name}: '{r['title']}' ({idx}): t={source} failed ({errors}/{MAX_ERRORS}): {e}")
            return None
        verdict = "langs" if len(langs) >= 2 else "none"
        self.releases[key] = {"verdict": verdict, "langs": langs, "title": r["title"], "source": source, "at": time.strftime("%Y-%m-%d", time.localtime(self.now))}
        self.save()
        self.log(f"{name}: '{r['title']}' ({idx}, {source}): audio {', '.join(langs) or 'not named'}" + ("" if verdict == "langs" else ", not pushed"))
        return langs if verdict == "langs" else None

    def payload(self, r, item, title, app_indexers, series):
        indexer = app_indexers.get(f"{r['indexer']} (Prowlarr)")
        if not indexer:
            return None
        base = next((f.get("value") for f in indexer.get("fields") or [] if f.get("name") == "baseUrl"), "") or ""
        url = app_download_url(r.get("downloadUrl"), base)
        if not url:
            return None
        p = {"title": title, "guid": r.get("guid"), "downloadUrl": url, "infoUrl": r.get("infoUrl"), "protocol": "usenet",
             "indexerId": indexer["id"], "indexer": indexer["name"], "size": r.get("size") or 0, "publishDate": r.get("publishDate")}
        if series:
            p["tvdbId"] = item.get("tvdbId") or 0
        else:
            p.update(tmdbId=item.get("tmdbId") or 0, imdbId=ds.imdb_num(item.get("imdbId")))
        return p


def app_download_url(url, base):
    """Prowlarr's download link as the app reaches Prowlarr (its indexer base URL); None unless it is that indexer's Prowlarr link."""
    u, b = urllib.parse.urlsplit(url or ""), urllib.parse.urlsplit(base or "")
    if not (u.scheme and b.scheme and b.path.strip("/")) or not u.path.startswith("/" + b.path.strip("/") + "/download"):
        return None
    return urllib.parse.urlunsplit((b.scheme, b.netloc, u.path, u.query, u.fragment))


def indexer_fetch(db):
    """t=<function>&id=<id> straight to the indexer (Prowlarr's proxy has no getnfo/details); base URL and key from Prowlarr's database, never logged."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        settings = {n: json.loads(s) for n, s in con.execute("SELECT Name, Settings FROM Indexers")}
    finally:
        con.close()

    def fetch(name, function, gid):
        s = settings.get(name) or {}
        if not s.get("baseUrl") or not s.get("apiKey"):
            raise Unsupported("no base URL or key in Prowlarr")
        url = s["baseUrl"].rstrip("/") + (s.get("apiPath") or "/api") + "?" + urllib.parse.urlencode({"t": function, "id": gid, "apikey": s["apiKey"]})
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if "<error" in body:
                return body
            raise ApiError(f"HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ApiError(type(e).__name__ + ": " + str(getattr(e, "reason", "") or "connection failed")) from None
    return fetch


def read_usage(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch within budget and cache what was read, never push")
    ap.add_argument("--tmdb", type=int, help="only this movie")
    ap.add_argument("--tvdb", type=int, help="only this series")
    a = ap.parse_args()
    env = os.environ.get
    logfile = pathlib.Path(env("NS_LOG", STATE_DIR / "nfo-scout.log"))
    logfile.parent.mkdir(parents=True, exist_ok=True)

    def log(msg):
        msg = re.sub(r"(?i)(apikey|api_key|r)=[^&\s'\"]+", r"\1=***", msg.replace("\n", " "))
        print(msg, flush=True)
        with logfile.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {'[dry run] ' if a.dry_run else ''}{msg}\n")

    apps = env("NS_APPS", "radarr,sonarr").split(",")
    clients = {}
    for app, port in (("radarr", 7878), ("sonarr", 8989)):
        if app in apps:
            key, aport, base = ds.config(env(f"NS_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml"))
            clients[app] = ds.client(f"http://localhost:{aport or port}{base}/api/v3", key)
    pkey, _, _ = ds.config(env("NS_PROWLARR_CONFIG", "/srv/appdata/prowlarr/config.xml"))
    scout = Scout(clients.get("radarr"), clients.get("sonarr"), ds.client(env("NS_PROWLARR_URL", "http://127.0.0.1:9696") + "/api/v1", pkey),
                  indexer_fetch(env("NS_PROWLARR_DB", "/srv/appdata/prowlarr/prowlarr.db")), env("NS_STATE", STATE_DIR / "nfo-scout.json"), log,
                  dub_paths={a.capitalize(): env(f"NS_{a.upper()}_DUB_ONLY", f"/srv/appdata/{a}/dub-only.json") for a in clients},
                  dry_run=a.dry_run, max_push=int(env("NS_MAX_PUSH", 3)), usage=read_usage(env("NS_USAGE", "/srv/appdata/indexer-bar/usage.json")),
                  max_fetch=int(env("NS_MAX_FETCH", 200)), tm_day=int(env("NS_TM_MAX_DAY", 50)), tm_pct=int(env("NS_TM_MAX_PCT", 70)),
                  tm_search_day=int(env("NS_TM_MAX_SEARCH_DAY", 100)), call_delay=float(env("NS_CALL_DELAY", 1)))
    return scout.run(tmdb=a.tmdb, tvdb=a.tvdb)


if __name__ == "__main__":
    sys.exit(main())
