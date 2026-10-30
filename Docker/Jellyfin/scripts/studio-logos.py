#!/usr/bin/env python3
"""Studio and network logos for the home rows Studios / Networks (home-placeholders/empty-sections.js).
Finds each title's TMDB companies and networks through Seerr (its TMDB access, no own key), uploads the white TMDB logo as the Jellyfin studio's Logo image once, and writes the JavaScript Injector entry "Home networks" (window.HssStudios / window.HssNetworks: the cards of MAJOR_STUDIOS / NETWORKS with their Jellyfin studio ids, in precedence order).
Lookups are cached in ~/.cache/jellyfin-studio-logos.json, so a run only asks about new titles. Never prints API keys.
Usage: studio-logos.py [--dry-run]"""
import base64
import io
import json
import pathlib
import re
import sys
import urllib.request

import jellyfin_api

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NAME = "Home networks"
# trending.py placeholders (Trending items, Trending shows) are not library titles.
TRENDING_PATH = "/config/trending/"
PLUGIN = "JavaScript Injector"
SEERR = "http://127.0.0.1:5055"
SEERR_KEY = HERE.parent.parent / "Seerr" / "secrets" / "api-key"
CACHE_FILE = pathlib.Path.home() / ".cache" / "jellyfin-studio-logos.json"
TMDB_LOGO = "https://image.tmdb.org/t/p/w500_filter(duotone,ffffff,bababa)"
# The home rows Studios / Networks show only these cards (owner 2026-10-28: major studios, well-known networks). One card = one entry, aliases grouped.
# Order = precedence, bigger first: a title with several of them counts only for the first (Harry Potter: Warner Bros., not 1492 Pictures; a Pixar film made with Walt Disney Pictures: Pixar; Marvel Studios and Lucasfilm likewise come before Walt Disney).
# Each entry: (card name, {TMDB id checked through Seerr 2026-10-28: its TMDB name}, exact names of aliases without a checked id). The TMDB name only counts for a title whose lookup failed (e.g. the Harry Potter Collection item, TMDB collection 1241). An id from themoviedb.org/search/company (or /network), then rerun this script.
MAJOR_STUDIOS = [
    ("Warner Bros.", {174: "Warner Bros. Pictures", 17: "Warner Bros. Entertainment", 2785: "Warner Bros. Animation"}, []),
    ("Pixar", {3: "Pixar"}, []),
    ("Marvel Studios", {420: "Marvel Studios"}, []),
    ("Lucasfilm", {1: "Lucasfilm Ltd."}, []),
    ("Walt Disney", {2: "Walt Disney Pictures", 6125: "Walt Disney Animation Studios", 3166: "Walt Disney Productions"}, []),
    ("Universal", {33: "Universal Pictures"}, []),
    ("Paramount", {4: "Paramount Pictures", 24955: "Paramount Animation"}, []),
    ("Sony Pictures", {5: "Columbia Pictures", 34: "Sony Pictures", 2251: "Sony Pictures Animation", 559: "TriStar Pictures", 3287: "Screen Gems"}, []),
    ("20th Century", {25: "20th Century Fox", 127928: "20th Century Studios", 711: "Fox 2000 Pictures"}, []),
    ("Netflix", {178464: "Netflix"}, []),
    ("Amazon MGM", {210099: "Amazon MGM Studios", 20580: "Amazon Studios", 21: "Metro-Goldwyn-Mayer", 60: "United Artists"}, []),
    ("Apple", {194232: "Apple Studios"}, ["Apple Original Films"]),
    ("Lionsgate", {1632: "Lionsgate", 491: "Summit Entertainment"}, []),
    ("DreamWorks", {521: "DreamWorks Animation", 7: "DreamWorks Pictures"}, []),
    ("New Line Cinema", {12: "New Line Cinema"}, []),
    ("Legendary", {923: "Legendary Pictures"}, []),
    ("Illumination", {6704: "Illumination"}, []),
    ("Focus Features", {10146: "Focus Features"}, []),
    ("Searchlight", {127929: "Searchlight Pictures", 43: "Fox Searchlight Pictures"}, []),
    ("A24", {41077: "A24"}, []),
    ("Neon", {90733: "NEON"}, []),
    ("Miramax", {14: "Miramax"}, []),
    ("Blumhouse", {3172: "Blumhouse Productions"}, []),
    ("Annapurna", {13184: "Annapurna Pictures"}, []),
    ("Amblin", {56: "Amblin Entertainment"}, []),
    ("Studio Ghibli", {10342: "Studio Ghibli"}, []),
    ("Toho", {882: "TOHO"}, []),
    ("Toei Animation", {5542: "Toei Animation"}, []),
    ("StudioCanal", {694: "StudioCanal"}, []),
    ("Pathé", {7981: "Pathé"}, []),
    ("Gaumont", {9: "Gaumont"}, []),
    ("Constantin Film", {47: "Constantin Film"}, []),
    ("Working Title", {10163: "Working Title"}, []),
    ("BBC Film", {288: "BBC Film"}, []),
    ("Film4", {6705: "Film4 Productions"}, []),
    ("Aardman", {297: "Aardman"}, []),
    ("Laika", {11537: "LAIKA"}, []),
]
# Big studios and household brands always get their card; the others only with at least 2 titles the user can see (owner 2026-10-29: trim the row).
MAJOR_TIER = ["Warner Bros.", "Pixar", "Marvel Studios", "Lucasfilm", "Walt Disney", "Universal", "Paramount", "Sony Pictures", "20th Century",
              "Netflix", "Amazon MGM", "Apple", "Lionsgate", "DreamWorks", "Illumination", "Studio Ghibli"]
SMALL_MIN = 2
# Global streamers, major US/UK/DE/CH broadcasters, Crunchyroll; matched by id, so a shared name like "RTL" (HU) or "max" (AU) never counts.
NETWORKS = [
    ("Netflix", {213: "Netflix"}, []), ("Prime Video", {1024: "Prime Video"}, []), ("Disney+", {2739: "Disney+"}, []), ("HBO", {49: "HBO"}, []), ("HBO Max", {3186: "HBO Max", 8304: "HBO Max", 7869: "HBO Max", 6783: "HBO Max"}, []),
    ("Apple TV+", {2552: "Apple TV+"}, []), ("Hulu", {453: "Hulu"}, []), ("Paramount+", {4330: "Paramount+"}, []), ("Peacock", {3353: "Peacock"}, []), ("Crunchyroll", {1112: "Crunchyroll"}, []),
    ("AMC", {174: "AMC"}, []), ("AMC+", {4661: "AMC+"}, []), ("FX", {88: "FX"}, []), ("Showtime", {67: "Showtime"}, []), ("Starz", {318: "Starz"}, []), ("Cinemax", {359: "Cinemax"}, []),
    ("NBC", {6: "NBC"}, []), ("CBS", {16: "CBS"}, []), ("ABC", {2: "ABC"}, []), ("FOX", {19: "FOX"}, []), ("The CW", {71: "The CW"}, []), ("TNT", {41: "TNT"}, []), ("USA Network", {30: "USA Network"}, []),
    ("Syfy", {77: "Syfy"}, []), ("Comedy Central", {47: "Comedy Central"}, []), ("Adult Swim", {80: "Adult Swim"}, []), ("Cartoon Network", {56: "Cartoon Network"}, []), ("Nickelodeon", {13: "Nickelodeon"}, []),
    ("Disney Channel", {54: "Disney Channel"}, []), ("Discovery", {64: "Discovery"}, []), ("discovery+", {4353: "discovery+"}, []), ("National Geographic", {43: "National Geographic"}, []),
    ("BBC One", {4: "BBC One"}, []), ("BBC Two", {332: "BBC Two"}, []), ("BBC Three", {3: "BBC Three"}, []), ("BBC iPlayer", {1155: "BBC iPlayer"}, []), ("ITV1", {9: "ITV1"}, []), ("Channel 4", {26: "Channel 4"}, []),
    ("Channel 5", {99: "Channel 5"}, []), ("E4", {136: "E4"}, []), ("Sky Atlantic", {1063: "Sky Atlantic"}, []), ("Sky One", {214: "Sky One"}, []),
    ("ZDF", {31: "ZDF"}, []), ("Das Erste", {308: "Das Erste"}, []), ("ARTE", {1628: "ARTE"}, []), ("ProSieben", {334: "ProSieben"}, []), ("SAT.1", {163: "SAT.1"}, []), ("RTL", {207: "RTL"}, []), ("RTL+", {5428: "RTL+"}, []),
    ("SRF 1", {571: "SRF 1"}, []), ("SRF zwei", {2563: "SRF zwei"}, []),
]


def norm(name):
    return re.sub(r"[^0-9a-z+]", "", (name or "").casefold())


def tmdb_ref(item):
    tmdb = (item.get("ProviderIds") or {}).get("Tmdb")
    if not tmdb:
        return None
    return ("tv" if item.get("Type") == "Series" else "movie") + ":" + str(tmdb)


def details(raw):
    def pick(key):
        return [{"id": c.get("id"), "name": c.get("name"), "logo": c.get("logoPath")} for c in raw.get(key) or []]
    return {"companies": pick("productionCompanies"), "networks": pick("networks")}


def card_groups(table, items, cache, key):
    """[{name, ids: Jellyfin studio ids}] in the table's order, only cards with a studio in these items; key = "companies" (films) or "networks" (series)."""
    tmdb = {}
    for item in items:
        d = cache.get(tmdb_ref(item) or "")
        by_name = {norm(c["name"]): c.get("id") for c in d[key]} if d else {}
        for st in item.get("Studios") or []:
            n = norm(st["Name"])
            entry = tmdb.setdefault(st["Id"], [set(), n, False])
            entry[2] = entry[2] or bool(d)
            if n in by_name:
                entry[0].add(by_name[n])
    out, taken = [], set()
    for name, ids, aliases in table:
        alias = {norm(a) for a in aliases}
        tmdb_names = {norm(n) for n in ids.values()}
        hit = sorted(sid for sid, (got, n, known) in tmdb.items() if sid not in taken and (got & set(ids) or n in alias or (not known and n in tmdb_names)))
        if hit:
            taken |= set(hit)
            out.append({"name": name, "ids": hit})
    return out


def with_min(cards):
    """Studio cards outside MAJOR_TIER need SMALL_MIN titles (counted per user in the browser, empty-sections.js assignCards)."""
    return [c if c["name"] in MAJOR_TIER else {**c, "min": SMALL_MIN} for c in cards]


def logo_jobs(studios, items, cache, shown):
    """[(studio id, name, TMDB logo path)] for the studios on a card (shown) that have no Logo image yet."""
    found = {}
    opaque = set(cache.get("opaque") or [])
    for item in items:
        d = cache.get(tmdb_ref(item) or "")
        if not d:
            continue
        pool = d["networks"] if item.get("Type") == "Series" else d["companies"]
        logos = {norm(c["name"]): c["logo"] for c in pool if c.get("logo") and c["logo"] not in opaque}
        for st in item.get("Studios") or []:
            if st["Id"] not in shown:
                continue
            path = logos.get(norm(st["Name"]))
            if path:
                found.setdefault(st["Id"], path)
    jobs = [(s["Id"], s["Name"], found[s["Id"]]) for s in studios if s["Id"] in found and not (s.get("ImageTags") or {}).get("Logo")]
    return sorted(jobs, key=lambda j: j[1].casefold())


def see_through(data):
    """True when the logo has a transparent background; an opaque one would be a white box on the dark card."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if "A" not in img.getbands():
            return False
        alpha = img.getchannel("A")
        clear = sum(alpha.histogram()[:16])
        return clear >= 0.2 * img.width * img.height
    except Exception:
        return False


def logo_url(path):
    return TMDB_LOGO + path


def cards_script(studios, networks):
    return "window.HssStudios = " + json.dumps(studios, ensure_ascii=False) + ";\nwindow.HssNetworks = " + json.dumps(networks, ensure_ascii=False) + ";\n"


def _seerr(ref):
    kind, tmdb = ref.split(":")
    req = urllib.request.Request(f"{SEERR}/api/v1/{kind}/{tmdb}")
    req.add_header("X-Api-Key", SEERR_KEY.read_text().strip())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _fetch(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        if not resp.headers.get("Content-Type", "").startswith("image/"):
            raise OSError("not an image")
        return resp.read()


def _upload(studio_id, data):
    req = urllib.request.Request(f"{jellyfin_api.BASE}/Items/{studio_id}/Images/Logo", data=base64.b64encode(data), method="POST")
    req.add_header("Authorization", f'MediaBrowser Token="{jellyfin_api.KEY_FILE.read_text().strip()}"')
    req.add_header("Content-Type", "image/png")
    with urllib.request.urlopen(req, timeout=30):
        pass


def upsert(config, script):
    entry = {"Name": NAME, "Script": script, "Enabled": True, "RequiresAuthentication": True}
    scripts = [s for s in config.get("CustomJavaScripts") or [] if s.get("Name") != NAME]
    return {**config, "CustomJavaScripts": scripts + [entry]}


def main(argv, api=None, seerr=_seerr, fetch=_fetch, upload=_upload, cache_file=CACHE_FILE, out=sys.stdout, see_through=see_through):
    if any(a != "--dry-run" for a in argv):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    dry = "--dry-run" in argv
    if api is None:
        api = jellyfin_api.api
    fields = "Recursive=true&CollapseBoxSetItems=false&Fields=Studios,ProviderIds,Path&EnableImages=false&EnableUserData=false"
    movies, series = ([i for i in api("GET", f"/Items?IncludeItemTypes={kind}&{fields}")["Items"] if not (i.get("Path") or "").startswith(TRENDING_PATH)] for kind in ("Movie", "Series"))
    studios = api("GET", "/Studios?Recursive=true&EnableImageTypes=Logo&ImageTypeLimit=1")["Items"]
    try:
        cache = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        cache = {}
    for item in movies + series:
        ref = tmdb_ref(item)
        if not ref or ref in cache:
            continue
        try:
            cache[ref] = details(seerr(ref))
        except Exception as e:
            print(f"skipped {ref}: {type(e).__name__}", file=out)
    majors = card_groups(MAJOR_STUDIOS, movies, cache, "companies")
    nets = card_groups(NETWORKS, series, cache, "networks")
    shown = {i for g in majors + nets for i in g["ids"]}
    jobs = logo_jobs(studios, movies + series, cache, shown)
    plugin_id = next(p["Id"] for p in api("GET", "/Plugins") if p["Name"] == PLUGIN)
    path = f"/Plugins/{plugin_id}/Configuration"
    script = cards_script(with_min(majors), nets)
    changed = next((s.get("Script") for s in api("GET", path).get("CustomJavaScripts") or [] if s.get("Name") == NAME), None) != script
    print(f"{len(majors)} studios, {len(nets)} networks", file=out)
    if dry:
        print(f"{len(jobs)} logos to add, cards {'would change' if changed else 'unchanged'}", file=out)
        return 0
    added = 0
    for sid, name, logo in jobs:
        try:
            data = fetch(logo_url(logo))
            if not see_through(data):
                cache["opaque"] = sorted(set(cache.get("opaque") or []) | {logo})
                print(f"logo skipped (no transparent background): {name}", file=out)
                continue
            upload(sid, data)
            added += 1
            print(f"logo: {name}", file=out)
        except Exception as e:
            print(f"logo failed: {name}: {type(e).__name__}", file=out)
    if changed:
        with jellyfin_api.injector_lock():
            api("POST", path, upsert(api("GET", path), script))
    cache.setdefault("opaque", [])
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache, sort_keys=True))
    print(f"{added} logos added, cards {'changed' if changed else 'unchanged'}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
