#!/usr/bin/env python3
"""Beta only: the "Not downloaded yet" band in each poster's complement colour (band_colour.py), on every platform.
TV / apps / Moonfin: re-renders the band of the beta's placeholder images (trending/{library,shows}/*/poster.jpg, landscape.jpg) from kept originals, then refreshes the two trending libraries.
Web: writes the injector entry "Band colours (beta)" (band-web.js + window.HssBandColours for every poster path of prod's published lists, read only).
BAND_STYLE=classic puts the original images back and removes the entry. Run as root (the images belong to the container uid).
Usage: band_render.py [--check] [--style complement|classic] [--sample DIR N]  (--sample writes N before/after pairs to DIR, changes nothing)"""
import hashlib
import json
import os
import pathlib
import shutil
import sys
import urllib.request

import band_colour as bc
from beta_api import api

HERE = pathlib.Path(__file__).resolve().parent
BETA = pathlib.Path("/srv/appdata/jellyfin-beta")
TRENDING = BETA / "trending"
STORE = BETA / "band"
LISTS = pathlib.Path("/srv/appdata/jellyfin-popular/lists.json")
UID = 65132
POSTER_BAND, THUMB_BAND = 128, 150
WEB_BAND = 0.2133  # .hssTopBand height / poster width in popular.js, as a share of a 2:3 poster's height: 0.2133 * 2/3
ENTRY = "Band colours (beta)"
LIBRARIES = ("Trending items", "Trending shows")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_state():
    try:
        return json.loads((STORE / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def placeholder_images():
    for sub in ("library", "shows"):
        for f in sorted((TRENDING / sub).glob("*/*.jpg")):
            if f.name in ("poster.jpg", "landscape.jpg"):
                yield f


def band_h(path):
    return THUMB_BAND if path.name == "landscape.jpg" else POSTER_BAND


def write(path, data):
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.chown(tmp, UID, UID)
    tmp.chmod(0o644)
    tmp.replace(path)


def images(style, check):
    """Number of images changed (or to change with check)."""
    state = load_state()
    changed = 0
    for f in placeholder_images():
        rel = str(f.relative_to(TRENDING))
        orig = STORE / "originals" / rel
        cur = f.read_bytes()
        if state.get(rel) != sha(cur):
            # Not ours: prod's (a refresh from prod, a new placeholder): that is the new original.
            if not check:
                orig.parent.mkdir(parents=True, exist_ok=True)
                orig.write_bytes(cur)
            src = cur
        else:
            src = orig.read_bytes() if orig.is_file() else cur
        want = src if style == "classic" else bc.render(src, band_h(f))
        if want == cur and (style == "classic" or state.get(rel) == sha(cur)):
            continue
        changed += 1
        if not check:
            write(f, want)
            state[rel] = sha(want) if style != "classic" else None
    if not check:
        STORE.mkdir(parents=True, exist_ok=True)
        (STORE / "state.json").write_text(json.dumps({k: v for k, v in state.items() if v}, indent=0))
    return changed


def poster_paths():
    try:
        d = json.loads(LISTS.read_text())
    except (OSError, ValueError):
        return []
    rows = list(d.get("rows") or []) + list(d.get("user") or [])
    items = [i for r in rows for i in r.get("items") or []] + [i for v in (d.get("home") or {}).values() if isinstance(v, list) for i in v]
    return sorted({p for i in items if not i.get("o") for p in (i.get("p") or {}).values() if isinstance(p, str) and p.startswith("/")})


def web_colours(paths):
    cache = STORE / "web.json"
    try:
        known = json.loads(cache.read_text())
    except (OSError, ValueError):
        known = {}
    out = {}
    for p in paths:
        key = f"{bc.VERSION}:{p}"
        if key not in known:
            try:
                req = urllib.request.Request("https://image.tmdb.org/t/p/w154" + p, headers={"User-Agent": "homelab-jellyfin-beta/1.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    im = bc.Image.open(bc.io.BytesIO(r.read())).convert("RGB")
            except (OSError, ValueError):
                continue
            bg, fg = bc.colours(im, round(im.height * WEB_BAND))
            known[key] = [bc.hex_(bg), bc.hex_(fg)]
        out[p] = known[key]
    STORE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({k: v for k, v in known.items() if k.startswith(f"{bc.VERSION}:")}))
    return out


def injector(style, check):
    import importlib.util
    spec = importlib.util.spec_from_file_location("tracker_link", HERE.parent.parent / "Jellyfin" / "scripts" / "tracker-link.py")
    tl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tl)
    if check:
        return 0
    if style == "classic":
        source = "window.HssBandColours = {};\n"
    else:
        source = "window.HssBandColours = " + json.dumps(web_colours(poster_paths()), sort_keys=True) + ";\n" + (HERE / "band-web.js").read_text()
    return tl.main([], api=api, source=source, name=ENTRY, source_file=HERE / "band-web.js")


def refresh():
    for f in api("GET", "/Library/VirtualFolders"):
        if f.get("Name") in LIBRARIES:
            api("POST", f"/Items/{f['ItemId']}/Refresh?Recursive=true&MetadataRefreshMode=Default&ImageRefreshMode=Default&ReplaceAllImages=false&ReplaceAllMetadata=false")


def sample(out, n):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    picks = [f for f in placeholder_images() if f.name == "poster.jpg"]
    state = load_state()
    done = 0
    for f in picks:
        rel = str(f.relative_to(TRENDING))
        orig = STORE / "originals" / rel
        src = orig.read_bytes() if state.get(rel) == sha(f.read_bytes()) and orig.is_file() else f.read_bytes()
        name = f.parent.name.replace(" ", "_")[:40]
        (out / f"{name}-before.jpg").write_bytes(src)
        (out / f"{name}-after.jpg").write_bytes(bc.render(src, POSTER_BAND))
        done += 1
        if done >= n:
            break
    return 0


def main(argv):
    args = list(argv)
    style = os.environ.get("BAND_STYLE", "complement")
    if "--style" in args:
        i = args.index("--style")
        style = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    if "--sample" in args:
        i = args.index("--sample")
        return sample(args[i + 1], int(args[i + 2]))
    check = "--check" in args
    if style not in ("complement", "classic") or any(a != "--check" for a in args):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    n = images(style, check)
    print(("would change " if check else "changed ") + f"{n} placeholder images ({style})")
    if check:
        return 1 if n else 0
    injector(style, check)
    if n:
        refresh()
        print("refreshing the trending libraries")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
