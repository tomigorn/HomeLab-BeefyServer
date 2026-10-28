#!/usr/bin/env python3
"""Studio and network logos for the home rows Studios / Networks (home-placeholders/empty-sections.js).
Finds each title's TMDB companies and networks through Seerr (its TMDB access, no own key), uploads the white TMDB logo as the Jellyfin studio's Logo image once, and writes the JavaScript Injector entry "Home networks" (window.HssNetworks: the studio ids that are well-known TMDB networks, WELL_KNOWN).
Lookups are cached in ~/.cache/jellyfin-studio-logos.json, so a run only asks about new titles. Never prints API keys.
Usage: studio-logos.py [--dry-run]"""
import base64
import io
import json
import pathlib
import re
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NAME = "Home networks"
PLUGIN = "JavaScript Injector"
SEERR = "http://127.0.0.1:5055"
SEERR_KEY = HERE.parent.parent / "Seerr" / "secrets" / "api-key"
CACHE_FILE = pathlib.Path.home() / ".cache" / "jellyfin-studio-logos.json"
TMDB_LOGO = "https://image.tmdb.org/t/p/w500_filter(duotone,ffffff,bababa)"
# The only networks the Networks row shows (owner 2026-10-28: well-known ones): global streamers, major US/UK/DE/CH broadcasters, Crunchyroll.
# Matched by TMDB network id (ids checked through Seerr 2026-10-28), the name only for a network without an id; a shared name like "RTL" (HU) or "max" (AU) never matches.
# Add one: find the id on themoviedb.org/search/network, then rerun studio-logos.py.
WELL_KNOWN = {
    213: "Netflix", 1024: "Prime Video", 2739: "Disney+", 2552: "Apple TV+", 453: "Hulu", 4330: "Paramount+", 3353: "Peacock",
    6783: "Max", 3186: "HBO Max", 8304: "HBO Max", 7869: "HBO Max", 1112: "Crunchyroll", 4353: "discovery+", 4661: "AMC+",
    49: "HBO", 6: "NBC", 16: "CBS", 2: "ABC", 19: "FOX", 71: "The CW", 174: "AMC", 88: "FX", 67: "Showtime", 318: "Starz",
    359: "Cinemax", 41: "TNT", 30: "USA Network", 77: "Syfy", 47: "Comedy Central", 80: "Adult Swim", 56: "Cartoon Network",
    13: "Nickelodeon", 54: "Disney Channel", 64: "Discovery", 43: "National Geographic",
    4: "BBC One", 332: "BBC Two", 3: "BBC Three", 1155: "BBC iPlayer", 9: "ITV1", 26: "Channel 4", 99: "Channel 5", 136: "E4",
    214: "Sky One", 1063: "Sky Atlantic",
    31: "ZDF", 308: "Das Erste", 334: "ProSieben", 163: "SAT.1", 207: "RTL", 5428: "RTL+", 1628: "ARTE",
    571: "SRF 1", 2563: "SRF zwei",
}


def norm(name):
    return re.sub(r"[^0-9a-z+]", "", (name or "").casefold())


def well_known(network):
    if network.get("id") is not None:
        return network["id"] in WELL_KNOWN
    return norm(network.get("name")) in {norm(n) for n in WELL_KNOWN.values()}


def tmdb_ref(item):
    tmdb = (item.get("ProviderIds") or {}).get("Tmdb")
    if not tmdb:
        return None
    return ("tv" if item.get("Type") == "Series" else "movie") + ":" + str(tmdb)


def details(raw):
    def pick(key):
        return [{"id": c.get("id"), "name": c.get("name"), "logo": c.get("logoPath")} for c in raw.get(key) or []]
    return {"companies": pick("productionCompanies"), "networks": pick("networks")}


def network_ids(series, cache):
    out = set()
    for s in series:
        d = cache.get(tmdb_ref(s) or "")
        if not d:
            continue
        names = {norm(n["name"]) for n in d["networks"] if well_known(n)}
        out |= {st["Id"] for st in s.get("Studios") or [] if norm(st["Name"]) in names}
    return sorted(out)


def logo_jobs(studios, items, cache, networks):
    """[(studio id, name, TMDB logo path)] for shown studios (film studios, networks) that have no Logo image yet."""
    found = {}
    opaque = set(cache.get("opaque") or [])
    for item in items:
        d = cache.get(tmdb_ref(item) or "")
        if not d:
            continue
        pool = d["networks"] if item.get("Type") == "Series" else d["companies"]
        logos = {norm(c["name"]): c["logo"] for c in pool if c.get("logo") and c["logo"] not in opaque}
        for st in item.get("Studios") or []:
            if item.get("Type") == "Series" and st["Id"] not in networks:
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


def networks_script(ids):
    return "window.HssNetworks = " + json.dumps(sorted(ids)) + ";\n"


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
    import jellyfin_api
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
        import jellyfin_api
        api = jellyfin_api.api
    fields = "Recursive=true&Fields=Studios,ProviderIds&EnableImages=false&EnableUserData=false"
    movies = api("GET", f"/Items?IncludeItemTypes=Movie&{fields}")["Items"]
    series = api("GET", f"/Items?IncludeItemTypes=Series&{fields}")["Items"]
    studios = api("GET", "/Studios?Recursive=true&EnableImageTypes=Logo&ImageTypeLimit=1")["Items"]
    try:
        cache = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        cache = {}
    has_logo = {s["Id"] for s in studios if (s.get("ImageTags") or {}).get("Logo")}
    wanted = series + [m for m in movies if any(st["Id"] not in has_logo for st in m.get("Studios") or [])]
    for item in wanted:
        ref = tmdb_ref(item)
        if not ref or ref in cache:
            continue
        try:
            cache[ref] = details(seerr(ref))
        except Exception as e:
            print(f"skipped {ref}: {type(e).__name__}", file=out)
    nets = network_ids(series, cache)
    jobs = logo_jobs(studios, movies + series, cache, nets)
    plugin_id = next(p["Id"] for p in api("GET", "/Plugins") if p["Name"] == PLUGIN)
    path = f"/Plugins/{plugin_id}/Configuration"
    config = api("GET", path)
    script = networks_script(nets)
    changed = next((s.get("Script") for s in config.get("CustomJavaScripts") or [] if s.get("Name") == NAME), None) != script
    print(f"{len(nets)} networks", file=out)
    if dry:
        print(f"{len(jobs)} logos to add, networks {'would change' if changed else 'unchanged'}", file=out)
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
        api("POST", path, upsert(config, script))
    cache.setdefault("opaque", [])
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache, sort_keys=True))
    print(f"{added} logos added, networks {'changed' if changed else 'unchanged'}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
