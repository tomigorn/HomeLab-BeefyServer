#!/usr/bin/env python3
"""Region rows: one collection per region (Nordic, Spanish-language, French, Swiss & German-language, then the biggest of the others), shown as home rows on the web (injector entry "Home regions", home-placeholders/region-rows.js) and in Moonfin (moonbase.py puts them after Hidden Gems).
Region = TMDB original language first (through Seerr, cached in ~/.cache/jellyfin-region-rows.json), production country second (Swiss co-productions, Latin American films, British titles, and Jellyfin's ProductionLocations when TMDB has no details).
A row needs MIN titles, at most MAX rows (region rows before region + genre rows), a row mostly inside a row already shown is left out. Members newest added first (Swiss-made first in the Swiss row), at most SIZE. Only BoxSets tagged hss-region are touched.
Usage: region-rows.py [--dry-run | --counts | --check]  (--counts: every candidate row with its titles, changes nothing; --check: read only, exits 1 when the sets, the web entry or Moonbase disagree)"""
import base64
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import collection_order  # noqa: E402
import jellyfin_api  # noqa: E402

TAG = "hss-region"
SIZE = 20
# A row of 4 fills a phone screen and most of a TV row; with fewer it reads as broken.
MIN = 4
# 6 region rows: one group between Hidden Gems and the genre rows, the home stays about 22 rows.
MAX = 6
# A row whose titles are this much inside one row already shown would repeat it.
OVERLAP = 0.75
WEB_ENTRY = "Home regions"
WEB_SCRIPT = HERE.parent / "home-placeholders" / "region-rows.js"
CACHE = pathlib.Path.home() / ".cache" / "jellyfin-region-rows.json"
STATE = pathlib.Path.home() / ".local" / "state" / "jellyfin-region-rows.json"
SEERR = "http://127.0.0.1:5055"
SEERR_KEY = HERE.parent.parent / "Seerr" / "secrets" / "api-key"
TRENDING_PATH = "/config/trending/"

# key: (English name = collection and Moonfin row, German name on the web). Order = priority on a tie.
ROWS = {
    "nordic": ("Nordic Films & Series", "Nordische Filme & Serien"),
    "spanish": ("Spanish-Language Films & Series", "Spanischsprachige Filme & Serien"),
    "french": ("French Films & Series", "Französische Filme & Serien"),
    "swiss": ("Swiss & German-Language", "Schweizer & Deutschsprachiges"),
    "korean": ("Korean Films & K-Dramas", "Koreanische Filme & K-Dramen"),
    "japanese": ("Japanese Films & Series", "Japanische Filme & Serien"),
    "anime": ("Anime", "Anime"),
    "british": ("British Films & Series", "Britische Filme & Serien"),
    "italian": ("Italian Films & Series", "Italienische Filme & Serien"),
    "latin": ("Latin American Films & Series", "Lateinamerikanische Filme & Serien"),
    "indian": ("Indian Films & Series", "Indische Filme & Serien"),
    "chinese": ("Chinese-Language Films & Series", "Chinesischsprachige Filme & Serien"),
    "east-european": ("Eastern European Films & Series", "Osteuropäische Filme & Serien"),
    "nordic-noir": ("Nordic Noir", "Nordic Noir"),
    "world-thrillers": ("International Thrillers", "Internationale Thriller"),
    "asian-action": ("Asian Action Films", "Asiatische Actionfilme"),
}
# Owner 2026-11-22: these four always when big enough, in this order.
OWNER = ("nordic", "spanish", "french", "swiss")
# Region + genre rows: only when a slot is left after the region rows.
CUTS = ("nordic-noir", "world-thrillers", "asian-action")

NORDIC = {"da", "sv", "no", "nb", "nn", "fi", "is", "fo"}
SWISS_LANGS = {"de", "fr", "it", "rm", "gsw"}
INDIAN = {"hi", "ta", "te", "ml", "kn", "bn", "mr", "pa", "gu"}
CHINESE = {"zh", "cn", "yue"}
EAST_EUROPEAN = {"pl", "cs", "sk", "hu", "ro", "bg", "ru", "uk", "sr", "hr", "sl", "bs", "lt", "lv", "et"}
ASIAN = {"ja", "ko", "th", "id", "vi", "tl", "ms"} | CHINESE | INDIAN
LATAM = {"AR", "BO", "BR", "CL", "CO", "CR", "CU", "DO", "EC", "GT", "HN", "MX", "NI", "PA", "PE", "PR", "PY", "SV", "UY", "VE"}
CRIME = {"Crime", "Thriller", "Mystery"}
# A single production country stands in for a missing original language.
COUNTRY_LANG = {"DK": "da", "SE": "sv", "NO": "no", "FI": "fi", "IS": "is", "ES": "es", "MX": "es", "AR": "es", "FR": "fr", "DE": "de", "AT": "de", "IT": "it",
                "JP": "ja", "KR": "ko", "IN": "hi", "CN": "zh", "HK": "cn", "TW": "zh", "BR": "pt", "PL": "pl", "GB": "en", "US": "en"}
# Jellyfin's ProductionLocations names (TMDB English names) of the countries the rules use.
NAME_ISO = {"Denmark": "DK", "Sweden": "SE", "Norway": "NO", "Finland": "FI", "Iceland": "IS", "Spain": "ES", "Mexico": "MX", "Argentina": "AR", "France": "FR",
            "Germany": "DE", "Austria": "AT", "Switzerland": "CH", "Italy": "IT", "Japan": "JP", "South Korea": "KR", "India": "IN", "China": "CN", "Hong Kong": "HK",
            "Taiwan": "TW", "Brazil": "BR", "Poland": "PL", "United Kingdom": "GB", "United States of America": "US", "Chile": "CL", "Colombia": "CO", "Peru": "PE"}


def slim(raw):
    """What the rules need of Seerr's TMDB details."""
    return {"lang": raw.get("originalLanguage") or None, "countries": [c.get("iso_3166_1") for c in raw.get("productionCountries") or [] if c.get("iso_3166_1")],
            "origin": list(raw.get("originCountry") or [])}


def tmdb_ref(item):
    tmdb = (item.get("ProviderIds") or {}).get("Tmdb")
    return f"{'tv' if item.get('Type') == 'Series' else 'movie'}:{tmdb}" if tmdb else None


def facts(item, entry):
    """Original language, production countries (ISO), origin countries, genres and type of an item; without TMDB details Jellyfin's ProductionLocations."""
    entry = entry or {}
    countries = entry.get("countries") or [NAME_ISO[n] for n in item.get("ProductionLocations") or [] if n in NAME_ISO]
    origin = entry.get("origin") or []
    lang = entry.get("lang")
    if not lang:
        single = origin[0] if origin else countries[0] if len(countries) == 1 else None
        lang = COUNTRY_LANG.get(single)
    return {"lang": lang, "countries": countries, "origin": origin, "genres": set(item.get("Genres") or []), "type": item.get("Type")}


def swiss_made(f):
    return "CH" in f["origin"] or "CH" in f["countries"] and (f["lang"] in SWISS_LANGS or f["countries"] == ["CH"])


def regions(f):
    """Row keys of an item's facts."""
    lang, genres, out = f["lang"], f["genres"], set()
    home = (f["origin"] or f["countries"] or [None])[0]
    if lang in NORDIC:
        out.add("nordic")
        if genres & CRIME:
            out.add("nordic-noir")
    if lang == "es":
        out.add("spanish")
    if lang in ("es", "pt") and home in LATAM:
        out.add("latin")
    if lang == "fr":
        out.add("french")
    if swiss_made(f) or lang == "de":
        out.add("swiss")
    if lang == "ja":
        out.add("anime" if "Animation" in genres else "japanese")
    if lang == "ko":
        out.add("korean")
    if lang == "it":
        out.add("italian")
    if lang in INDIAN:
        out.add("indian")
    if lang in CHINESE:
        out.add("chinese")
    if lang in EAST_EUROPEAN:
        out.add("east-european")
    if lang == "en" and (f["origin"] == ["GB"] or not f["origin"] and f["countries"] and set(f["countries"]) <= {"GB"}):
        out.add("british")
    if lang and lang != "en" and genres & CRIME:
        out.add("world-thrillers")
    if lang in ASIAN and f["type"] == "Movie" and "Action" in genres:
        out.add("asian-action")
    return out


def plan(items, cache):
    """{key: [ids]} of every row with titles: newest added first (Swiss-made first in the Swiss row), at most SIZE."""
    rows = {}
    for i in sorted(items, key=lambda i: i.get("DateCreated") or "", reverse=True):
        if (i.get("Path") or "").startswith(TRENDING_PATH):
            continue
        f = facts(i, cache.get(tmdb_ref(i) or ""))
        for key in regions(f):
            rows.setdefault(key, []).append((i["Id"], key == "swiss" and not swiss_made(f)))
    return {k: [i for i, _ in sorted(v, key=lambda x: x[1])][:SIZE] for k, v in rows.items()}


def rank(key, count):
    """Sort key of a shown row: the owner's rows first in their order, then the biggest region rows, then the biggest region + genre rows; ties by ROWS order."""
    keys = list(ROWS)
    return (0, OWNER.index(key), 0) if key in OWNER else (2 if key in CUTS else 1, -count, keys.index(key) if key in keys else len(keys))


def choose(rows):
    """The keys shown, in order."""
    chosen = []
    for key in sorted((k for k in rows if k in ROWS and len(rows[k]) >= MIN), key=lambda k: rank(k, len(rows[k]))):
        if len(chosen) >= MAX:
            break
        members = set(rows[key])
        if any(len(members & set(rows[c])) >= OVERLAP * len(members) for c in chosen):
            continue
        chosen.append(key)
    return chosen


def row_id(key):
    return "HssRegion" + "".join(p.capitalize() for p in key.split("-"))


def web_source(rows, script):
    """Injector entry: window.HssRegionRows = [{id, name: {en, de}, items}] then region-rows.js."""
    data = [{"id": row_id(k), "name": {"en": ROWS[k][0], "de": ROWS[k][1]}, "items": list(ids)} for k, ids in rows]
    return "window.HssRegionRows = " + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + ";\n" + script


def _seerr(ref):
    kind, tmdb = ref.split(":")
    req = urllib.request.Request(f"{SEERR}/api/v1/{kind}/{tmdb}")
    req.add_header("X-Api-Key", SEERR_KEY.read_text().strip())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def load_details(items, seerr, path=CACHE):
    """{ref: slim details}: the cache plus the titles not in it yet (seerr None: cache only). A failed lookup is logged and asked again next run."""
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        cache = {}
    if seerr is None:
        return cache
    new = failed = 0
    for i in items:
        ref = tmdb_ref(i)
        if not ref or ref in cache or (i.get("Path") or "").startswith(TRENDING_PATH):
            continue
        try:
            cache[ref] = slim(seerr(ref))
            new += 1
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            failed += 1
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(cache, sort_keys=True))
        os.replace(tmp, path)
    if new or failed:
        print(f"TMDB details: {new} new, {failed} failed")
    return cache


def _module(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_set(boxes, key):
    return next((b for b in boxes if f"{TAG}-{key}" in (b.get("Tags") or [])), None)


def sync_set(api, key, want, boxes, admin, dry):
    """Creates or updates the collection of a row, members in want's order; returns its id (None in a dry run when new)."""
    name = ROWS[key][0]
    box = find_set(boxes, key)
    if box is None:
        if dry:
            print(f"would create {name} with {len(want)} titles")
            return None
        box = api("POST", f"/Collections?name={urllib.parse.quote(name)}&ids={','.join(want)}&isLocked=true")
        print(f"created {name} with {len(want)} titles")
    sid = box["Id"]
    item = api("GET", f"/Items/{sid}?userId={admin}")
    tags = [TAG, f"{TAG}-{key}"]
    sort = f"93 {name}"
    if set(item.get("Tags") or []) != set(tags) or item.get("Name") != name or item.get("DisplayOrder") != "Default" or item.get("LockData") is not True or item.get("ForcedSortName") != sort:
        if not dry:
            api("POST", f"/Items/{sid}", {**item, "Name": name, "Tags": tags, "DisplayOrder": "Default", "LockData": True, "ForcedSortName": sort})
    have = [i["Id"] for i in api("GET", f"/Items?ParentId={sid}&userId={admin}&EnableImages=false&EnableUserData=false")["Items"]]
    todo = _module("hidden_gems", "hidden-gems.py").steps(have, want)
    if todo and not dry:
        for method, ids in todo:
            api(method, f"/Collections/{sid}/Items?ids={','.join(ids)}")
    if todo:
        print(f"{name}: {'would change' if dry else 'changed'} ({len(want)} titles, {len(todo)} steps)")
    return sid


def drop_sets(api, boxes, keep, dry):
    """Deletes the region sets of rows no longer shown (only BoxSets tagged hss-region); returns their ids."""
    gone = [b["Id"] for b in boxes if TAG in (b.get("Tags") or []) and not any(f"{TAG}-{k}" in b["Tags"] for k in keep)]
    for sid in gone:
        if not dry:
            api("DELETE", f"/Items/{sid}")
    return gone


def artwork(raw, sid, key, want, dry, state):
    """Poster (region colour, globe, first 3 posters fanned) and backdrop (first title's) of a row's set, uploaded when they change. Returns 1 when uploaded."""
    import trending_art as art
    gems = _module("hidden_gems", "hidden-gems.py")
    originals = gems.ORIGINALS
    posters = [p for p in (gems.image(raw, i, "Primary", originals) for i in want[:3]) if p]
    back = next((b for b in (gems.image(raw, i, "Backdrop/0", originals) for i in want[:3]) if b), None)
    name = ROWS[key][0]
    poster = art.collection_poster(f"region-{key}", name, None, posters)
    backdrop = art.collection_backdrop(f"region-{key}", name, None, back, posters)
    digest = hashlib.sha256(poster + backdrop).hexdigest()
    if state.get(sid) == digest:
        return 0
    if dry:
        return 1
    raw("POST", f"/Items/{sid}/Images/Primary", base64.b64encode(poster), "image/jpeg")
    raw("POST", f"/Items/{sid}/Images/Backdrop/0", base64.b64encode(backdrop), "image/jpeg")
    state[sid] = digest
    return 1


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(f".{STATE.name}.tmp")
    tmp.write_text(json.dumps(state, sort_keys=True))
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE)


def library(api, admin):
    return api("GET", f"/Items?userId={admin}&Recursive=true&IncludeItemTypes=Movie,Series&CollapseBoxSetItems=false"
                      "&Fields=ProductionLocations,Genres,ProviderIds,Path,DateCreated&EnableImages=false&EnableUserData=false")["Items"]


def region_sets(api):
    return [b for b in api("GET", "/Items?Recursive=true&IncludeItemTypes=BoxSet&Fields=Tags&EnableImages=false&EnableUserData=false")["Items"] if TAG in (b.get("Tags") or [])]


def check(api, admin):
    """Problems of the shown rows: sizes, order mode, the web entry and Moonbase's default layout agree with the sets."""
    sets = region_sets(api)
    problems = []
    if len(sets) > MAX:
        problems.append(f"{len(sets)} sets > {MAX}")
    keys = {}
    for b in sets:
        key = next((t[len(TAG) + 1:] for t in b["Tags"] if t.startswith(TAG + "-") and t[len(TAG) + 1:] in ROWS), None)
        item = api("GET", f"/Items/{b['Id']}?userId={admin}")
        n = api("GET", f"/Items?ParentId={b['Id']}&userId={admin}&Limit=0&EnableImages=false")["TotalRecordCount"]
        if key is None or item.get("DisplayOrder") != "Default" or n < MIN:
            problems.append(f"{b.get('Name')}: key {key}, {item.get('DisplayOrder')}, {n} titles")
        keys[b["Id"]] = key
    injector = next((p["Id"] for p in api("GET", "/Plugins") or [] if p.get("Name") == "JavaScript Injector"), None)
    script = next((s.get("Script") or "" for s in (api("GET", f"/Plugins/{injector}/Configuration") if injector else {}).get("CustomJavaScripts") or [] if s.get("Name") == WEB_ENTRY), None)
    web = [] if script is None else [r["id"] for r in json.loads(script.split("window.HssRegionRows = ", 1)[1].split(";\n", 1)[0])]
    if sorted(web) != sorted(row_id(k) for k in keys.values() if k):
        problems.append(f"web entry rows {web} differ from the sets")
    moon = api("GET", "/Plugins/8c5d0e914f2a4b6d9e3f1a7c8d9e0f2b/Configuration")
    shown = {s.get("pluginAdditionalData") for s in (moon.get("DefaultUserSettings") or {}).get("homeSections") or []}
    missing = [b.get("Name") for b in sets if b["Id"] not in shown]
    if missing:
        problems.append(f"not in the Moonfin default layout: {missing}")
    return len(sets), problems


def main(argv, api=None, raw=None, seerr=_seerr):
    flags = {"--dry-run", "--counts", "--check"}
    if any(a not in flags for a in argv) or len(argv) > 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    api = api or jellyfin_api.api
    raw = raw or jellyfin_api.api_raw
    dry = "--dry-run" in argv
    users = api("GET", "/Users")
    admin = next(u["Id"] for u in users if u["Policy"]["IsAdministrator"])
    if "--check" in argv:
        n, problems = check(api, admin)
        for p in problems:
            print(p)
        print(f"ok {n} rows" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    items = library(api, admin)
    cache = load_details(items, None if "--counts" in argv else seerr)
    rows = plan(items, cache)
    keys = choose(rows)
    if "--counts" in argv:
        names = {i["Id"]: i["Name"] for i in items}
        for key in sorted(rows, key=lambda k: rank(k, len(rows[k]))):
            mark = "shown" if key in keys else "too small" if len(rows[key]) < MIN else "not shown"
            print(f"{key:<16} {len(rows[key]):>3}  {mark:<9}  {ROWS[key][0]}: {', '.join(names[i] for i in rows[key][:6])}")
        return 0
    boxes = region_sets(api)
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    sets, arts = {}, 0
    for key in keys:
        sid = sync_set(api, key, rows[key], boxes, admin, dry)
        if sid is None:
            continue
        sets[sid] = rows[key]
        try:
            arts += artwork(raw, sid, key, rows[key], dry, state)
        except (OSError, ValueError, urllib.error.URLError) as e:
            print(f"{ROWS[key][0]}: art failed: {type(e).__name__}")
    gone = drop_sets(api, boxes, set(keys), dry)
    try:
        series = {i["Id"] for i in items if i.get("Type") == "Series"}
        collection_order.sync("regions", collection_order.active_ids(users), collection_order.films_only(sets, series), dry, print)
    except (subprocess.CalledProcessError, OSError, ValueError) as e:
        print(f"Moonfin order failed: {type(e).__name__}")
    if not dry:
        state = {k: v for k, v in state.items() if k in sets}
        save_state(state)
        _module("tracker_link", "tracker-link.py").main([], api=api, source=web_source([(k, rows[k]) for k in keys], WEB_SCRIPT.read_text()), name=WEB_ENTRY, source_file=WEB_SCRIPT)
    verb = "would show" if dry else "shown"
    print(f"{verb}: {', '.join(f'{k} {len(rows[k])}' for k in keys) or 'none'}; too small: {', '.join(f'{k} {len(v)}' for k, v in rows.items() if len(v) < MIN) or 'none'}; "
          f"sets {'to delete' if dry else 'deleted'}: {len(gone)}; art {arts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
