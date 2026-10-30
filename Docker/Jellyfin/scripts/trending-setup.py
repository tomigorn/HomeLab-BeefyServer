#!/usr/bin/env python3
"""Creates the hidden libraries "Trending items" (movies) and "Trending shows" (tvshows), hides them for every user and renames the Collections library.
A new library is scanned (full library scan) and hidden as soon as it resolves, in the same run.
Idempotent. Usage: trending-setup.py [--dry-run | --check]  (--check exits 1 when a user still sees the library)"""
import base64
import copy
import json
import pathlib
import stat
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from trending_model import COLLECTIONS_NAME, LIBRARY, SHOWS_LIBRARY, find_library, hidden, library_id  # noqa: E402

PATH = "/config/trending/library"
LIBRARIES = [(LIBRARY, "movies", PATH), (SHOWS_LIBRARY, "tvshows", "/config/trending/shows")]
TYPES = {"movies": ("Movie",), "tvshows": ("Series", "Season", "Episode")}
HOST_ROOT = pathlib.Path("/srv/appdata/jellyfin/trending")
POLL = 2
RESOLVE_TRIES = 150
SCAN_TRIES = 900
sleep = time.sleep
OLD_COLLECTIONS = "Collections"
SEERR_SETTINGS = pathlib.Path("/srv/appdata/seerr/settings.json")
LISTS = pathlib.Path("/srv/appdata/jellyfin-popular/lists.json")
CACHE = pathlib.Path("/srv/appdata/jellyfin/trending/cache")
WANT = {
    "Enabled": True,
    "EnableRealtimeMonitor": False,
    "EnableInternetProviders": False,
    "EnableTrickplayImageExtraction": False,
    "ExtractTrickplayImagesDuringLibraryScan": False,
    "EnableChapterImageExtraction": False,
    "ExtractChapterImagesDuringLibraryScan": False,
    "AutomaticallyAddToCollection": False,
    "SaveLocalMetadata": False,
    "MetadataSavers": [],
    "LocalMetadataReaderOrder": ["Nfo"],
}


def type_options(kind):
    return {"Type": kind, "MetadataFetchers": [], "MetadataFetcherOrder": [], "ImageFetchers": [], "ImageFetcherOrder": [], "ImageOptions": [], "SimilarItemProviders": [], "SimilarItemProviderOrder": []}


def library_options(existing, collection_type="movies"):
    out = copy.deepcopy(existing) if existing else {}
    out.update(copy.deepcopy(WANT))
    out["TypeOptions"] = [type_options(k) for k in TYPES[collection_type]]
    if collection_type == "tvshows":
        out["EnableAutomaticSeriesGrouping"] = False
    disabled = list(out.get("DisabledMediaSegmentProviders") or [])
    if "Intro Skipper" not in disabled:
        disabled.append("Intro Skipper")
    out["DisabledMediaSegmentProviders"] = disabled
    return out


def collections_folder(items):
    return next((i for i in items if i.get("CollectionType") == "boxsets"), None)


def tile_needed(item, force):
    return force or not (item.get("ImageTags") or {}).get("Primary")


def tile_posters(lists_file, cache):
    """Cached posters (w780) of the Top 10 movies, in rank order, for the library tile."""
    try:
        movies = json.loads(lists_file.read_text()).get("home", {}).get("movies") or []
    except (OSError, ValueError):
        return []
    out = []
    for e in movies:
        p = e.get("p")
        p = (p.get("en") or p.get("de")) if isinstance(p, dict) else p
        if not p or "/" in p[1:]:
            continue
        try:
            out.append((cache / f"w780_{p[1:]}").read_bytes())
        except OSError:
            continue
    return out


def check_line(users, libs):
    """libs = {library name: id}; ok only when every user hides every library."""
    for name, lib_id in libs.items():
        seeing = sum(lib_id not in (u["Configuration"].get("MyMediaExcludes") or []) for u in users)
        if seeing:
            return False, f"{seeing} of {len(users)} users see {name}"
    return True, f"all {len(users)} users hide {' and '.join(libs)}"


def ensure_dirs(root, names, dry, run=subprocess.run):
    """Creates missing library folders owned like library/ (the trending root belongs to the container user)."""
    like = root / "library"
    missing = [root / n for n in names if not (root / n).is_dir()]
    if dry or not missing:
        return missing
    st = like.stat() if like.is_dir() else root.stat()
    mode = f"{stat.S_IMODE(st.st_mode):o}" if like.is_dir() else "2755"
    for d in missing:
        run(["sudo", "-n", "install", "-d", "-o", str(st.st_uid), "-g", str(st.st_gid), "-m", mode, str(d)], check=True)
    return missing


def wait_resolved(api, name):
    for _ in range(RESOLVE_TRIES):
        folders = api("GET", "/Library/VirtualFolders")
        if library_id(folders, name):
            return folders
        sleep(POLL)
    return folders


def scan_running(api):
    return any(t.get("Key") == "RefreshLibrary" and t.get("State") != "Idle" for t in api("GET", "/ScheduledTasks"))


def seerr_enabled():
    try:
        text = SEERR_SETTINGS.read_text()
    except PermissionError:
        text = subprocess.run(["sudo", "-n", "cat", str(SEERR_SETTINGS)], capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        return False
    names = {name for name, _, _ in LIBRARIES}
    return any(lib.get("name") in names and lib.get("enabled") for lib in json.loads(text).get("jellyfin", {}).get("libraries", []))


def main(argv):
    from jellyfin_api import api, api_raw

    dry = "--dry-run" in argv
    folders = api("GET", "/Library/VirtualFolders")
    users = api("GET", "/Users")
    if "--check" in argv:
        libs = {name: library_id(folders, name) for name, _, _ in LIBRARIES}
        missing = [name for name, lib_id in libs.items() if lib_id is None]
        if missing:
            print(f"{' and '.join(missing)} library missing")
            return 1
        ok, line = check_line(users, libs)
        print(line)
        return 0 if ok else 1

    def say(step):
        print(f"would {step}" if dry else step)

    for d in ensure_dirs(HOST_ROOT, [pathlib.PurePath(p).name for _, _, p in LIBRARIES], dry):
        say(f"create {d}")

    rc, ids, scanned = 0, [], False
    for name, collection_type, path in LIBRARIES:
        present, lib = find_library(folders, name), library_id(folders, name)
        if present is not None and lib is None:
            print(f"{name} exists but is unresolved (no ItemId); run a library scan, then rerun")
            rc = 1
            continue
        if present is None:
            say(f"create library {name} at {path}")
            if dry:
                continue
            q = urllib.parse.urlencode({"name": name, "collectionType": collection_type, "paths": path, "refreshLibrary": "false"})
            api("POST", f"/Library/VirtualFolders?{q}", {"LibraryOptions": library_options(None, collection_type)})
            folders = api("GET", "/Library/VirtualFolders")
            lib = library_id(folders, name)
            if lib is None and not scanned:
                say("scan all libraries")
                api("POST", "/Library/Refresh")
                scanned = True
                folders = wait_resolved(api, name)
                lib = library_id(folders, name)
            if lib is None:
                print(f"{name} was created but has no ItemId yet; users not changed for it, rerun after a library scan")
                rc = 1
                continue
        else:
            folder = next(f for f in folders if f.get("ItemId") == lib)
            want = library_options(folder["LibraryOptions"], collection_type)
            if want != folder["LibraryOptions"]:
                say(f"update {name} options")
                if not dry:
                    api("POST", "/Library/VirtualFolders/LibraryOptions", {"Id": lib, "LibraryOptions": want})
        ids.append((name, lib))

    for u in users:
        config = u["Configuration"]
        for _, lib in ids:
            config = hidden(config, lib)
        if config != u["Configuration"]:
            say(f"hide {', '.join(n for n, _ in ids)} for {u['Name']}")
            if not dry:
                api("POST", f"/Users/{u['Id']}/Configuration", config)

    if scanned:
        for _ in range(SCAN_TRIES):
            if not scan_running(api):
                print("library scan finished")
                break
            sleep(POLL)
        else:
            print(f"library scan still running after {SCAN_TRIES * POLL} s; wait for it before the next trending.py run")
            rc = 1

    if any(f["Name"] == OLD_COLLECTIONS and f.get("CollectionType") == "boxsets" for f in folders):
        say(f"rename {OLD_COLLECTIONS} to {COLLECTIONS_NAME}")
        if not dry:
            q = urllib.parse.urlencode({"name": OLD_COLLECTIONS, "newName": COLLECTIONS_NAME, "refreshLibrary": "false"})
            api("POST", f"/Library/VirtualFolders/Name?{q}")

    coll = collections_folder(api("GET", "/Library/MediaFolders")["Items"])
    if coll and tile_needed(coll, "--force-tile" in argv):
        say("upload the library tile")
        if not dry:
            from trending_art import library_tile

            api_raw("POST", f"/Items/{coll['Id']}/Images/Primary", base64.b64encode(library_tile(COLLECTIONS_NAME, tile_posters(LISTS, CACHE)[:3])), "image/jpeg")

    if seerr_enabled():
        print("WARNING: Seerr has a trending library enabled; untick Trending items and Trending shows in Seerr's Jellyfin libraries")
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
