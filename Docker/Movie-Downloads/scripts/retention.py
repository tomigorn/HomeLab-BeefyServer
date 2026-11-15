#!/usr/bin/env python3
"""Storage retention classifier (README "Storage retention", spec 2026-11-15): every library title is temporary or kept forever, with its stage dates (SSD until, HDD from, would-delete on, postponed reason).
Reads Radarr/Sonarr (titles, imports, ratings, tags, profiles), Jellyfin (Continue Watching, recent episode plays), Seerr's Collections store and the keep-forever store; scans only the SSD branches (the HDD is never touched, statvfs only). Rules: ../../Seerr/download-now/retention_rules.py.
Writes /srv/appdata/indexer-bar/retention.json (read by the Seerr backend). Series promotion HDD→SSD is computed and logged only.
Deletion (--apply) is off: it needs RETENTION_DELETE=1 and RETENTION_ENABLED_ON=YYYY-MM-DD (retention.conf), runs only 04:00-04:40, deletes files only (title kept, unmonitored, tag retention-deleted), never a kept or protected title.
Usage: scripts/retention.py [--plan] [--dry-run] [--apply]"""
import argparse
import dataclasses
import datetime
import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("retention_rules", HERE.parent.parent / "Seerr" / "download-now" / "retention_rules.py")
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)

TZ = ZoneInfo("Europe/Zurich")
GIB = 1024 ** 3
VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".webm", ".iso", ".mpg", ".mpeg"}
ROOTS = ("media/movies", "media/tv")
HDD_START, HDD_TARGET = 0.85, 0.80
NIGHT = (datetime.time(4, 0), datetime.time(4, 40))
DELETED_TAG = "retention-deleted"
DELETE_CAP = 20
PROMOTE_HOURS = 24
SEASON = re.compile(r"/Season (\d+)/")


@dataclasses.dataclass
class Title:
    key: str
    kind: str
    id: int
    title: str
    year: int
    imported: datetime.date
    size: int
    tags: list
    profile: str
    rating: float | None
    votes: int | None
    niche_votes: int | None
    rel: str
    videos: list = dataclasses.field(default_factory=list)
    seasons: dict = dataclasses.field(default_factory=dict)
    files: list = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Library:
    titles: list
    skipped: list


Apis = dataclasses.make_dataclass("Apis", ["radarr", "sonarr", "jf"])


@dataclasses.dataclass
class Switch:
    on: bool
    since: datetime.date | None

    @classmethod
    def from_env(cls, env):
        if env.get("RETENTION_DELETE") != "1":
            return cls(False, None)
        try:
            return cls(True, datetime.date.fromisoformat(env.get("RETENTION_ENABLED_ON", "")))
        except ValueError:
            return cls(False, None)


def stamp(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except (ValueError, AttributeError):
        return None


def rel_of(path, prefix):
    return path[len(prefix):].strip("/") if isinstance(path, str) and path.startswith(prefix) else None


def collect(apis):
    titles, skipped = [], []
    for app, kind in (("radarr", "movie"), ("sonarr", "tv")):
        call = getattr(apis, app)
        tags = {t["id"]: t["label"] for t in call("/tag")}
        profiles = {p["id"]: p["name"] for p in call("/qualityprofile")}
        for x in call("/movie" if kind == "movie" else "/series"):
            base = rel_of(x.get("path"), "/data/")
            if not base:
                continue
            labels = [tags.get(i, "") for i in x.get("tags") or []]
            r = x.get("ratings") or {}
            if kind == "movie":
                f = x.get("movieFile") or {}
                when = stamp(f.get("dateAdded"))
                if not x.get("hasFile") or not when:
                    continue
                imdb = r.get("imdb") or {}
                t = Title(rr.key(kind, x["tmdbId"]) if x.get("tmdbId") else "", kind, x["id"], x.get("title", ""), x.get("year") or 0, when.astimezone(TZ).date(), f.get("size") or 0,
                          labels, profiles.get(x.get("qualityProfileId"), ""), imdb.get("value"), imdb.get("votes"), (r.get("tmdb") or {}).get("votes"), base,
                          videos=[f"{base}/{f['relativePath']}"] if f.get("relativePath") else [], files=[f["id"]] if f.get("id") else [])
            else:
                if not (x.get("statistics") or {}).get("episodeFileCount"):
                    continue
                if not x.get("tmdbId"):
                    skipped.append(x.get("title", ""))
                    continue
                eps = call(f"/episodefile?seriesId={x['id']}")
                dates = [d for d in (stamp(e.get("dateAdded")) for e in eps) if d]
                if not dates:
                    continue
                seasons = {}
                for e in eps:
                    rel = rel_of(e.get("path"), "/data/") or (f"{base}/{e['relativePath']}" if e.get("relativePath") else None)
                    if rel:
                        seasons.setdefault(e.get("seasonNumber", 0), []).append(rel)
                t = Title(rr.key(kind, x["tmdbId"]), kind, x["id"], x.get("title", ""), x.get("year") or 0, max(dates).astimezone(TZ).date(),
                          (x.get("statistics") or {}).get("sizeOnDisk") or 0, labels, profiles.get(x.get("qualityProfileId"), ""), r.get("value"), r.get("votes"), r.get("votes"), base,
                          seasons=seasons, files=[e["id"] for e in eps if e.get("id")])
            if t.key:
                titles.append(t)
    return Library(titles, skipped)


def hot_videos(hots, roots=ROOTS):
    out = set()
    for hot in hots:
        for root in roots:
            base = pathlib.Path(hot) / root
            if not base.is_dir():
                continue
            for dirpath, _, files in os.walk(base):
                for name in files:
                    if os.path.splitext(name)[1].lower() in VIDEO:
                        out.add(os.path.relpath(os.path.join(dirpath, name), hot))
    return out


def unit(rel):
    parts = (rel or "").split("/")
    return "/".join(parts[:3]) if len(parts) > 3 else None


def by_unit(titles):
    return {t.rel: t for t in titles}


def in_progress(jf, titles):
    units, out = by_unit(titles), set()
    for user in jf("/Users"):
        for it in jf(f"/Users/{user['Id']}/Items/Resume?Limit=500&Recursive=true&IncludeItemTypes=Movie,Episode&Fields=Path&EnableImages=false")["Items"]:
            t = units.get(unit("media/" + (rel_of(it.get("Path"), "/media/") or "")))
            if t:
                out.add(t.key)
    return out


def recent_plays(jf, titles, since):
    """{series key: (season, last play iso)} for episodes played since `since`."""
    units, out = by_unit(titles), {}
    for user in jf("/Users"):
        q = "SortBy=DatePlayed&SortOrder=Descending&IncludeItemTypes=Episode&Recursive=true&Limit=200&Fields=Path&EnableImages=false&EnableUserData=true&Filters=IsPlayed,IsResumable"
        items = jf(f"/Users/{user['Id']}/Items?{q}")["Items"]
        for it in items:
            rel = "media/" + (rel_of(it.get("Path"), "/media/") or "")
            t = units.get(unit(rel))
            iso = (it.get("UserData") or {}).get("LastPlayedDate")
            when = stamp(iso)
            if not t or t.kind != "tv" or not when or when < since:
                continue
            season = next((s for s, files in t.seasons.items() if rel in files), None)
            if season is None:
                m = SEASON.search("/" + rel)
                season = int(m.group(1)) if m else None
            if season is not None and (t.key not in out or iso > out[t.key][1]):
                out[t.key] = (season, iso)
    return out


def promotions(titles, plays, hot):
    out = []
    for t in titles:
        if t.key not in plays:
            continue
        season, iso = plays[t.key]
        want = [s for s in (season, season + 1) if s in t.seasons and not all(f in hot for f in t.seasons[s])]
        if want:
            out.append({"key": t.key, "title": t.title, "seasons": want, "played": iso})
    return out


def tier(t, hot):
    files = t.videos or [f for fs in t.seasons.values() for f in fs]
    on = sum(f in hot for f in files)
    return "ssd" if files and on == len(files) else "hdd" if on == 0 else "split"


def classify(titles, overrides, collections, progress, hot, hdd, today):
    entries = {}
    for t in titles:
        why = rr.reasons(t.kind, t.year, today.year, t.niche_votes, t.rating, t.votes, t.tags, t.profile)
        e = {"t": t.title, "y": t.year, "kind": t.kind, **rr.schedule(t.imported), "default": bool(why), "reasons": why,
             "protected": rr.protected(t.key, t.tags), "tier": tier(t, hot), "size": t.size,
             "postponed": "in-progress" if t.key in progress else "collection" if t.key in collections else None}
        entries[t.key] = e
    total, used = hdd
    if total and used / total > HDD_START:
        for t in sorted(titles, key=lambda t: (t.imported, t.key)):
            if used / total <= HDD_TARGET:
                break
            e = entries[t.key]
            if rr.effective(e, overrides.get(t.key), today)["keep"] or datetime.date.fromisoformat(e["hddFrom"]) > today:
                continue
            e["space"] = today.isoformat()
            used -= t.size
    return entries


def document(entries, promote, now, switch, hdd, skipped):
    total, used = hdd
    return {"updated": now.isoformat(timespec="seconds"), "deleteEnabled": switch.on, "enabledOn": switch.since.isoformat() if switch.since else None,
            "rules": {"ssdDays": rr.SSD_DAYS, "deleteDays": rr.DELETE_DAYS, "soonDays": rr.SOON_DAYS, "postponeMaxDays": rr.POSTPONE_MAX_DAYS, "graceDays": rr.GRACE_DAYS,
                      "hddStartPct": round(HDD_START * 100), "hddTargetPct": round(HDD_TARGET * 100)},
            "hdd": {"usedPct": round(100 * used / total, 1) if total else None}, "titles": entries, "promote": promote, "skipped": len(skipped)}


def read_overrides(path):
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return {}
    titles = data.get("titles") if isinstance(data, dict) else None
    return {k: v["keep"] for k, v in (titles or {}).items() if rr.parse_key(k) and isinstance(v, dict) and isinstance(v.get("keep"), bool)}


def read_collections(path):
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return set()
    out = set()
    for lst in ((data or {}).get("lists") or {}).values():
        for t in (lst or {}).get("titles") or []:
            if isinstance(t, dict) and t.get("k") in ("movie", "tv") and isinstance(t.get("id"), int) and t["id"] > 0:
                out.add(rr.key(t["k"], t["id"]))
    return out


def in_night(now):
    return NIGHT[0] <= now.astimezone(TZ).time() < NIGHT[1]


def ensure_tag(call):
    for t in call("/tag"):
        if t.get("label") == DELETED_TAG:
            return t["id"]
    return call("/tag", "POST", {"label": DELETED_TAG})["id"]


def delete_due(titles, entries, overrides, now, switch, arr, log, cap=DELETE_CAP):
    """Delete the media files of temporary titles whose date has come. Off unless switched on; files only, the title stays (unmonitored, tagged)."""
    if not switch.on:
        log("retention: observe mode (RETENTION_DELETE off) - nothing deleted")
        return 0
    if not in_night(now):
        log("retention: deletion runs only in the night window 04:00-04:40 - nothing deleted")
        return 0
    today, done = now.astimezone(TZ).date(), 0
    for t in sorted(titles, key=lambda t: (t.imported, t.key)):
        e = entries.get(t.key)
        if not e or done >= cap:
            continue
        st = rr.effective(e, overrides.get(t.key), today, switch.since)
        if st["keep"] or e.get("protected") or not st["deleteOn"] or st["deleteOn"] > today.isoformat():
            continue
        app = "radarr" if t.kind == "movie" else "sonarr"
        call = arr(app)
        try:
            if t.kind == "movie":
                item = call(f"/movie/{t.id}")
                fid = (item.get("movieFile") or {}).get("id")
                if fid:
                    call(f"/moviefile/{fid}", "DELETE")
            else:
                item = call(f"/series/{t.id}")
                ids = [e["id"] for e in call(f"/episodefile?seriesId={t.id}") if e.get("id")]
                if ids:
                    call("/episodefile/bulk", "DELETE", {"episodeFileIds": ids})
            tid = ensure_tag(call)
            item["monitored"] = False
            item["tags"] = sorted(set(item.get("tags") or []) | {tid})
            call(f"/{'movie' if t.kind == 'movie' else 'series'}/{t.id}", "PUT", item)
            done += 1
            log(f"retention: deleted the files of {t.key} ({t.title}, {st['why']} {st['deleteOn']}); title kept, unmonitored")
        except Exception as ex:
            log(f"retention: {t.key} ({t.title}) not deleted: {type(ex).__name__}")
    return done


def arr_key(config_xml):
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", pathlib.Path(config_xml).read_text())
    if not m:
        raise SystemExit(f"no API key in {config_xml}")
    return m.group(1)


def api(base, headers):
    def call(path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method, headers={**headers, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw.strip() else None
    return call


def write_atomic(path, data):
    path = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".retention-")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def human(n):
    return f"{n / GIB:.1f} GiB"


def plan_lines(doc, overrides, today):
    rows = []
    for k, e in doc["titles"].items():
        st = rr.effective(e, overrides.get(k), today, datetime.date.fromisoformat(doc["enabledOn"]) if doc["enabledOn"] else None)
        rows.append((st["deleteOn"] or "9999", k, e, st))
    out = []
    for _, k, e, st in sorted(rows):
        what = f"keep ({st['source']}: {','.join(e['reasons']) or 'test film' if st['source'] != 'override' else 'override'})" if st["keep"] else f"delete {st['deleteOn']} ({st['why']})"
        out.append(f"  {what:38} {e['tier']:5} {human(e['size']):>10}  imported {e['imported']}  SSD until {e['ssdUntil']}  {k}  {e['t']} ({e['y']})" + (f"  postponed: {e['postponed']}" if e["postponed"] else ""))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Storage retention classifier (observe mode unless switched on)")
    ap.add_argument("--plan", action="store_true", help="print every title with its stage dates, write nothing")
    ap.add_argument("--dry-run", action="store_true", help="compute and log, write nothing")
    ap.add_argument("--apply", action="store_true", help="delete due titles' files (needs RETENTION_DELETE=1 and RETENTION_ENABLED_ON, night window only)")
    a = ap.parse_args(argv)
    env = os.environ
    now = datetime.datetime.now(TZ).replace(microsecond=0)
    jf_key = pathlib.Path(env.get("RT_JF_KEY", "/home/buntu/Projects/Docker/Jellyfin/secrets/api-key")).read_text().strip()
    apis = Apis(api(env.get("RT_RADARR", "http://127.0.0.1:7878") + "/api/v3", {"X-Api-Key": arr_key(env.get("RT_RADARR_CONFIG", "/srv/appdata/radarr/config.xml"))}),
                api(env.get("RT_SONARR", "http://127.0.0.1:8989") + "/api/v3", {"X-Api-Key": arr_key(env.get("RT_SONARR_CONFIG", "/srv/appdata/sonarr/config.xml"))}),
                api(env.get("RT_JF", "http://127.0.0.1:8096"), {"Authorization": f"MediaBrowser Token={jf_key}"}))
    lib = collect(apis)
    progress = in_progress(apis.jf, lib.titles)
    plays = recent_plays(apis.jf, lib.titles, now - datetime.timedelta(hours=PROMOTE_HOURS))
    hot = hot_videos([pathlib.Path(h) for h in env.get("HOT", "/srv/.disks/ssd-hot /srv/.disks/ssd-hot2").split()])
    st = os.statvfs(env.get("COLD", "/srv/.disks/hdd-cold"))
    hdd = (st.f_blocks * st.f_frsize, (st.f_blocks - st.f_bavail) * st.f_frsize)
    overrides = read_overrides(env.get("RT_KEEP", "/srv/appdata/seerr-retention/keep.json"))
    collections = read_collections(env.get("RT_COLLECTIONS", "/srv/appdata/seerr-collections/lists.json"))
    entries = classify(lib.titles, overrides, collections, progress, hot, hdd, now.date())
    promote = promotions(lib.titles, plays, hot)
    switch = Switch.from_env(env)
    doc = document(entries, promote, now, switch, hdd, lib.skipped)
    if a.plan:
        print("\n".join(plan_lines(doc, overrides, now.date())))
        return 0
    keep = sum(rr.effective(e, overrides.get(k), now.date())["keep"] for k, e in entries.items())
    print(f"retention: {len(entries)} titles, {keep} kept forever, {len(entries) - keep} temporary, HDD {doc['hdd']['usedPct']} %{'' if switch.on else ', observe mode'}", flush=True)
    for p in promote:
        print(f"retention: would promote {p['key']} ({p['title']}) seasons {p['seasons']} to the SSD (played {p['played']}); observe only, nothing moved", flush=True)
    if lib.skipped:
        print(f"retention: {len(lib.skipped)} series without a TMDB id left out", flush=True)
    if not a.dry_run:
        write_atomic(env.get("RT_OUT", "/srv/appdata/indexer-bar/retention.json"), doc)
    if a.apply:
        arr = {"radarr": apis.radarr, "sonarr": apis.sonarr}
        delete_due(lib.titles, entries, overrides, now, switch, lambda app: arr[app], lambda l: print(l, flush=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
