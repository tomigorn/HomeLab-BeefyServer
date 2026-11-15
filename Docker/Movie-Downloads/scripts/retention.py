#!/usr/bin/env python3
"""Storage retention classifier (README "Storage retention", spec 2026-11-15): every library title is temporary or kept forever, with its stage dates (SSD until, HDD from, would-delete on, postponed reason).
Reads Radarr/Sonarr (titles, imports, ratings, tags, profiles, queues), Seerr's media rows (series are keyed by Seerr's TMDB id, mapped by Sonarr id or TVDB id; unmapped series are protected), Jellyfin (Continue Watching, Next Up, plays of the last 14 days, coverage), Seerr's Collections store and the keep-forever store; scans only the SSD branches (the HDD is never touched, statvfs only, after a mount check). Rules: ../../Seerr/download-now/retention_rules.py.
Writes /srv/appdata/indexer-bar/retention.json (read by the Seerr backend) and the leaving-soon history. Series promotion HDD→SSD is computed and logged only.
Deletion (--apply) is off: it needs RETENTION_DELETE=1 and RETENTION_ENABLED_ON=YYYY-MM-DD (retention.conf), a synced clock, readable data (keep store, Collections, Seerr, Jellyfin coverage ≥ 90 %), the media lock, 04:00-04:40, and 7 days of "Leaving soon" per title. It unmonitors + tags first, then deletes files only, at most 20 attempts a night (5 for the space rule), and stops at the first error.
Usage: scripts/retention.py [--plan] [--dry-run] [--apply]"""
import argparse
import contextlib
import dataclasses
import datetime
import fcntl
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
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
SPACE_CAP = 5
SOON_DAYS_BEFORE_DELETE = 7
RECYCLE_DAYS = 7
FRESH_HOURS = 6
PLAYED_DAYS = 14
COVERAGE_MIN = 0.9
LOCK_FILE = "/run/lock/beefy-media.lock"
LOCK_WAIT = 240
PROMOTE_HOURS = 24
GONE_CAP = 500
JF_LISTS = {"leaving": ("user-retention-leaving", "Leaving soon", "temporary titles that leave within 7 days"),
            "gone": ("user-retention-gone", "Left the server", "files deleted by the storage cleanup; request again to download")}
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
    tmdb: int = 0
    tvdb: int | None = None
    unmapped: bool = False
    added_at: datetime.datetime | None = None


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


@dataclasses.dataclass
class Night:
    """What the delete run may rely on: problems (any = no deletion), leaving-soon history, titles with queue items, HDD bytes used now, the previous delete run, clock synced, the first day the switch was seen on."""
    problems: list
    soon: dict
    busy: set
    hdd_used: int
    last: dict
    clock_ok: bool
    first_on: datetime.date | None


class NotMounted(Exception):
    pass


def stamp(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except (ValueError, AttributeError):
        return None


def rel_of(path, prefix):
    return path[len(prefix):].strip("/") if isinstance(path, str) and path.startswith(prefix) else None


def seerr_tv_map(rows):
    """{("sonarr", id) | ("tvdb", id): Seerr TMDB id} from Seerr's media rows (as manual.py: externalServiceId, else tvdbId)."""
    out = {}
    for r in rows or []:
        if not isinstance(r, dict) or r.get("mediaType") != "tv" or not isinstance(r.get("tmdbId"), int) or r["tmdbId"] <= 0:
            continue
        if isinstance(r.get("externalServiceId"), int):
            out.setdefault(("sonarr", r["externalServiceId"]), r["tmdbId"])
        if isinstance(r.get("tvdbId"), int):
            out.setdefault(("tvdb", r["tvdbId"]), r["tmdbId"])
    return out


def collect(apis, seerr_rows):
    titles, skipped = [], []
    tv = seerr_tv_map(seerr_rows)
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
                if not x.get("hasFile") or not when or not x.get("tmdbId"):
                    continue
                imdb = r.get("imdb") or {}
                t = Title(rr.key(kind, x["tmdbId"]), kind, x["id"], x.get("title", ""), x.get("year") or 0, when.astimezone(TZ).date(), f.get("size") or 0,
                          labels, profiles.get(x.get("qualityProfileId"), ""), imdb.get("value"), imdb.get("votes"), (r.get("tmdb") or {}).get("votes"), base,
                          videos=[f"{base}/{f['relativePath']}"] if f.get("relativePath") else [], files=[f["id"]] if f.get("id") else [], tmdb=x["tmdbId"], added_at=when)
            else:
                if not (x.get("statistics") or {}).get("episodeFileCount"):
                    continue
                tvdb = x.get("tvdbId") if isinstance(x.get("tvdbId"), int) and x.get("tvdbId") > 0 else None
                seerr = tv.get(("sonarr", x["id"])) or (tv.get(("tvdb", tvdb)) if tvdb else None)
                tmdb = seerr or x.get("tmdbId")
                if not tmdb:
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
                t = Title(rr.key(kind, tmdb), kind, x["id"], x.get("title", ""), x.get("year") or 0, max(dates).astimezone(TZ).date(),
                          (x.get("statistics") or {}).get("sizeOnDisk") or 0, labels, profiles.get(x.get("qualityProfileId"), ""), r.get("value"), r.get("votes"), r.get("votes"), base,
                          seasons=seasons, files=[e["id"] for e in eps if e.get("id")], tmdb=tmdb, tvdb=tvdb, unmapped=not seerr, added_at=max(dates))
            titles.append(t)
    mapped = {t.key for t in titles if not t.unmapped}
    keep = []
    for t in titles:
        if t.unmapped and t.key in mapped:
            skipped.append(t.title)
        else:
            keep.append(t)
    return Library(keep, skipped)


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


def hdd_usage(path, ismount=os.path.ismount, statvfs=os.statvfs):
    if not ismount(path):
        raise NotMounted(f"{path} is not mounted")
    st = statvfs(path)
    return st.f_blocks * st.f_frsize, (st.f_blocks - st.f_bavail) * st.f_frsize


def unit(rel):
    parts = (rel or "").split("/")
    return "/".join(parts[:3]) if len(parts) > 3 else None


def by_unit(titles):
    return {t.rel: t for t in titles}


def jf_rel(path):
    return "media/" + (rel_of(path, "/media/") or "")


def in_progress(jf, titles, now):
    """Keys someone is watching: Continue Watching, Next Up, or played in the last PLAYED_DAYS days (binge watching)."""
    units, out, since = by_unit(titles), set(), now - datetime.timedelta(days=PLAYED_DAYS)
    for user in jf("/Users"):
        uid = user["Id"]
        items = jf(f"/Users/{uid}/Items/Resume?Limit=500&Recursive=true&IncludeItemTypes=Movie,Episode&Fields=Path&EnableImages=false")["Items"]
        items += jf(f"/Shows/NextUp?userId={uid}&Limit=500&Fields=Path&EnableImages=false")["Items"]
        q = "SortBy=DatePlayed&SortOrder=Descending&IncludeItemTypes=Movie,Episode&Recursive=true&Limit=500&Fields=Path&EnableImages=false&EnableUserData=true&Filters=IsPlayed"
        items += [it for it in jf(f"/Users/{uid}/Items?{q}")["Items"] if (stamp((it.get("UserData") or {}).get("LastPlayedDate")) or since) > since]
        for it in items:
            t = units.get(unit(jf_rel(it.get("Path"))))
            if t:
                out.add(t.key)
    return out


def coverage(jf, titles, now):
    """Share of the library's files (imported more than a day ago) that Jellyfin knows (None: nothing to check)."""
    users = jf("/Users")
    known = {jf_rel(it.get("Path")) for it in jf(f"/Items?userId={users[0]['Id']}&Recursive=true&IncludeItemTypes=Movie,Episode&Fields=Path&EnableImages=false")["Items"]} if users else set()
    old = [f for t in titles if t.added_at and t.added_at < now - datetime.timedelta(days=1) for f in (t.videos or [x for s in t.seasons.values() for x in s])]
    return sum(f in known for f in old) / len(old) if old else None


def recent_plays(jf, titles, since):
    """{series key: (season, last play iso)} for episodes played since `since`."""
    units, out = by_unit(titles), {}
    for user in jf("/Users"):
        q = "SortBy=DatePlayed&SortOrder=Descending&IncludeItemTypes=Episode&Recursive=true&Limit=200&Fields=Path&EnableImages=false&EnableUserData=true&Filters=IsPlayed,IsResumable"
        for it in jf(f"/Users/{user['Id']}/Items?{q}")["Items"]:
            rel = jf_rel(it.get("Path"))
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


def files_of(t):
    return t.videos or [f for fs in t.seasons.values() for f in fs]


def tier(t, hot):
    files = files_of(t)
    on = sum(f in hot for f in files)
    return "ssd" if files and on == len(files) else "hdd" if on == 0 else "split"


def hdd_bytes(t, hot):
    files = files_of(t)
    return int(t.size * sum(f not in hot for f in files) / len(files)) if files else 0


def override_for(overrides, key, entry):
    if key in overrides:
        return overrides[key]
    tvdb = (entry or {}).get("tvdb")
    return overrides.get(f"tvdb/{tvdb}") if tvdb else None


def classify(titles, overrides, collections, progress, hot, hdd, today, pending=0):
    """pending: HDD bytes of our own deletions still in the 7-day recycle bin (counted as free)."""
    entries = {}
    for t in titles:
        why = rr.reasons(t.kind, t.year, today.year, t.niche_votes, t.rating, t.votes, t.tags, t.profile)
        e = {"t": t.title, "y": t.year, "kind": t.kind, **rr.schedule(t.imported), "default": bool(why), "reasons": why,
             "protected": rr.protected(t.key, t.tags) or t.unmapped, "tier": tier(t, hot), "size": t.size, "hddBytes": hdd_bytes(t, hot),
             "postponed": "in-progress" if t.key in progress else "collection" if t.key in collections else None}
        if t.kind == "tv":
            e["tvdb"] = t.tvdb
            if t.unmapped:
                e["unmapped"] = True
        entries[t.key] = e
    total, used = hdd
    used -= pending
    if total and used / total > HDD_START:
        for t in sorted(titles, key=lambda t: (t.imported, t.key)):
            if used / total <= HDD_TARGET:
                break
            e = entries[t.key]
            if not e["hddBytes"] or rr.effective(e, override_for(overrides, t.key, e), today)["keep"] or datetime.date.fromisoformat(e["hddFrom"]) > today:
                continue
            e["space"] = today.isoformat()
            used -= e["hddBytes"]
    return entries


def summary(entries, overrides, today, since):
    out = {"keep": 0, "temporary": 0, "leaving7d": 0, "next": None}
    for k, e in entries.items():
        st = rr.effective(e, override_for(overrides, k, e), today, since)
        out["keep" if st["keep"] else "temporary"] += 1
        if not st["keep"] and st["deleteOn"]:
            out["leaving7d"] += st["soon"]
            out["next"] = min(out["next"] or st["deleteOn"], st["deleteOn"])
    return out


def update_soon(entries, overrides, today, since, prev):
    """{key: {since, p}}: the first day each temporary title showed "Leaving soon"; gone when it stops, restarted when its postponement changes."""
    out = {}
    for k, e in entries.items():
        st = rr.effective(e, override_for(overrides, k, e), today, since)
        if st["keep"] or not st["soon"]:
            continue
        p, old = bool(e.get("postponed")), prev.get(k) if isinstance(prev, dict) else None
        keep_old = isinstance(old, dict) and old.get("p") == p and isinstance(old.get("since"), str)
        out[k] = {"since": old["since"] if keep_old else today.isoformat(), "p": p}
    return out


def document(entries, promote, now, switch, hdd, skipped, totals=None, problems=()):
    total, used = hdd
    return {"summary": totals, "updated": now.isoformat(timespec="seconds"), "deleteEnabled": switch.on, "enabledOn": switch.since.isoformat() if switch.since else None,
            "rules": {"ssdDays": rr.SSD_DAYS, "deleteDays": rr.DELETE_DAYS, "soonDays": rr.SOON_DAYS, "postponeMaxDays": rr.POSTPONE_MAX_DAYS, "graceDays": rr.GRACE_DAYS,
                      "hddStartPct": round(HDD_START * 100), "hddTargetPct": round(HDD_TARGET * 100)},
            "hdd": {"usedPct": round(100 * used / total, 1) if total else None}, "titles": entries, "promote": promote, "skipped": len(skipped), "problems": list(problems)}


def read_json(path):
    return json.loads(pathlib.Path(path).read_text())


def read_overrides(path):
    """({key | tvdb/<id>: keep}, problem or None). A missing store is fine unless its .bak exists (then it went missing)."""
    p = pathlib.Path(path)
    if not p.exists():
        return {}, ("keep.json missing while keep.json.bak exists" if p.with_name(p.name + ".bak").exists() else None)
    try:
        data = read_json(p)
    except (OSError, ValueError) as e:
        return {}, f"keep.json unreadable ({type(e).__name__})"
    if not isinstance(data, dict) or not isinstance(data.get("titles"), dict):
        return {}, "keep.json malformed"
    out = {}
    for k, v in data["titles"].items():
        if not rr.parse_key(k) or not isinstance(v, dict) or not isinstance(v.get("keep"), bool):
            continue
        out[k] = v["keep"]
        if k.startswith("tv/") and isinstance(v.get("tvdb"), int) and v["tvdb"] > 0:
            out.setdefault(f"tvdb/{v['tvdb']}", v["keep"])
    return out, None


def read_collections(path):
    """(keys in household collections, readable)."""
    try:
        data = read_json(path)
        lists = data["lists"]
        if not isinstance(lists, list):
            return set(), False
        out = set()
        for lst in lists:
            for t in (lst or {}).get("items") or []:
                if isinstance(t, dict) and t.get("k") in ("movie", "tv") and isinstance(t.get("id"), int) and t["id"] > 0:
                    out.add(rr.key(t["k"], t["id"]))
        return out, True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return set(), False


def read_gone(path):
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return []
    return [g for g in (data.get("gone") if isinstance(data, dict) else None) or [] if isinstance(g, dict) and rr.parse_key(g.get("key"))]


def record_gone(path, key, title, year, when, cap=GONE_CAP, hdd_bytes=0, tvdb=None):
    gone = [g for g in read_gone(path) if g["key"] != key]
    rec = {"key": key, "t": title, "y": year, "at": when.isoformat(), "hddBytes": hdd_bytes}
    if tvdb:
        rec["tvdb"] = tvdb
    write_atomic(path, {"gone": ([rec] + gone)[:cap]})


def recycle_pending(gone, today):
    """HDD bytes of our deletions of the last 7 days: Radarr/Sonarr keep them in the recycle bin until its 7-day cleanup."""
    limit = (today - datetime.timedelta(days=RECYCLE_DAYS)).isoformat()
    return sum(g.get("hddBytes") or 0 for g in gone if isinstance(g.get("at"), str) and g["at"] > limit)


def jellyfin_lists(entries, overrides, gone, today, since):
    """Own-list rows (Seerr Collections format) for Jellyfin's Collections & Trending: titles leaving within 7 days (real items) and titles whose files went (placeholders, requestable again), by Seerr's TMDB id."""
    leaving = []
    for k, e in entries.items():
        st = rr.effective(e, override_for(overrides, k, e), today, since)
        if not st["keep"] and st["soon"]:
            leaving.append((st["deleteOn"], k))
    rows = []
    for which, keys in (("leaving", [k for _, k in sorted(leaving)]), ("gone", [g["key"] for g in gone if rr.parse_key(g.get("key"))])):
        lid, name, query = JF_LISTS[which]
        items = [{"k": rr.parse_key(k)[0], "id": rr.parse_key(k)[1]} for k in dict.fromkeys(keys)]
        rows.append({"id": lid, "name": name, "ownerName": "the server", "query": query, "items": items[:100]})
    return {"updated": datetime.datetime.now(TZ).isoformat(timespec="seconds"), "lists": rows}


def in_night(now):
    return NIGHT[0] <= now.astimezone(TZ).time() < NIGHT[1]


def ensure_tag(call):
    for t in call("/tag"):
        if t.get("label") == DELETED_TAG:
            return t["id"]
    return call("/tag", "POST", {"label": DELETED_TAG})["id"]


@contextlib.contextmanager
def media_lock(path=LOCK_FILE, wait=LOCK_WAIT):
    """The shared media lock of the mover, the promoter and original-audio; yields False when it stays busy for `wait` s."""
    try:
        f = open(path, "a")
    except OSError:
        try:
            f = open(path)
        except OSError:
            yield False
            return
    with f:
        end = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    yield False
                    return
                time.sleep(5)
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def refusal(switch, now, night):
    today = now.astimezone(TZ).date()
    if not switch.on:
        return "observe mode (RETENTION_DELETE off)"
    if not in_night(now):
        return "deletion runs only in the night window 04:00-04:40"
    if not night.clock_ok:
        return "the clock is not NTP-synced"
    if switch.since > today + datetime.timedelta(days=1):
        return f"RETENTION_ENABLED_ON {switch.since} is in the future"
    if night.first_on and switch.since < night.first_on - datetime.timedelta(days=1):
        return f"RETENTION_ENABLED_ON {switch.since} is before the day the switch was first seen on ({night.first_on}): set it to the real day"
    if night.problems:
        return "data not trustworthy: " + "; ".join(night.problems)
    return None


def delete_due(titles, entries, overrides, now, switch, arr, log, night, cap=DELETE_CAP, space_cap=SPACE_CAP, gone_file=None):
    """Delete the media files of temporary titles whose date has come and that showed "Leaving soon" for 7 days. Returns (attempts, space-rule attempts)."""
    why = refusal(switch, now, night)
    if why:
        log(f"retention: {why} - nothing deleted")
        return 0, 0
    today, done, space = now.astimezone(TZ).date(), 0, 0
    space_ok = not (night.last.get("spaceDeleted") and night.hdd_used >= (night.last.get("hddUsed") or 0))
    if not space_ok:
        log("retention: the HDD did not drop after the last night's space deletions (recycle bin?) - no space-rule deletions tonight")
    soon_limit = (today - datetime.timedelta(days=SOON_DAYS_BEFORE_DELETE)).isoformat()
    for t in sorted(titles, key=lambda t: (t.imported, t.key)):
        e = entries.get(t.key)
        if not e or done >= cap or e.get("protected") or e.get("unmapped"):
            continue
        st = rr.effective(e, override_for(overrides, t.key, e), today, switch.since)
        if st["keep"] or not st["deleteOn"] or st["deleteOn"] > today.isoformat():
            continue
        s = night.soon.get(t.key)
        if not isinstance(s, dict) or not isinstance(s.get("since"), str) or s["since"] > soon_limit:
            continue
        if t.key in night.busy or (t.added_at and t.added_at > now - datetime.timedelta(hours=FRESH_HOURS)):
            log(f"retention: {t.key} ({t.title}) skipped: download or import in progress")
            continue
        is_space = st["why"] == "space"
        if is_space and (not space_ok or space >= space_cap):
            continue
        done += 1
        space += is_space
        call = arr("radarr" if t.kind == "movie" else "sonarr")
        step = "read"
        try:
            item = call(f"/{'movie' if t.kind == 'movie' else 'series'}/{t.id}")
            got = item.get("tmdbId") if t.kind == "movie" else item.get("tvdbId")
            want = t.tmdb if t.kind == "movie" else t.tvdb
            if not want or got != want:
                raise LookupError(f"fetched title id {got} does not match {want}")
            step = "unmonitor"
            tid = ensure_tag(call)
            item["monitored"] = False
            item["tags"] = sorted(set(item.get("tags") or []) | {tid})
            call(f"/{'movie' if t.kind == 'movie' else 'series'}/{t.id}", "PUT", item)
            log(f"retention: {t.key} ({t.title}) unmonitored and tagged {DELETED_TAG}")
            step = "delete files"
            if t.kind == "movie":
                fid = (item.get("movieFile") or {}).get("id")
                if fid:
                    call(f"/moviefile/{fid}", "DELETE")
            else:
                ids = [x["id"] for x in call(f"/episodefile?seriesId={t.id}") if x.get("id")]
                if ids:
                    call("/episodefile/bulk", "DELETE", {"episodeFileIds": ids})
            if gone_file:
                record_gone(gone_file, t.key, t.title, t.year, today, hdd_bytes=e.get("hddBytes") or 0, tvdb=t.tvdb)
            log(f"retention: {t.key} ({t.title}) files deleted to the 7-day recycle bin ({st['why']} {st['deleteOn']}); title kept")
        except Exception as ex:
            log(f"retention: {t.key} ({t.title}) failed at '{step}': {type(ex).__name__}: {ex} - run stopped")
            break
    return done, space


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


def seerr_media(call):
    rows, skip = [], 0
    while True:
        page = call(f"/media?take=100&skip={skip}&filter=all")
        results = (page or {}).get("results") or []
        rows += results
        skip += len(results)
        if not results or skip >= ((page.get("pageInfo") or {}).get("results") or 0):
            return rows


def queue_keys(apis, titles):
    """Keys of titles with an item in the Radarr/Sonarr queue."""
    ids = {("radarr", r.get("movieId")) for r in (apis.radarr("/queue?pageSize=1000") or {}).get("records") or []}
    ids |= {("sonarr", r.get("seriesId")) for r in (apis.sonarr("/queue?pageSize=1000") or {}).get("records") or []}
    return {t.key for t in titles if ("radarr" if t.kind == "movie" else "sonarr", t.id) in ids}


def clock_synced():
    try:
        return subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True, text=True, timeout=10).stdout.strip() == "yes"
    except (OSError, subprocess.SubprocessError):
        return False


def write_atomic(path, data, mode=0o644):
    path = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".retention-")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_state(path):
    try:
        data = read_json(path)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def human(n):
    return f"{n / GIB:.1f} GiB"


def plan_lines(doc, overrides, today):
    rows = []
    for k, e in doc["titles"].items():
        st = rr.effective(e, override_for(overrides, k, e), today, datetime.date.fromisoformat(doc["enabledOn"]) if doc["enabledOn"] else None)
        rows.append((st["deleteOn"] or "9999", k, e, st))
    out = []
    for _, k, e, st in sorted(rows):
        what = f"keep ({st['source']}: {'unmapped series' if e.get('unmapped') else ','.join(e['reasons']) or 'test film' if st['source'] != 'override' else 'override'})" if st["keep"] else f"delete {st['deleteOn']} ({st['why']})"
        out.append(f"  {what:38} {e['tier']:5} {human(e['size']):>10}  imported {e['imported']}  SSD until {e['ssdUntil']}  {k}  {e['t']} ({e['y']})" + (f"  postponed: {e['postponed']}" if e["postponed"] else ""))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Storage retention classifier (observe mode unless switched on)")
    ap.add_argument("--plan", action="store_true", help="print every title with its stage dates, write nothing")
    ap.add_argument("--dry-run", action="store_true", help="compute and log, write nothing")
    ap.add_argument("--apply", action="store_true", help="delete due titles' files (needs RETENTION_DELETE=1 and RETENTION_ENABLED_ON, night window only)")
    a = ap.parse_args(argv)
    env = os.environ
    say = lambda l: print(l, flush=True)  # noqa: E731
    now = datetime.datetime.now(TZ).replace(microsecond=0)
    try:
        hdd = hdd_usage(env.get("COLD", "/srv/.disks/hdd-cold"))
    except NotMounted as e:
        say(f"retention: {e} - nothing written, nothing deleted")
        return 1
    jf_key = pathlib.Path(env.get("RT_JF_KEY", "/home/buntu/Projects/Docker/Jellyfin/secrets/api-key")).read_text().strip()
    seerr_key = pathlib.Path(env.get("RT_SEERR_KEY", HERE.parent.parent / "Seerr" / "secrets" / "api-key")).read_text().strip()
    apis = Apis(api(env.get("RT_RADARR", "http://127.0.0.1:7878") + "/api/v3", {"X-Api-Key": arr_key(env.get("RT_RADARR_CONFIG", "/srv/appdata/radarr/config.xml"))}),
                api(env.get("RT_SONARR", "http://127.0.0.1:8989") + "/api/v3", {"X-Api-Key": arr_key(env.get("RT_SONARR_CONFIG", "/srv/appdata/sonarr/config.xml"))}),
                api(env.get("RT_JF", "http://127.0.0.1:8096"), {"Authorization": f"MediaBrowser Token={jf_key}"}))
    problems = []
    try:
        rows = seerr_media(api(env.get("RT_SEERR", "http://127.0.0.1:5055") + "/api/v1", {"X-Api-Key": seerr_key}))
    except (OSError, ValueError) as e:
        rows = []
        problems.append(f"Seerr media list unreadable ({type(e).__name__}): every series protected")
    lib = collect(apis, rows)
    try:
        progress = in_progress(apis.jf, lib.titles, now)
        plays = recent_plays(apis.jf, lib.titles, now - datetime.timedelta(hours=PROMOTE_HOURS))
        cov = coverage(apis.jf, lib.titles, now)
        if cov is not None and cov < COVERAGE_MIN:
            problems.append(f"Jellyfin knows only {100 * cov:.0f} % of the library files")
    except (OSError, ValueError, KeyError, TypeError, IndexError) as e:
        progress, plays = set(), {}
        problems.append(f"Jellyfin unreadable ({type(e).__name__})")
    hot = hot_videos([pathlib.Path(h) for h in env.get("HOT", "/srv/.disks/ssd-hot /srv/.disks/ssd-hot2").split()])
    overrides, keep_problem = read_overrides(env.get("RT_KEEP", "/srv/appdata/seerr-retention/keep.json"))
    if keep_problem:
        problems.append(keep_problem)
    collections, ok = read_collections(env.get("RT_COLLECTIONS", "/srv/appdata/seerr-collections/lists.json"))
    if not ok:
        problems.append("Collections store unreadable")
    state = pathlib.Path(env.get("RT_STATE", pathlib.Path.home() / ".local" / "state" / "movie-downloads"))
    gone = read_gone(state / "retention-gone.json")
    entries = classify(lib.titles, overrides, collections, progress, hot, hdd, now.date(), recycle_pending(gone, now.date()))
    promote = promotions(lib.titles, plays, hot)
    switch = Switch.from_env(env)
    doc = document(entries, promote, now, switch, hdd, lib.skipped, summary(entries, overrides, now.date(), switch.since), problems)
    if a.plan:
        print("\n".join(plan_lines(doc, overrides, now.date())))
        return 0
    sm = doc["summary"]
    say(f"retention: {len(entries)} titles, {sm['keep']} kept forever, {sm['temporary']} temporary, next would-delete {sm['next']}, HDD {doc['hdd']['usedPct']} %{'' if switch.on else ', observe mode'}")
    for p in problems:
        say(f"retention: WARNING {p} - deletion would be refused")
    for p in promote:
        say(f"retention: would promote {p['key']} ({p['title']}) seasons {p['seasons']} to the SSD (played {p['played']}); observe only, nothing moved")
    unmapped = [t.title for t in lib.titles if t.unmapped]
    if unmapped:
        say(f"retention: {len(unmapped)} series without a Seerr row protected: {', '.join(unmapped[:10])}")
    if lib.skipped:
        say(f"retention: {len(lib.skipped)} series without a usable TMDB id left out")
    if a.dry_run:
        return 0
    write_atomic(env.get("RT_OUT", "/srv/appdata/indexer-bar/retention.json"), doc)
    soon = update_soon(entries, overrides, now.date(), switch.since, read_state(state / "retention-soon.json"))
    write_atomic(state / "retention-soon.json", soon)
    night_file = state / "retention-night.json"
    last = read_state(night_file)
    first_on = None
    if switch.on:
        try:
            first_on = datetime.date.fromisoformat(last.get("firstOn") or "")
        except ValueError:
            first_on = now.date()
            write_atomic(night_file, {**last, "firstOn": first_on.isoformat()})
            last = read_state(night_file)
    if a.apply:
        try:
            busy = queue_keys(apis, lib.titles)
        except (OSError, ValueError) as e:
            busy = set()
            problems.append(f"Radarr/Sonarr queue unreadable ({type(e).__name__})")
        night = Night(problems, soon, busy, hdd[1], last, clock_synced(), first_on)
        arr = {"radarr": apis.radarr, "sonarr": apis.sonarr}
        with media_lock() as locked:
            if not locked:
                say("retention: the media lock stayed busy - nothing deleted")
                done, space = 0, 0
            else:
                done, space = delete_due(lib.titles, entries, overrides, now, switch, lambda app: arr[app], say, night, gone_file=state / "retention-gone.json")
        if done:
            write_atomic(night_file, {**last, "date": now.date().isoformat(), "hddUsed": hdd[1], "deleted": done, "spaceDeleted": space})
    if switch.on:
        write_atomic(state / "retention-lists.json", jellyfin_lists(entries, overrides, read_gone(state / "retention-gone.json"), now.date(), switch.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
