#!/usr/bin/env python3
"""Lists for the pages "Popular movies" / "Popular shows" (one row per service and per genre, each only films or only series) and the home rows "Top 10 Shows / Movies in Switzerland" (home-placeholders/empty-sections.js).
Sources: Netflix's official weekly Top 10 for Switzerland (TSV) and TMDB popularity of what streams in Switzerland per service and per genre (Seerr's discover API, watch region CH: Seerr's TMDB access, no own key).
Every rank is listed; a title not in the library is requestable only when a suitable release exists: Prowlarr searches (never grabs) on NzbPlanet / NZBgeek (by id) and Knaben (by name), throttled, cached (no release: again the next night; found: weekly; owned: never).
Writes the JavaScript Injector entry "Currently popular" (window.HssPopular) and the request allow-list for the popular backend (opaque entry ids; the backend accepts nothing else).
Never prints API keys. Usage: popular.py [--dry-run] [--tsv FILE]  (FILE: the Netflix TSV downloaded elsewhere, when netflix.com is unreachable from beefy)"""
import datetime
import io
import itertools
import json
import os
import pathlib
import re
import secrets
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NAME = "Currently popular"
PLUGIN = "JavaScript Injector"
COUNTRY = "CH"
TSV_URL = "https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv"
SEERR = "http://127.0.0.1:5055"
SEERR_KEY = HERE.parent.parent / "Seerr" / "secrets" / "api-key"
PROWLARR = ("http://127.0.0.1:9696", "/srv/appdata/prowlarr/config.xml")
STATE_FILE = pathlib.Path.home() / ".local" / "state" / "jellyfin-popular.json"
ALLOW_FILE = pathlib.Path("/srv/appdata/jellyfin-popular/allow.json")
PROM_FILE = pathlib.Path.home() / ".local" / "state" / "jellyfin-popular.prom"
OVERRIDES = HERE / "popular-overrides.tsv"
# Streaming services in Switzerland (TMDB watch provider ids, region CH), one row each, in this order.
SERVICES = [(8, "Netflix"), (119, "Prime Video"), (337, "Disney+"), (350, "Apple TV+"), (210, "Sky"), (691, "Play Suisse"), (531, "Paramount+"), (150, "blue TV")]
# (row id, English, German, TMDB film genres, TMDB series genres; "|" = any of them, None = no series)
GENRES = [
    ("action", "Action", "Action", "28", "10759"),
    ("comedy", "Comedy", "Komödie", "35", "35"),
    ("drama", "Drama", "Drama", "18", "18"),
    ("thriller", "Thriller & Crime", "Thriller & Krimi", "53|80", "80|9648"),
    ("scifi", "Sci-Fi & Fantasy", "Sci-Fi & Fantasy", "878|14", "10765"),
    ("animation", "Animation", "Animation", "16", "16"),
    ("documentary", "Documentaries", "Dokumentationen", "99", "99"),
    ("family", "Kids & Family", "Kinder & Familie", "10751", "10762|10751"),
    ("horror", "Horror", "Horror", "27", None),
    ("romance", "Romance", "Romantik", "10749", None),
]
ROW_SIZE = 10
CANDIDATES = 30
SEARCH_MAX = int(os.environ.get("POP_SEARCH_MAX", "300"))
SEARCH_DELAY = float(os.environ.get("POP_SEARCH_DELAY", "2"))
INDEXERS = ["NzbPlanet", "NZBgeek", "Knaben"]
# Runs at 04:39 and 22:20: a title without a release is searched once per night, a found one once a week.
RECHECK_NONE = datetime.timedelta(hours=20)
RECHECK_FOUND = datetime.timedelta(days=7)
# A found release not re-checked for this long (search budget used up) is no longer offered.
FOUND_MAX_AGE = datetime.timedelta(days=14)
# Failures of single titles (Seerr / TMDB) are skipped; more than this share of the titles failing fails the run and keeps the published lists.
FAIL_SHARE = 0.2
FAIL_MIN = 3
FAILURES = (OSError, urllib.error.URLError, ValueError, KeyError, TypeError)
LISTS_TTL = datetime.timedelta(days=7)
DETAILS_TTL = datetime.timedelta(days=7)
MIN_GB = {"movie": 1.5, "episode": 0.3, "pack": 1.0}
SEASON_RE = re.compile(r"\b(?:season|series|staffel)\s+(\d+)\b", re.I)
BAD = {"cam", "camrip", "hdcam", "ts", "hdts", "telesync", "tc", "hdtc", "telecine", "scr", "screener", "dvdscr", "workprint"}
LANG_TOKENS = {"german": "de", "deutsch": "de", "french": "fr", "vff": "fr", "vf": "fr", "truefrench": "fr", "italian": "it", "ita": "it", "spanish": "es", "esp": "es",
               "latino": "es", "castellano": "es", "hindi": "hi", "russian": "ru", "rus": "ru", "polish": "pl", "dutch": "nl", "turkish": "tr", "portuguese": "pt",
               "english": "en", "eng": "en"}
MULTI = {"dl", "dual", "multi", "2audio", "dualaudio"}


def norm(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def ref(kind, tmdb, season):
    return f"movie/{tmdb}" if kind == "movie" else f"tv/{tmdb}/s{season}"


def parse_week(lines, country):
    """Rows of the newest week of `country`: {week, movie: [...], tv: [...]}, entries {rank, title, season (Netflix season title or None)}."""
    rows, head = [], None
    for line in lines:
        cols = line.rstrip("\r\n").split("\t")
        if head is None:
            head = {c: i for i, c in enumerate(cols)}
            continue
        if len(cols) >= len(head) and cols[head["country_iso2"]] == country:
            rows.append(cols)
    if not rows:
        return None
    week = max(r[head["week"]] for r in rows)
    out = {"week": week, "movie": [], "tv": []}
    for r in rows:
        if r[head["week"]] == week:
            season = r[head["season_title"]]
            out["movie" if r[head["category"]] == "Films" else "tv"].append(
                {"rank": int(r[head["weekly_rank"]]), "title": r[head["show_title"]], "season": None if season in ("", "N/A") else season})
    for kind in ("movie", "tv"):
        out[kind].sort(key=lambda e: e["rank"])
    return out


def due_week(now):
    """The newest Netflix week (ending Sunday) that should be out: Netflix publishes it on the Tuesday after."""
    day = now.date() - datetime.timedelta(days=2)
    return (day - datetime.timedelta(days=(day.weekday() + 1) % 7)).isoformat()


def load_overrides(text):
    out = {}
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            kind, title, tmdb = line.split("\t")[:3]
            out[(kind.strip(), norm(title))] = int(tmdb)
    return out


def pick(title, results, kind):
    want = norm(title)
    names = ("title", "originalTitle") if kind == "movie" else ("name", "originalName")
    hits = [r for r in results if r.get("mediaType") == kind and want in {norm(r.get(n)) for n in names}]
    return max(hits, key=lambda r: r.get("popularity") or 0) if hits else None


def season_number(show, season_title, seasons, today):
    regular = [s for s in seasons or [] if (s.get("seasonNumber") or 0) > 0]
    if not regular:
        return None
    if season_title:
        m = SEASON_RE.search(season_title)
        if m:
            n = int(m.group(1))
            return n if any(s["seasonNumber"] == n for s in regular) else None
        rest = re.sub(r"^\s*" + re.escape(show) + r"\s*[:\-–]\s*", "", season_title, flags=re.I)
        for s in regular:
            if norm(s.get("name")) in (norm(season_title), norm(rest)):
                return s["seasonNumber"]
        if len(regular) == 1:
            return regular[0]["seasonNumber"]
    aired = [s for s in regular if s.get("airDate") and s["airDate"] <= today]
    return max(aired or regular, key=lambda s: s["seasonNumber"])["seasonNumber"]


def _names_it(tokens, title, year, kind, season):
    want = norm(title).split()
    if tokens[:len(want)] != want:
        return False
    rest = tokens[len(want):]
    if kind == "movie":
        return bool(rest) and rest[0].isdigit() and len(rest[0]) == 4 and year is not None and abs(int(rest[0]) - year) <= 1
    i = 0
    while i < 2 and i < len(rest) and ((rest[i].isdigit() and len(rest[i]) == 4) or (len(rest[i]) == 2 and rest[i].isalpha())):
        i += 1
    return i < len(rest) and re.fullmatch(rf"s0*{season}(e\d+)*", rest[i]) is not None


def suitable(r, kind, lang, title, year, season, text):
    """The release's resolution ("2160p" / "1080p") when it fits the library rules (1080p or better, no cam/TS, plausible size, no dub without the original audio, a seeder), else None.
    text: found by name, so the name must start with the title (+ year / season)."""
    tokens = norm(r.get("title")).split()
    words = set(tokens)
    res = "2160p" if words & {"2160p", "4k", "uhd"} else "1080p" if "1080p" in words else None
    if not res or words & BAD:
        return None
    langs = {LANG_TOKENS[w] for w in words if w in LANG_TOKENS}
    if langs and lang not in langs and not words & MULTI and "dual audio" not in " ".join(tokens):
        return None
    episode = kind == "tv" and any(re.fullmatch(r"s\d+e\d+", w) for w in words)
    size = (r.get("size") or 0) / 1e9
    if size < MIN_GB["movie" if kind == "movie" else "episode" if episode else "pack"]:
        return None
    if r.get("protocol") == "torrent" and (r.get("seeders") or 0) < 1:
        return None
    if text and not _names_it(tokens, title, year, kind, season):
        return None
    return res


def best(results, kind, lang, title, year, season, text):
    found = {suitable(r, kind, lang, title, year, season, text) for r in results or []} - {None}
    return "2160p" if "2160p" in found else "1080p" if found else None


def ranked(kind, results, n):
    seen, out = set(), []
    for r in sorted(results, key=lambda r: -(r.get("popularity") or 0)):
        if r["id"] not in seen:
            seen.add(r["id"])
            out.append((kind, r["id"]))
    return out[:n]


def row_ids(services, genres):
    """(row id, kind, service id or genre) in page order: per service its movie row, then its tv row; then the genres (tv only with TV genres)."""
    out = []
    for p, _ in services:
        out += [(f"service-{p}-{k}", k, p) for k in ("movie", "tv")]
    for g in genres:
        out += [(f"genre-{g[0]}-{k}", k, g) for k in ("movie", "tv") if k == "movie" or g[4]]
    return out


def check_due(cache, now):
    if not cache:
        return True
    age = now - datetime.datetime.fromisoformat(cache["at"])
    return age >= (RECHECK_FOUND if cache.get("res") else RECHECK_NONE)


def script(data):
    return "window.HssPopular = " + json.dumps(data, ensure_ascii=False, sort_keys=True).replace("</", "<\\/") + ";\n"


def upsert(config, source):
    from importlib import util
    spec = util.spec_from_file_location("tracker_link", HERE / "tracker-link.py")
    tl = util.module_from_spec(spec)
    spec.loader.exec_module(tl)
    return tl.upsert_script(config, NAME, source)


def _key(path):
    return re.search(r"<ApiKey>([^<]+)</ApiKey>", pathlib.Path(path).read_text()).group(1)


class Io:
    """Everything that leaves the script; tests use a fake."""

    def tsv(self, known):
        """(lines or None when Last-Modified is still `known`, Last-Modified). ~33 MB without conditional GET support: the body is read only when it changed."""
        resp = urllib.request.urlopen(urllib.request.Request(TSV_URL, headers={"User-Agent": "Mozilla/5.0 (holy-grail popular)"}), timeout=60)
        lm = resp.headers.get("Last-Modified")
        if known and lm == known:
            resp.close()
            return None, lm
        with resp:
            return io.TextIOWrapper(resp, encoding="utf-8").read().splitlines(), lm

    def seerr(self, path):
        req = urllib.request.Request(SEERR + path, headers={"X-Api-Key": SEERR_KEY.read_text().strip()})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def _prowlarr(self, path):
        req = urllib.request.Request(PROWLARR[0] + path, headers={"X-Api-Key": _key(PROWLARR[1])})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    def indexers(self):
        return {i["name"]: i["id"] for i in self._prowlarr("/api/v1/indexer") if i.get("enable")}

    def search(self, params):
        return self._prowlarr("/api/v1/search?" + urllib.parse.urlencode(params))

    def owned(self):
        import jellyfin_api
        items = jellyfin_api.api("GET", "/Items?Recursive=true&IncludeItemTypes=Movie,Series&Fields=ProviderIds&EnableImages=false&EnableUserData=false")["Items"]
        return {("movie/" if i["Type"] == "Movie" else "tv/") + i["ProviderIds"]["Tmdb"]: i["Id"] for i in items if (i.get("ProviderIds") or {}).get("Tmdb")}

    def _plugin_path(self):
        import jellyfin_api
        pid = next(p["Id"] for p in jellyfin_api.api("GET", "/Plugins") if p["Name"] == PLUGIN)
        return jellyfin_api, f"/Plugins/{pid}/Configuration"

    def injector_get(self):
        ja, path = self._plugin_path()
        return ja.api("GET", path)

    def injector_put(self, config):
        ja, path = self._plugin_path()
        ja.api("POST", path, config)

    def injector_lock(self):
        import jellyfin_api
        return jellyfin_api.injector_lock()


def load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_json(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False))
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def fresh(entry, ttl, now):
    return entry and now - datetime.datetime.fromisoformat(entry["at"]) < ttl


def details(io_, state, kind, tmdb, now):
    key = f"{kind}/{tmdb}"
    d = state.setdefault("details", {}).get(key)
    if fresh(d, DETAILS_TTL, now):
        return d
    en = io_.seerr(f"/api/v1/{kind}/{tmdb}?language=en")
    de = io_.seerr(f"/api/v1/{kind}/{tmdb}?language=de")
    ext = en.get("externalIds") or {}
    title = en.get("title") or en.get("name") or ""
    d = {"at": now.isoformat(timespec="seconds"), "title": title, "de": de.get("title") or de.get("name") or title,
         "poster": en.get("posterPath"), "posterDe": de.get("posterPath") or en.get("posterPath"), "lang": en.get("originalLanguage"),
         "year": int((en.get("releaseDate") or en.get("firstAirDate") or "0")[:4] or 0) or None,
         "imdb": en.get("imdbId") or ext.get("imdbId"), "tvdb": ext.get("tvdbId"),
         "seasons": [{"seasonNumber": s.get("seasonNumber"), "name": s.get("name"), "airDate": s.get("airDate")} for s in en.get("seasons") or []]}
    state["details"][key] = d
    return d


def search_plan(kind, d, season, ix):
    plan = []
    if kind == "movie":
        if d.get("imdb"):
            plan += [(ix.get(n), {"query": "{ImdbId:%s}" % d["imdb"], "type": "movie", "categories": "2000"}, False) for n in INDEXERS[:2]]
        plan.append((ix.get(INDEXERS[2]), {"query": f"{d['title']} {d.get('year') or ''}".strip(), "type": "search", "categories": "2000"}, True))
    else:
        if d.get("tvdb"):
            plan += [(ix.get(n), {"query": "{TvdbId:%s}{Season:%s}" % (d["tvdb"], season), "type": "tvsearch", "categories": "5000"}, False) for n in INDEXERS[:2]]
        plan.append((ix.get(INDEXERS[2]), {"query": f"{d['title']} S{season:02d}", "type": "search", "categories": "5000"}, True))
    return [(i, p, t) for i, p, t in plan if i is not None]


class Budget(Exception):
    pass


def check(io_, kind, d, season, ix, budget, sleep):
    for ixid, params, text in search_plan(kind, d, season, ix):
        if budget[0] <= 0:
            raise Budget
        budget[0] -= 1
        try:
            results = io_.search(dict(params, indexerIds=ixid))
        except (OSError, urllib.error.URLError, ValueError):
            results = []
        sleep(SEARCH_DELAY)
        res = best(results, kind, d.get("lang"), d["title"], d.get("year"), season, text)
        if res:
            return res
    return None


def write_prom(path, now, ok, stats):
    lines = ["# TYPE jf_popular_last_run_timestamp_seconds gauge", f"jf_popular_last_run_timestamp_seconds {now.timestamp():.0f}",
             "# TYPE jf_popular_last_run_success gauge", f"jf_popular_last_run_success {1 if ok else 0}",
             "# TYPE jf_popular_errors gauge", f"jf_popular_errors {stats.get('errors', 0)}"]
    if ok:
        lines += ["# TYPE jf_popular_items gauge"] + [f'jf_popular_items{{list="{k}"}} {n}' for k, (n, _) in stats["lists"].items()]
        lines += ["# TYPE jf_popular_items_available gauge"] + [f'jf_popular_items_available{{list="{k}"}} {a}' for k, (_, a) in stats["lists"].items()]
        lines += ["# TYPE jf_popular_searches_total gauge", f"jf_popular_searches_total {stats['searches']}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def main(argv, io_=None, now=None, state_file=STATE_FILE, overrides_file=OVERRIDES, out=sys.stdout, sleep=time.sleep, services=None, genres=None, allow_file=ALLOW_FILE, prom_file=PROM_FILE):
    now = now or datetime.datetime.now()
    stats = {}
    try:
        code = run(argv, io_, now, state_file, overrides_file, out, sleep, services, genres, allow_file, stats)
    except BaseException:
        if "--dry-run" not in argv:
            write_prom(prom_file, now, False, stats)
        raise
    if code == 0 and "lists" in stats:
        write_prom(prom_file, now, True, stats)
    elif code == 1 and "--dry-run" not in argv:
        write_prom(prom_file, now, False, stats)
    return code


def run(argv, io_, now, state_file, overrides_file, out, sleep, services, genres, allow_file, stats):
    args = list(argv)
    tsv_file = None
    if "--tsv" in args:
        i = args.index("--tsv")
        if i + 1 >= len(args):
            print(__doc__.strip(), file=sys.stderr)
            return 2
        tsv_file = pathlib.Path(args.pop(i + 1))
        args.pop(i)
    if any(a != "--dry-run" for a in args):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    dry = "--dry-run" in args
    io_ = io_ or Io()
    services = SERVICES if services is None else services
    genres = GENRES if genres is None else genres
    layout = row_ids(services, genres)
    stamp = now.isoformat(timespec="seconds")
    today = now.date().isoformat()

    def say(msg):
        print(msg, file=out)

    state = load_json(state_file)
    overrides = load_overrides(overrides_file.read_text()) if overrides_file.exists() else {}

    top = state.get("top")
    if tsv_file or not top or top.get("week", "") < due_week(now):
        try:
            lines, lm = (tsv_file.read_text().splitlines(), f"file {tsv_file.name}") if tsv_file else io_.tsv((state.get("tsv") or {}).get("lastModified"))
            parsed = parse_week(lines, COUNTRY) if lines is not None else None
            if parsed:
                top, state["tsv"] = parsed, {"lastModified": lm}
                say(f"Netflix {COUNTRY}: week {top['week']}")
            elif lines is None:
                say(f"Netflix list unchanged, week {top and top['week']} (due {due_week(now)})")
        except (OSError, urllib.error.URLError, ValueError) as e:
            say(f"Netflix list not fetched: {e}")
    state["top"] = top or {"week": None, "movie": [], "tv": []}

    tried, failed = set(), {}

    def failure(key, e):
        if key not in failed:
            failed[key] = type(e).__name__
            say(f"{key}: skipped this run ({type(e).__name__})")

    def refresh_lists(lists):
        if fresh(lists, LISTS_TTL, now) and sorted(lists.get("rows", {})) == sorted(r for r, _, _ in layout):
            return lists
        logos = {p["id"]: p.get("logoPath") for p in io_.seerr(f"/api/v1/watchproviders/movies?watchRegion={COUNTRY}")}
        union = urllib.parse.quote("|".join(str(p) for p, _ in services), safe="")

        def discover(kind, provider, genre):
            res = []
            for page in (1, 2):
                q = f"page={page}&watchRegion={COUNTRY}&watchProviders={provider}&sortBy=popularity.desc&language=en"
                if genre:
                    q += "&genre=" + urllib.parse.quote(genre, safe="")
                res += io_.seerr(f"/api/v1/discover/{'movies' if kind == 'movie' else 'tv'}?{q}").get("results") or []
            return res
        rows = {}
        for rid, kind, src in layout:
            if rid.startswith("service-"):
                rows[rid] = ranked(kind, discover(kind, src, None), CANDIDATES)
            else:
                rows[rid] = ranked(kind, discover(kind, union, src[3] if kind == "movie" else src[4]), CANDIDATES)
        say(f"lists refreshed: {len(rows)} rows")
        return {"at": stamp, "rows": rows, "logos": {str(k): v for k, v in logos.items()}}

    lists = state.get("lists")
    try:
        lists = state["lists"] = refresh_lists(lists)
    except FAILURES as e:
        if not lists:
            raise
        failure("lists", e)

    owned = io_.owned()
    cache = state.setdefault("checks", {})
    ix = io_.indexers() if not dry else {}
    budget = [SEARCH_MAX]

    def status(r):
        kind, tmdb = r.split("/")[:2]
        if f"{kind}/{tmdb}" in owned:
            return "owned"
        c = cache.get(r)
        if not c:
            return None
        if c.get("res") and now - datetime.datetime.fromisoformat(c["at"]) > FOUND_MAX_AGE:
            return None
        return c.get("res") or "none"

    def safe_details(kind, tmdb):
        key = f"{kind}/{tmdb}"
        tried.add(key)
        if key in failed:
            return state["details"].get(key)
        try:
            return details(io_, state, kind, tmdb, now)
        except FAILURES as e:
            failure(key, e)
            return state.setdefault("details", {}).get(key)

    def candidate(kind, tmdb, season_title=None, show=None, rank=None):
        d = safe_details(kind, tmdb)
        if d is None:
            return None
        season = season_number(show or d["title"], season_title, d["seasons"], today) if kind == "tv" else None
        if kind == "tv" and not season:
            return None
        return {"kind": kind, "tmdb": tmdb, "season": season, "ref": ref(kind, tmdb, season), "d": d, "rank": rank}

    def fill(cands, label):
        """Checks the release of every candidate that is due."""
        for c in cands:
            if c.get("unmatched"):
                continue
            st = status(c["ref"])
            if st is None or (st == "none" and check_due(cache.get(c["ref"]), now)) or (st in ("1080p", "2160p") and check_due(cache.get(c["ref"]), now)):
                if dry:
                    say(f"{label}: {c['ref']} {c['d']['title']!r}: unchecked")
                    continue
                try:
                    res = check(io_, c["kind"], c["d"], c["season"], ix, budget, sleep)
                except Budget:
                    continue
                cache[c["ref"]] = {"res": res, "at": stamp}
                st = status(c["ref"])
                say(f"{label}: {c['ref']} {c['d']['title']!r}: {res or 'no suitable release'}")

    home = {"shows": [], "movies": []}
    home_cands = {}
    for kind, key in (("tv", "shows"), ("movie", "movies")):
        cands = []
        for e in state["top"][kind]:
            tmdb = overrides.get((kind, norm(e["title"])))
            mkey = f"{kind}|{norm(e['title'])}"
            tmdb = tmdb or state.setdefault("matches", {}).get(mkey)
            if not tmdb:
                q = urllib.parse.quote(e["title"], safe="")
                tried.add(mkey)
                try:
                    h = pick(e["title"], io_.seerr(f"/api/v1/search?query={q}&page=1&language=en").get("results") or [], kind)
                except FAILURES as err:
                    failure(mkey, err)
                    h = None
                if h:
                    tmdb = state["matches"][mkey] = h["id"]
            c = candidate(kind, tmdb, e.get("season"), e["title"], e["rank"]) if tmdb else None
            if c is None:
                say(f"Netflix {kind} {e['rank']} {e['title']!r}: no match")
                c = {"unmatched": True, "kind": kind, "rank": e["rank"], "title": e["title"]}
            cands.append(c)
        fill(cands, f"Netflix {kind}")
        home_cands[key] = cands
        if not dry:
            save_json(state_file, state)

    row_cands = {}
    for rid, pairs in lists["rows"].items():
        cands = list(itertools.islice(filter(None, (candidate(k, t) for k, t in pairs)), ROW_SIZE))
        fill(cands, rid)
        row_cands[rid] = cands
        if not dry:
            save_json(state_file, state)

    tokens = state.setdefault("tokens", {})
    allow = {}

    def item(c, rank):
        if c.get("unmatched"):
            return {"k": c["kind"], "id": None, "s": None, "r": rank, "y": None, "t": {"en": c["title"], "de": c["title"]}, "p": {"en": None, "de": None},
                    "o": False, "f": None, "n": True, "c": None, "u": True}
        st = status(c["ref"])
        d = c["d"]
        checked = (cache.get(c["ref"]) or {}).get("at")
        it = {"k": c["kind"], "id": c["tmdb"], "s": c["season"], "r": rank, "y": d.get("year"), "t": {"en": d["title"], "de": d["de"]},
              "p": {"en": d.get("poster"), "de": d.get("posterDe")}, "o": st == "owned", "f": st if st in ("1080p", "2160p") else None,
              "c": checked[:10] if checked else None}
        it["n"] = not it["o"] and not it["f"]
        if it["f"] and not it["o"]:
            tok = tokens.get(c["ref"]) or secrets.token_urlsafe(18)
            tokens[c["ref"]] = it["x"] = tok
            allow[tok] = {"kind": c["kind"], "tmdb": c["tmdb"], "season": c["season"], "title": d["title"], "res": it["f"], "ref": c["ref"]}
        return it

    for key, cands in home_cands.items():
        home[key] = [item(c, c["rank"]) for c in cands]
    rows = []
    service_names = dict(services)
    for rid, kind, src in layout:
        if rid.startswith("service-"):
            en = de = service_names[src]
            typ, logo = "service", (lists.get("logos") or {}).get(str(src))
        else:
            en, de, typ, logo = src[1], src[2], "genre", None
        shown = row_cands.get(rid, [])
        rows.append({"id": rid, "kind": kind, "type": typ, "name": {"en": en, "de": de}, "logo": logo, "items": [item(c, i) for i, c in enumerate(shown, 1)]})
    data = {"week": state["top"].get("week"), "lists": lists["at"], "home": home, "rows": rows}
    say(f"home: {len(home['shows'])} shows, {len(home['movies'])} films; rows: " + ", ".join(f"{r['id']} {len(r['items'])}" for r in rows) + f"; searches {SEARCH_MAX - budget[0]}")
    stats["errors"] = len(failed)
    if dry:
        return 0
    if len(failed) > max(FAIL_MIN, int(len(tried) * FAIL_SHARE)):
        say(f"{len(failed)} of {len(tried)} lookups failed: published lists kept")
        save_json(state_file, state)
        return 1
    avail = lambda items: sum(1 for i in items if i["o"] or i["f"])
    stats["lists"] = {"home-shows": (len(home["shows"]), avail(home["shows"])), "home-movies": (len(home["movies"]), avail(home["movies"]))}
    stats["lists"].update({r["id"]: (len(r["items"]), avail(r["items"])) for r in rows})
    stats["searches"] = SEARCH_MAX - budget[0]
    for r in [r for r in list(tokens) if r not in {e["ref"] for e in allow.values()}]:
        del tokens[r]
    source = script(data)
    with io_.injector_lock():
        config = io_.injector_get()
        current = next((s for s in config.get("CustomJavaScripts") or [] if s.get("Name") == NAME), None)
        if not current or current.get("Script") != source or not current.get("Enabled") or not current.get("RequiresAuthentication"):
            io_.injector_put(upsert(config, source))
            say(f"upserted {NAME}")
    save_json(allow_file, {"updated": stamp, "entries": allow}, 0o644)
    save_json(state_file, state)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
