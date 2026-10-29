#!/usr/bin/env python3
"""Capture the real responses the Swiftfin (tvOS) and Android TV clients request for the trending screens, read-only, as one existing non-admin user (Quick Connect).
Usage: trending-device-views.py [--out DIR]   (default ~/.cache/trending-device-views/<date>/)"""
import argparse
import datetime
import json
import pathlib
import re
import sys
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

COLLECTIONS_VIEW = "Collections & Trending"
TOP_MOVIES = "Top 10 Switzerland · Movies"
SCREENS = ("media", "collections", "list", "details", "movies", "search")
DROP_KEYS = {"UserId", "AccessToken", "Token", "User", "ApiKey", "api_key"}
SF_FIELDS = "MediaSources,ParentId,ChannelInfo"
SF_PAGE = 50
ATV_FIELDS = "CanDelete,ChannelInfo,Chapters,ChildCount,CumulativeRunTimeTicks,DateCreated,DisplayPreferencesId,Genres,ItemCounts,MediaSourceCount,MediaSources,MediaStreams,Overview,Path,PrimaryImageAspectRatio,Taglines,Trickplay"
ATV_PAGE = 100

CLIENTS = {
    "swiftfin": {"client": "Swiftfin tvOS", "device": "Apple TV", "device_id": "trending-views-swiftfin", "version": "1.6.1"},
    "androidtv": {"client": "Jellyfin for Android TV", "device": "Android TV", "device_id": "trending-views-androidtv", "version": "0.20.0-beta.4"},
}
SF = "Swiftfin/Shared/"
ATV = "jellyfin-androidtv/app/src/main/java/org/jellyfin/androidtv/"
SOURCES = {
    "swiftfin": {
        "media": SF + "Objects/Libraries/UserViewLibrary.swift",
        "collections": SF + "Objects/Libraries/ItemLibrary.swift",
        "list": SF + "Views/ItemContentGroupView/ItemContentGroupProvider.swift, " + SF + "ViewModels/ContentGroupViewModel/ItemTypeContentGroupProvider.swift",
        "details": SF + "Extensions/JellyfinAPI/BaseItemDto/BaseItemDto.swift (getFullItem), " + SF + "Views/ItemContentGroupView/ItemContentGroupProvider.swift",
        "movies": SF + "Coordinators/Tabs/MainTabView.swift, " + SF + "Objects/Libraries/ItemLibrary.swift",
        "search": SF + "ViewModels/SearchViewModel.swift, " + SF + "ViewModels/ContentGroupViewModel/SearchContentGroupProvider.swift",
    },
    "androidtv": {
        "media": ATV + "data/repository/UserViewsRepository.kt",
        "collections": ATV + "ui/browsing/BrowseGridFragment.java, " + ATV + "ui/browsing/BrowsingUtils.kt",
        "list": ATV + "ui/browsing/CollectionFragment.kt",
        "details": ATV + "ui/itemdetail/FullDetailsFragmentHelper.kt",
        "movies": ATV + "ui/browsing/BrowseGridFragment.java, " + ATV + "ui/browsing/BrowsingUtils.kt",
        "search": ATV + "ui/search/SearchRepository.kt, " + ATV + "ui/search/SearchViewModel.kt",
    },
}


def query(path, params):
    return path + "?" + urllib.parse.urlencode(params, safe=",") if params else path


def scrub(obj, user_id="", user_name=""):
    names = [re.compile(rf"(?<!\w){re.escape(user_name)}(?!\w)", re.I)] if len(user_name or "") >= 3 else []
    ids = {x for x in (user_id, (user_id or "").replace("-", "")) if x}

    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items() if k not in DROP_KEYS}
        if isinstance(o, list):
            return [walk(v) for v in o]
        if isinstance(o, str):
            for i in ids:
                o = o.replace(i, "")
            for n in names:
                o = n.sub("viewer", o)
        return o

    return walk(obj)


def masked(path, user_id):
    for i in {user_id, user_id.replace("-", "")}:
        path = path.replace(i, "{userId}")
    return path


def find_view(views, name):
    return next((v for v in views.get("Items", []) if v.get("Name") == name), None)


def is_found(item):
    sources = item.get("MediaSources") or []
    return (bool(sources) and "⬇ Download" in sources[0].get("Name", "")) or (item.get("Overview") or "").startswith("⬇ Not downloaded")


def capture_swiftfin(get, user_id):
    out = {}
    base = {"userId": user_id, "enableUserData": "true", "fields": SF_FIELDS, "sortBy": "SortName", "sortOrder": "Ascending", "limit": SF_PAGE, "startIndex": 0}

    def items(screen, **extra):
        return get(screen, "/Items", {**base, **extra})

    views = get("media", "/UserViews", {"userId": user_id})
    excludes = set((get("media", "/Users/Me").get("Configuration") or {}).get("MyMediaExcludes") or [])
    views["Items"] = [v for v in views.get("Items", []) if v.get("Id") not in excludes]
    out["media"] = views

    view = find_view(views, COLLECTIONS_VIEW)
    out["collections"] = items("collections", parentId=view["Id"], includeItemTypes="BoxSet,Movie,MusicVideo,Series,Video")
    top = next(i for i in out["collections"]["Items"] if i.get("Name") == TOP_MOVIES)
    out["list"] = {t: items("list", parentId=top["Id"], includeItemTypes=t, recursive="true") for t in ("Movie", "Series", "Video")}
    found = next(i for i in out["list"]["Movie"]["Items"] if is_found(i))
    pid = f"/Items/{found['Id']}"
    details = get("details", pid, {"userId": user_id})
    details["_related"] = {
        "localTrailers": get("details", pid + "/LocalTrailers", {"userId": user_id}),
        "specialFeatures": get("details", pid + "/SpecialFeatures"),
        "similar": get("details", pid + "/Similar", {"userId": user_id, "fields": SF_FIELDS, "limit": SF_PAGE}),
    }
    out["details"] = details
    out["movies"] = items("movies", includeItemTypes="Movie", recursive="true")
    out["search"] = {t: items("search", searchTerm=found["Name"], includeItemTypes=t, recursive="true") for t in ("Movie", "Series", "BoxSet,UserView")}
    return out


def capture_androidtv(get, user_id):
    out = {}
    paged = {"fields": ATV_FIELDS, "limit": ATV_PAGE, "startIndex": 0}
    grid = {"sortBy": "SortName", "sortOrder": "Ascending"}

    def items(screen, **extra):
        return get(screen, "/Items", {**paged, **extra})

    views = get("media", "/UserViews")
    out["media"] = views
    view = find_view(views, COLLECTIONS_VIEW)
    out["collections"] = items("collections", parentId=view["Id"], **grid)
    top = next(i for i in out["collections"]["Items"] if i.get("Name") == TOP_MOVIES)
    out["list"] = {
        "Movie": items("list", parentId=top["Id"], includeItemTypes="Movie"),
        "Series": items("list", parentId=top["Id"], includeItemTypes="Series"),
        "Other": items("list", parentId=top["Id"], excludeItemTypes="Movie,Series"),
    }
    found = next(i for i in out["list"]["Movie"]["Items"] if is_found(i))
    pid = f"/Items/{found['Id']}"
    details = get("details", pid)
    details["_related"] = {"localTrailers": get("details", pid + "/LocalTrailers")}
    out["details"] = details
    movies = next(v for v in views["Items"] if v.get("CollectionType") == "movies" and v.get("Name") != COLLECTIONS_VIEW)
    out["movies"] = items("movies", parentId=movies["Id"], includeItemTypes="Movie", recursive="true", **grid)
    search = {"searchTerm": found["Name"], "limit": 25, "imageTypeLimit": 1, "fields": ATV_FIELDS, "recursive": "true", "enableTotalRecordCount": "false"}
    out["search"] = {t: get("search", "/Items", {**search, "includeItemTypes": t}) for t in ("Movie", "Series", "BoxSet")}
    return out


CAPTURE = {"swiftfin": capture_swiftfin, "androidtv": capture_androidtv}


def run_client(name, send, user_id):
    requests = []

    def get(screen, path, params=None):
        full = query(path, params)
        requests.append({"screen": screen, "method": "GET", "path_with_query": masked(full, user_id), "source": SOURCES[name][screen]})
        return send(full)

    return CAPTURE[name](get, user_id), requests


def save(out_dir, name, data, user_id, user_name):
    out_dir.mkdir(parents=True, exist_ok=True)
    for screen in SCREENS:
        (out_dir / f"{name}-{screen}.json").write_text(json.dumps(scrub(data[screen], user_id, user_name), ensure_ascii=False, indent=1))


def save_meta(out_dir, per_client):
    meta = {n: {"client": CLIENTS[n]["client"], "version": CLIENTS[n]["version"], "requests": r} for n, r in per_client.items()}
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))


def main(argv=None):
    import jellyfin_api
    import jellyfin_quickconnect as qc

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    out_dir = pathlib.Path(args.out) if args.out else pathlib.Path.home() / ".cache" / "trending-device-views" / datetime.date.today().isoformat()
    users = jellyfin_api.api("GET", "/Users")
    user = next(u for u in users if not u["Policy"]["IsAdministrator"] and not u["Policy"]["IsDisabled"])
    user_id, user_name = user["Id"], user["Name"]
    per_client = {}
    for name, c in CLIENTS.items():
        tok = qc.token(c["client"], c["device"], c["device_id"], c["version"], user_id)
        try:
            auth = qc.header(c["client"], c["device"], c["device_id"], c["version"], tok)
            data, reqs = run_client(name, lambda p: qc.http("GET", p, None, auth), user_id)
            save(out_dir, name, data, user_id, user_name)
            per_client[name] = reqs
        finally:
            qc.logout(tok, c["device_id"])
    save_meta(out_dir, per_client)
    print(f"{len(per_client) * len(SCREENS)} screen files and meta.json in {out_dir}")


if __name__ == "__main__":
    main()
