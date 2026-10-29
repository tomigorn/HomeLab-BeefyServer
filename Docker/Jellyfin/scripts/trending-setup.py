#!/usr/bin/env python3
"""Creates the hidden "Trending items" movie library, hides it for every user and renames the Collections library.
Idempotent. Usage: trending-setup.py [--dry-run | --check]  (--check exits 1 when a user still sees the library)"""
import base64
import copy
import json
import pathlib
import subprocess
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from trending_model import COLLECTIONS_NAME, LIBRARY, find_library, hidden, library_id  # noqa: E402

PATH = "/config/trending/library"
OLD_COLLECTIONS = "Collections"
SEERR_SETTINGS = pathlib.Path("/srv/appdata/seerr/settings.json")
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
    "TypeOptions": [{"Type": "Movie", "MetadataFetchers": [], "MetadataFetcherOrder": [], "ImageFetchers": [], "ImageFetcherOrder": [], "ImageOptions": [], "SimilarItemProviders": [], "SimilarItemProviderOrder": []}],
}


def library_options(existing):
    out = copy.deepcopy(existing) if existing else {}
    out.update(copy.deepcopy(WANT))
    disabled = list(out.get("DisabledMediaSegmentProviders") or [])
    if "Intro Skipper" not in disabled:
        disabled.append("Intro Skipper")
    out["DisabledMediaSegmentProviders"] = disabled
    return out


def collections_folder(items):
    return next((i for i in items if i.get("CollectionType") == "boxsets"), None)


def tile_needed(item, force):
    return force or not (item.get("ImageTags") or {}).get("Primary")


def check_line(users, lib_id):
    seeing = sum(lib_id not in (u["Configuration"].get("MyMediaExcludes") or []) for u in users)
    if seeing:
        return False, f"{seeing} of {len(users)} users see Trending items"
    return True, f"all {len(users)} users hide Trending items"


def seerr_enabled():
    try:
        text = SEERR_SETTINGS.read_text()
    except PermissionError:
        text = subprocess.run(["sudo", "-n", "cat", str(SEERR_SETTINGS)], capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        return False
    return any(lib.get("name") == LIBRARY and lib.get("enabled") for lib in json.loads(text).get("jellyfin", {}).get("libraries", []))


def main(argv):
    from jellyfin_api import api, api_raw

    dry = "--dry-run" in argv
    folders = api("GET", "/Library/VirtualFolders")
    lib = library_id(folders)
    users = api("GET", "/Users")
    if "--check" in argv:
        if lib is None:
            print("Trending items library missing")
            return 1
        ok, line = check_line(users, lib)
        print(line)
        return 0 if ok else 1

    def say(step):
        print(f"would {step}" if dry else step)

    present = find_library(folders)
    if present is not None and lib is None:
        print(f"{LIBRARY} exists but is unresolved (no ItemId); run a library scan, then rerun")
        return 1
    if present is None:
        say(f"create library {LIBRARY} at {PATH}")
        if not dry:
            q = urllib.parse.urlencode({"name": LIBRARY, "collectionType": "movies", "paths": PATH, "refreshLibrary": "false"})
            api("POST", f"/Library/VirtualFolders?{q}", {"LibraryOptions": library_options(None)})
            folders = api("GET", "/Library/VirtualFolders")
            lib = library_id(folders)
            if lib is None:
                print(f"{LIBRARY} was created but has no ItemId yet; users not changed, rerun after a library scan")
                return 1
    else:
        folder = next(f for f in folders if f.get("ItemId") == lib)
        if library_options(folder["LibraryOptions"]) != folder["LibraryOptions"]:
            say("update library options")
            if not dry:
                api("POST", "/Library/VirtualFolders/LibraryOptions", {"Id": lib, "LibraryOptions": library_options(folder["LibraryOptions"])})

    for u in users:
        if lib is None or lib not in (u["Configuration"].get("MyMediaExcludes") or []):
            say(f"hide {LIBRARY} for {u['Name']}")
            if not dry:
                api("POST", f"/Users/{u['Id']}/Configuration", hidden(u["Configuration"], lib))

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

            api_raw("POST", f"/Items/{coll['Id']}/Images/Primary", base64.b64encode(library_tile(COLLECTIONS_NAME)), "image/jpeg")

    if seerr_enabled():
        print(f"WARNING: Seerr has {LIBRARY} enabled; untick it in Seerr's Jellyfin libraries")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
