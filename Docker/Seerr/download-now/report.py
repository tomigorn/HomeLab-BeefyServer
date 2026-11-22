"""Problem reports (README "Problem reports"): report.holy-grail.ch, where household members report a broken film or episode and the server replaces it without an admin.
Runs inside download-now on its own port (RP_PORT), reached only through fastpi's Traefik: Authentik forward auth (app `report`, group streaming-users) sets X-authentik-username/-groups,
and Traefik adds X-Report-Secret, so a request without that secret did not pass the login and is refused. Admins = group streaming-admins: no daily limit, see every report and the file check.
A report is a record of type "report" in Manual interaction's store (manual.json): admins see it on the Manual page, a replacement is tracked there like a picked release (old file stays until the new one is imported).
What happens per reason: REASONS. Library = Radarr/Sonarr titles with a file (what Jellyfin shows and what can be replaced)."""
import json
import pathlib
import queue
import re
import secrets
import threading
import time
import urllib.parse

import manual

NOW = time.time
LOCAL = time.localtime
DAILY = 2
NOTE_MAX = 300
SEARCH_MAX = 20
BROWSE_PAGE = 50
BROWSE_SORTS = ("az", "year", "added")
LIB_TTL = 600
QUEUE_TTL = 15
RELEASE_TIMEOUT = 300  # an interactive search of every indexer (FlareSolverr ones included) can take minutes
RETRY_WAIT = 60
SETTLE = (10, 9)  # after a grab: look for its download every 10 s, 9 times
USERS, ADMINS = "streaming-users", "streaming-admins"
PROTECTED_TMDB = {653}  # Nosferatu (1922): the permanent test film, never replaced automatically
REASONS = {"start": "replace", "video": "replace", "audio": "replace", "wrong": "replace", "quality": "better",
           "sound": "language", "language": "language", "subtitles": "subtitles", "other": "review"}
REF = re.compile(r"^(radarr|sonarr):([1-9][0-9]{0,8})$")
RID = re.compile(r"^[0-9a-f]{12}$")
SOURCE_RANK = {"unknown": 0, "cam": 0, "telesync": 0, "telecine": 0, "workprint": 0, "dvd": 1, "tv": 2, "television": 2, "televisionraw": 2,
               "webrip": 3, "web": 4, "webdl": 4, "bluray": 5, "blurayraw": 6}
WANTED_ALWAYS = {"german", "english"}
OPEN_STATES = {"queued", "checking", "searching", "nothing-found", "language-ok", "review", "dry-run"}
REPLACE_STATUS = {"grabbed": "downloading", "downloading": "downloading", "importing": "importing", "done": "imported", "failed": "failed", "searching": "searching"}
PUBLIC = ("id", "title", "year", "kind", "reason", "note", "user", "season", "episode", "episodeTitle", "status", "result", "at", "progress", "release")
LIB = {}
QUEUE_CACHE = {}
LIB_LOCK = threading.Lock()
CREATE_LOCK = threading.Lock()
WWW = pathlib.Path(__file__).resolve().parent / "report-www"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/report.js": ("report.js", "text/javascript; charset=utf-8"),
          "/report.css": ("report.css", "text/css; charset=utf-8"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}


class Refused(Exception):
    def __init__(self, status, error, **extra):
        super().__init__(error)
        self.status, self.error, self.extra = status, error, extra


# ---------- who is asking ----------

def caller(headers, deps):
    """(user, admin) from the forward-auth headers, only when Traefik's shared secret came with them."""
    h = {k.lower(): v for k, v in headers.items()}
    want = getattr(deps, "report_secret", None)
    got = h.get("x-report-secret", "")
    if not want or not isinstance(got, str) or not secrets.compare_digest(got.encode(), want.encode()):
        raise Refused(403, "forbidden")
    user = str(h.get("x-authentik-username") or "").strip()
    groups = {g.strip() for g in str(h.get("x-authentik-groups") or "").split("|")}
    if not user or len(user) > 64 or USERS not in groups and ADMINS not in groups:
        raise Refused(403, "forbidden")
    return user, ADMINS in groups


def day_of(t):
    return time.strftime("%Y-%m-%d", LOCAL(t))


def reports(deps):
    with manual.STORE_LOCK:
        try:
            return {k: v for k, v in manual.load(deps).items() if isinstance(v, dict) and v.get("type") == "report"}
        except manual.StoreUnreadable:
            return {}


def used_today(deps, user):
    today = day_of(NOW())
    return sum(1 for r in reports(deps).values() if (r.get("details") or {}).get("user") == user and day_of(manual.epoch(r.get("firstSeen")) or 0) == today)


# ---------- library ----------

def library(deps, app):
    with LIB_LOCK:
        hit = LIB.get(app)
        if hit and NOW() - hit[0] < LIB_TTL:
            return hit[1]
    got = deps.arr(app)("/movie" if app == "radarr" else "/series")
    items = [t for t in got if isinstance(t, dict) and manual.is_int(t.get("id"))] if isinstance(got, list) else []
    if app == "radarr":
        items = [t for t in items if t.get("hasFile")]
    else:
        items = [t for t in items if ((t.get("statistics") or {}).get("episodeFileCount") or 0) > 0]
    with LIB_LOCK:
        LIB[app] = (NOW(), items)
    return items


def names(t):
    out = [t.get("title"), t.get("originalTitle")] + [a.get("title") for a in t.get("alternateTitles") or [] if isinstance(a, dict)]
    return [manual.norm(n) for n in out if n]


def brief(app, t):
    return {"ref": f"{app}:{t['id']}", "kind": "movie" if app == "radarr" else "tv", "title": str(t.get("title") or "")[:200], "year": t.get("year")}


def search(deps, term):
    q = manual.norm(term)
    if not q or len(term) > 100:
        raise Refused(400, "bad request")
    words = q.split()
    hits = []
    for app in ("radarr", "sonarr"):
        for t in library(deps, app):
            ns = names(t)
            if not any(all(w in n for w in words) for n in ns):
                continue
            exact = any(n == q for n in ns)
            start = any(n.startswith(q) for n in ns)
            hits.append(((not exact, not start, manual.norm(t.get("title"))), brief(app, t)))
    hits.sort(key=lambda x: x[0])
    return [b for _, b in hits[:SEARCH_MAX]]


def browse(deps, user, kind, sort, page):
    """One page of the library (films or series with a file) for the browse list: the same titles as the search."""
    if kind not in ("movie", "tv") or not page.isdigit() or not 1 <= int(page) <= 10 ** 6:
        raise Refused(400, "bad request")
    app = "radarr" if kind == "movie" else "sonarr"
    seen = deps.watched(user) if callable(getattr(deps, "watched", None)) else None
    sorts = list(BROWSE_SORTS) + (["watched"] if seen is not None else [])
    if sort not in sorts:
        raise Refused(400, "bad request")
    items = library(deps, app)
    key = lambda t: manual.norm(t.get("title"))
    if sort == "az":
        items = sorted(items, key=key)
    elif sort == "year":
        items = sorted(items, key=key)
        items.sort(key=lambda t: t.get("year") or 0, reverse=True)
    elif sort == "added":
        items = sorted(items, key=key)
        items.sort(key=lambda t: str(t.get("added") or ""), reverse=True)
    else:
        by = {f"{app}:{t['id']}": t for t in items}
        items = [by[r] for r in seen if r in by]
    total, n = len(items), int(page)
    lo = (n - 1) * BROWSE_PAGE
    return {"items": [brief(app, t) for t in items[lo:lo + BROWSE_PAGE]], "total": total, "page": n, "pages": max(1, -(-total // BROWSE_PAGE)), "sorts": sorts}


def poster(deps, ref):
    """The small cover of a library title from its Radarr/Sonarr (MediaCover), for the browse list."""
    app, t = title_of(deps, ref)
    try:
        return deps.arr_image(app, f"/MediaCover/{int(t['id'])}/poster-250.jpg")
    except Exception:
        raise Refused(404, "not found") from None


def title_of(deps, ref):
    m = REF.match(str(ref or ""))
    if not m:
        raise Refused(400, "bad request")
    app, tid = m.group(1), int(m.group(2))
    t = next((x for x in library(deps, app) if x["id"] == tid), None)
    if t is None:
        raise Refused(400, "bad request")
    return app, t


def episodes(deps, t):
    got = deps.arr("sonarr")(f"/episode?seriesId={int(t['id'])}")
    return [e for e in got if isinstance(e, dict) and e.get("hasFile") and manual.is_int(e.get("id"))] if isinstance(got, list) else []


def seasons(deps, ref):
    app, t = title_of(deps, ref)
    if app != "sonarr":
        raise Refused(400, "bad request")
    by = {}
    for e in sorted(episodes(deps, t), key=lambda e: (e.get("seasonNumber") or 0, e.get("episodeNumber") or 0)):
        by.setdefault(e.get("seasonNumber") or 0, []).append({"id": e["id"], "episode": e.get("episodeNumber"), "title": str(e.get("title") or "")[:200]})
    return [{"season": s, "episodes": eps} for s, eps in sorted(by.items())]


def resolve(deps, q):
    """A Jellyfin item (its TMDB/TVDB ids, season and episode number) as a library title and episode: the "Report a problem" link pre-selects it."""
    kind = (q.get("type") or [""])[0]
    ids = {k: (q.get(k) or [""])[0] for k in ("tmdb", "tvdb", "season", "episode")}
    if kind not in ("movie", "tv") or any(v and not v.isdigit() for v in ids.values()):
        raise Refused(400, "bad request")
    app = "radarr" if kind == "movie" else "sonarr"
    t = next((x for x in library(deps, app) if ids["tmdb"] and str(x.get("tmdbId")) == ids["tmdb"] or ids["tvdb"] and kind == "tv" and str(x.get("tvdbId")) == ids["tvdb"]), None)
    if t is None:
        raise Refused(404, "not in the library")
    out = dict(brief(app, t), episodeId=None)
    if kind == "tv" and ids["season"] and ids["episode"]:
        ep = next((e for e in episodes(deps, t) if str(e.get("seasonNumber")) == ids["season"] and str(e.get("episodeNumber")) == ids["episode"]), None)
        out["episodeId"] = ep["id"] if ep else None
    return out


# ---------- creating a report ----------

def item_key(app, tid, episode):
    return f"{app}:{tid}" + (f"#{episode}" if episode else "")


def is_open(r):
    return r.get("resolved") is None


def create(dn, deps, user, admin, req):
    reason, note = req.get("reason"), req.get("note") or ""
    if reason not in REASONS or not isinstance(note, str) or len(note) > NOTE_MAX or reason == "other" and not note.strip():
        raise Refused(400, "bad request")
    app, t = title_of(deps, req.get("ref"))
    ep = None
    if app == "sonarr":
        eid = req.get("episodeId")
        ep = next((e for e in episodes(deps, t) if e["id"] == eid), None) if manual.is_int(eid) else None
        if ep is None:
            raise Refused(400, "bad request")
    now = NOW()
    item = item_key(app, t["id"], ep and ep["id"])
    with CREATE_LOCK:
        mine = reports(deps)
        same = next((r for r in mine.values() if is_open(r) and (r.get("details") or {}).get("item") == item), None)
        if same:
            raise Refused(409, "already reported", id=same["id"])
        if not admin and sum(1 for r in mine.values() if (r.get("details") or {}).get("user") == user and day_of(manual.epoch(r.get("firstSeen")) or 0) == day_of(now)) >= DAILY:
            raise Refused(429, "limit")
        ident = f"report|{user}|{manual.ts(now)}|{secrets.token_hex(4)}"
        pid = manual.pid_of("report", f"{app}:{t['id']}", ident)
        details = {"reason": "report", "report": reason, "note": note.strip(), "user": user, "item": item, "kind": "movie" if app == "radarr" else "tv",
                   "problems": []}
        if ep:
            details.update(season=ep.get("seasonNumber"), episode=ep.get("episodeNumber"), episodeId=ep["id"], episodeTitle=str(ep.get("title") or "")[:200])
        rec = {"id": pid, "type": "report", "key": f"{app}:{t['id']}", "ident": ident, "title": str(t.get("title") or "")[:200], "year": t.get("year"),
               "requesters": [user], "details": details, "firstSeen": manual.ts(now), "lastSeen": manual.ts(now), "resolved": None,
               "actions": [{"at": manual.ts(now), "user": user, "userId": None, "action": "report", "result": reason + (": " + note.strip() if note.strip() else "")}],
               "report": {"status": "queued", "result": None}}
        with manual.STORE_LOCK:
            records = manual.load(deps)
            records[pid] = rec
            manual.save(deps, records)
    print(f"report: {user} {reason} {item} ({rec['title']})", flush=True)
    KICK(dn, deps, pid)
    return pid


# ---------- doing what the reason asks ----------

def update(deps, pid, status=None, result=None, resolve=None, details=None, replace=None, action=None):
    with manual.STORE_LOCK:
        records = manual.load(deps)
        r = records.get(pid)
        if r is None:
            return None
        rep = r.setdefault("report", {})
        if status:
            rep["status"] = status
        if result is not None:
            rep["result"] = result
        if details:
            r.setdefault("details", {}).update(details)
        if replace:
            r["replace"] = replace
        if action:
            r.setdefault("actions", []).append({"at": manual.ts(NOW()), "user": None, "userId": None, "action": action, "result": result})
        if resolve:
            r["resolved"] = {"at": manual.ts(NOW()), "how": resolve}
        r["lastSeen"] = manual.ts(NOW())
        manual.save(deps, records)
        return r


def current_file(arr, app, t, episode_id):
    """(file id, path, quality dict, audio languages) of the reported film or episode, None when it has no file."""
    if app == "radarr":
        mf = t.get("movieFile") if t.get("hasFile") else None
        if not isinstance(mf, dict) or not manual.is_int(mf.get("id")):
            return None
    else:
        ep = arr(f"/episode/{int(episode_id)}")
        if not isinstance(ep, dict) or not ep.get("hasFile") or not manual.is_int(ep.get("episodeFileId")):
            return None
        mf = arr(f"/episodefile/{int(ep['episodeFileId'])}")
        if not isinstance(mf, dict):
            return None
        mf = dict(mf, id=ep["episodeFileId"])
    audio = [a.strip() for a in str((mf.get("mediaInfo") or {}).get("audioLanguages") or "").replace(",", "/").split("/") if a.strip()]
    return {"id": mf["id"], "path": mf.get("path"), "quality": (mf.get("quality") or {}).get("quality") or {}, "audio": audio, "scene": mf.get("sceneName")}


def grab_of(arr, app, t, episode_id, f):
    """The grab the current file was imported from: (history id, release name) or (None, scene name)."""
    path = f"/history/movie?movieId={int(t['id'])}" if app == "radarr" else f"/history/series?seriesId={int(t['id'])}"
    got = arr(path)
    hist = got.get("records") if isinstance(got, dict) else got
    hist = sorted((h for h in hist or [] if isinstance(h, dict)), key=lambda h: h.get("date") or "", reverse=True)
    imp = next((h for h in hist if h.get("eventType") in manual.IMPORT_EVENTS and h.get("downloadId") and str((h.get("data") or {}).get("fileId")) == str(f["id"])
                and (app == "radarr" or h.get("episodeId") == episode_id)), None)
    if imp:
        g = next((h for h in hist if h.get("eventType") == "grabbed" and str(h.get("downloadId") or "").lower() == str(imp["downloadId"]).lower() and manual.is_int(h.get("id"))), None)
        if g:
            return g["id"], str(g.get("sourceTitle") or ""), imp["downloadId"]
    return None, str(f.get("scene") or ""), None


def wanted(t):
    orig = str((t.get("originalLanguage") or {}).get("name") or "").lower()
    return WANTED_ALWAYS | ({orig} if orig else set())


def better(x, q):
    """A release in higher quality than the current file: higher resolution, or the same with a better source."""
    rq = ((x.get("quality") or {}).get("quality") or {})
    r1, r0 = rq.get("resolution") or 0, q.get("resolution") or 0
    if r1 != r0:
        return r1 > r0
    norm = lambda s: str(s or "").lower().replace("-", "").replace("_", "")
    return SOURCE_RANK.get(norm(rq.get("source")), 0) > SOURCE_RANK.get(norm(q.get("source")), 0)


def pick(arr, app, t, episode, season, current, f, mode, want):
    """The release to grab and whether it is a season pack: the arr's own order (not rejected, custom format score, seeders); current/blocked release never."""
    def candidates(path, job):
        try:
            got = arr(path, timeout=RELEASE_TIMEOUT)
        except Exception as e:
            print(f"report: release search {path.split('?')[0]} failed ({type(e).__name__}), once more in {RETRY_WAIT} s", flush=True)
            time.sleep(RETRY_WAIT)
            got = arr(path, timeout=RELEASE_TIMEOUT)
        data = manual.release_data(got, job)
        raw = {x.get("guid"): x for x in got if isinstance(x, dict)} if isinstance(got, list) else {}
        same = manual.norm(current)
        # the current release under another spelling (other indexer, other case or separators) is the same file
        return [(i, raw.get(i["guid"]) or {}) for i in data["items"] if not i["rejected"] and not i["current"] and not (same and manual.norm(i["title"]) == same)]

    def ok(x):
        if mode == "better" and not better(x, f["quality"]):
            return False
        if mode == "language" and not any(str(lang.get("name") or "").lower() in want for lang in x.get("languages") or [] if isinstance(lang, dict)):
            return False
        return True
    def order(cands):
        """The arr's order, but a release with at least the current file's resolution first, unknown quality last."""
        cur = (f["quality"] or {}).get("resolution") or 0

        def bucket(c):
            res = (((c[1].get("quality") or {}).get("quality") or {}).get("resolution")) or 0
            return 2 if not res else 0 if res >= cur else 1
        return sorted(cands, key=bucket)
    job = {"kind": "movie" if app == "radarr" else "tv", "arrId": t["id"], "season": season, "current": current, "runtime": t.get("runtime"), "title": t.get("title")}
    if app == "radarr":
        got = order([c for c in candidates(f"/release?movieId={int(t['id'])}", job) if ok(c[1])])
        return (got[0][0], False) if got else (None, False)
    got = order([c for c in candidates(f"/release?episodeId={int(episode)}", job) if ok(c[1])])
    if got:
        return got[0][0], False
    packs = order([c for c in candidates(f"/release?seriesId={int(t['id'])}&seasonNumber={int(season)}", job) if c[1].get("fullSeason") and ok(c[1])])
    return (packs[0][0], True) if packs else (None, False)


def tag_now(arr, app, t):
    """Daytime, like Download now: the title's grabs go to the normal (not night) categories."""
    tid = next((x["id"] for x in arr("/tag") if isinstance(x, dict) and x.get("label") == "download-now"), None)
    if tid is None or tid in (t.get("tags") or []):
        return
    if app == "radarr":
        arr("/movie/editor", "PUT", {"movieIds": [t["id"]], "tags": [tid], "applyTags": "add"})
    else:
        arr("/series/editor", "PUT", {"seriesIds": [t["id"]], "tags": [tid], "applyTags": "add"})


def release_night(dn, deps, app, t):
    """Download now's own step (download_now.plan/apply): the title's downloads leave the night categories and paused ones start, so the replacement comes now, not at night."""
    if getattr(deps, "qbit", None) is None or getattr(deps, "sab", None) is None:
        return 0
    kind = "movie" if app == "radarr" else "tv"
    arr = deps.arr(app)
    key = "movieId" if kind == "movie" else "seriesId"
    wait, tries = SETTLE
    for _ in range(tries):
        time.sleep(wait)
        got = arr("/queue?page=1&pageSize=1000")
        records = got.get("records") or [] if isinstance(got, dict) else []
        if not any(r.get(key) == t["id"] and r.get("downloadId") for r in records):
            continue
        tag_id = next((x["id"] for x in arr("/tag") if isinstance(x, dict) and x.get("label") == dn.TAG), None)
        torrents = deps.qbit("/torrents/info")
        slots = deps.sab({"mode": "queue", "limit": "1000"})["queue"]["slots"]
        p = dn.plan(kind, t, tag_id, records, torrents, slots)
        p.update(app=app, title_id=t["id"], tag_id=tag_id, search=False, add_tag=p["add_tag"] and tag_id is not None)
        dn.apply(kind, p, deps)
        return len(p["qbit"]) + len(p["sab"])
    return 0


def run_check(deps, pid, path):
    """The download quality check on the old file (timestamp jumps, missing streams): recorded for admins, decides nothing."""
    fc = getattr(deps, "file_check", None)
    if not fc or not path:
        return
    try:
        res = fc(path)
    except Exception as e:
        res = {"verdict": "error", "reasons": [], "notes": [], "why": type(e).__name__}
    res = {"verdict": res.get("verdict"), "reasons": manual.clean_lines(res.get("reasons") or []), "notes": manual.clean_lines(res.get("notes") or []),
           "why": res.get("why"), "at": manual.ts(NOW())}
    lines = res["reasons"] + res["notes"] + ([f"file check failed: {res['why']}"] if res.get("why") else []) or ["file check: no fault found"]
    update(deps, pid, details={"fileCheck": res, "problems": lines})
    print(f"report {pid}: file check {res['verdict']}", flush=True)


def start_check(deps, pid, path):
    threading.Thread(target=run_check, args=(deps, pid, path), daemon=True).start()


CHECK = start_check


def bazarr_search(deps, app, t):
    b = getattr(deps, "bazarr", None)
    if b is None:
        return False
    b("/api/movies?radarrid=%d&action=search-missing" % t["id"] if app == "radarr" else "/api/series?seriesid=%d&action=search-missing" % t["id"], "PATCH")
    return True


def process(dn, deps, pid):
    try:
        _process(dn, deps, pid)
    except Exception as e:
        print(f"report {pid}: {type(e).__name__} {dn.redact(e)}", flush=True)
        update(deps, pid, status="review", result="could not finish automatically, an admin will look at it", action="report-error")


def _process(dn, deps, pid):
    with manual.STORE_LOCK:
        r = manual.load(deps).get(pid)
    if not r or (r.get("report") or {}).get("status") != "queued":
        return
    x = r["details"]
    mode = REASONS[x["report"]]
    update(deps, pid, status="checking")
    app, tid = r["key"].split(":")
    arr = deps.arr(app)
    t = arr(f"/{'movie' if app == 'radarr' else 'series'}/{int(tid)}")
    if mode == "review":
        return update(deps, pid, status="review", result="an admin will look at it")
    if t.get("tmdbId") in PROTECTED_TMDB:
        return update(deps, pid, status="review", result="test film: no automatic change, an admin will look at it")
    f = current_file(arr, app, t, x.get("episodeId"))
    if f is None:
        return update(deps, pid, status="review", result="no file in the library, an admin will look at it")
    dry = bool(getattr(deps, "report_dry_run", False))
    if mode == "subtitles":
        if dry:
            return update(deps, pid, status="dry-run", result="would ask Bazarr to search subtitles again")
        if not bazarr_search(deps, app, t):
            return update(deps, pid, status="review", result="subtitle search not available, an admin will look at it")
        return update(deps, pid, status="subtitles", result="subtitles are searched again", resolve="subtitles", action="subtitles")
    want = wanted(t)
    if mode == "language":
        have = [a for a in f["audio"] if a.lower() in want]
        update(deps, pid, details={"audio": f["audio"][:12], "original": (t.get("originalLanguage") or {}).get("name")})
        if have or not f["audio"]:
            return update(deps, pid, status="language-ok", result="the file has " + (", ".join(have) if have else "audio in an unknown language") + "; an admin will look at it")
    if mode != "better":
        CHECK(deps, pid, f["path"])
    grab_id, current, old_dl = grab_of(arr, app, t, x.get("episodeId"), f)
    update(deps, pid, status="searching", details={"release": current[:200]})
    if mode != "better" and grab_id and not dry:
        try:
            arr(f"/history/failed/{int(grab_id)}", "POST")
        except Exception as e:
            # e.g. already marked failed by an earlier report: the release is blocklisted either way
            print(f"report {pid}: block of {current} refused ({type(e).__name__} {dn.redact(e)}), going on", flush=True)
    found, pack = pick(arr, app, t, x.get("episodeId"), x.get("season"), current, f, mode, want)
    if found is None:
        if mode == "better":
            return update(deps, pid, status="nothing-better", result="no better version found", resolve="nothing-better", action="nothing-better")
        return update(deps, pid, status="nothing-found", result=("current release blocked; " if grab_id and not dry else "") + "no other version found yet, an admin will look at it")
    if dry:
        return update(deps, pid, status="dry-run", result=f"would block {current or 'the current file'} and grab {found['title']}" + (" (season pack)" if pack else ""))
    tag_now(arr, app, t)
    arr("/release", "POST", {"guid": found["guid"], "indexerId": found["indexerId"]})
    eps = [x["episodeId"]] if app == "sonarr" else []
    o = {"at": manual.ts(NOW()), "user": x.get("user"), "userId": None, "app": app, "kind": "movie" if app == "radarr" else "tv", "arrId": t["id"], "from": current,
         "downloadId": old_dl, "episodeIds": eps, "season": x.get("season"), "runtime": t.get("runtime"), "downloadIds": [], "lastResult": None,
         "mode": "replace", "release": found["title"], "state": "grabbed", "pack": pack}
    if eps:
        o["only"] = eps
    result = f"grabbed {found['title']}" + (" (season pack, only this episode is imported)" if pack else "") + "; the current file stays until it is imported"
    update(deps, pid, status="downloading", result=result, replace=o, action="replace")
    print(f"report {pid} ({r.get('title')}): {result}", flush=True)
    try:
        moved = release_night(dn, deps, app, t)
        print(f"report {pid}: {moved} download(s) moved out of the night categories", flush=True)
    except Exception as e:
        print(f"report {pid}: night release failed ({type(e).__name__} {dn.redact(e)}), it downloads at night", flush=True)


# ---------- worker ----------

JOBS = queue.Queue()


def worker(dn, deps):
    while True:
        pid = JOBS.get()
        process(dn, deps, pid)


def kick(dn, deps, pid):
    JOBS.put(pid)


KICK = kick


def start(dn, deps):
    """The worker thread; reports left queued by a restart are picked up again."""
    threading.Thread(target=worker, args=(dn, deps), daemon=True).start()
    for pid, r in reports(deps).items():
        if (r.get("report") or {}).get("status") in ("queued", "checking", "searching") and not r.get("replace"):
            update(deps, pid, status="queued")
            JOBS.put(pid)


# ---------- status ----------

def queue_of(deps, app):
    hit = QUEUE_CACHE.get(app)
    if hit and NOW() - hit[0] < QUEUE_TTL:
        return hit[1]
    try:
        got = deps.arr(app)("/queue?page=1&pageSize=1000")
        recs = got.get("records") or [] if isinstance(got, dict) else []
    except Exception:
        recs = []
    QUEUE_CACHE[app] = (NOW(), recs)
    return recs


def progress(deps, o):
    field = "movieId" if o.get("kind") == "movie" else "seriesId"
    ids = set(o.get("downloadIds") or [])
    own = [q for q in queue_of(deps, o["app"]) if isinstance(q, dict) and q.get(field) == o.get("arrId")
           and (str(q.get("downloadId") or "").lower() in ids or str(q.get("title") or "").lower() == str(o.get("release") or "").lower())]
    size = sum(q.get("size") or 0 for q in own)
    left = sum(q.get("sizeleft") or 0 for q in own)
    return round(100 * (size - left) / size) if size else None


def public(deps, r, admin):
    x, rep, o = r.get("details") or {}, r.get("report") or {}, r.get("replace") if isinstance(r.get("replace"), dict) else None
    status = REPLACE_STATUS.get(o.get("state"), rep.get("status")) if o else rep.get("status")
    if r.get("resolved") and r["resolved"].get("how") == "dismiss":
        status = "closed"
    out = {"id": r["id"], "title": r.get("title"), "year": r.get("year"), "kind": x.get("kind"), "reason": x.get("report"), "note": x.get("note"), "user": x.get("user"),
           "season": x.get("season"), "episode": x.get("episode"), "episodeTitle": x.get("episodeTitle"), "status": status,
           "result": (o.get("lastResult") if o and o.get("state") in ("done", "failed") and o.get("lastResult") else rep.get("result")), "at": r.get("firstSeen"),
           "progress": progress(deps, o) if o and status == "downloading" else None, "release": o.get("release") if o else None}
    out = {k: out.get(k) for k in PUBLIC}
    if admin:
        out["fileCheck"] = x.get("fileCheck")
        out["current"] = x.get("release")
    return out


def view(deps, user, admin, rid=None):
    rs = sorted(reports(deps).values(), key=lambda r: r.get("firstSeen") or "", reverse=True)
    rs = [r for r in rs if admin or (r.get("details") or {}).get("user") == user]
    if rid is not None:
        r = next((r for r in rs if r["id"] == rid), None)
        if r is None:
            raise Refused(404, "not found")
        return public(deps, r, admin)
    return {"reports": [public(deps, r, admin) for r in rs[:100]]}


# ---------- HTTP ----------

def static(path):
    name, ctype = STATIC[path]
    try:
        return 200, ctype, (WWW / name).read_bytes()
    except OSError:
        return 404, "application/json", b'{"error":"not found"}'


def handle(dn, method, path, headers, body, deps):
    """(status, content type, body bytes)."""
    url = urllib.parse.urlsplit(path)
    if url.path == "/health" and method == "GET":
        return 200, "application/json", b'{"ok":true}'
    try:
        user, admin = caller(headers, deps)
        if method == "GET" and url.path in STATIC:
            return static(url.path)
        q = urllib.parse.parse_qs(url.query)
        if method == "GET" and url.path == "/api/poster":
            return 200, "image/jpeg", poster(deps, (q.get("ref") or [""])[0])
        out = route(dn, deps, user, admin, method, url.path, q, headers, body)
        return 200, "application/json", json.dumps(out).encode()
    except Refused as e:
        return e.status, "application/json", json.dumps({"error": e.error, **e.extra}).encode()
    except Exception as e:
        print(f"report {url.path}: {type(e).__name__} {dn.redact(e)}", flush=True)
        return 502, "application/json", b'{"error":"not reachable"}'


def route(dn, deps, user, admin, method, path, q, headers, body):
    if method == "POST" and path == "/api/report":
        h = {k.lower(): v for k, v in headers.items()}
        if h.get("origin") != getattr(deps, "report_origin", None):
            raise Refused(403, "origin")
        if h.get("content-type", "").split(";")[0].strip() != "application/json":
            raise Refused(415, "json only")
        try:
            req = json.loads(body or b"{}")
        except (ValueError, RecursionError):
            raise Refused(400, "bad request") from None
        if not isinstance(req, dict):
            raise Refused(400, "bad request")
        return {"id": create(dn, deps, user, admin, req)}
    if method != "GET":
        raise Refused(404, "not found")
    if path == "/api/me":
        return {"user": user, "admin": admin, "left": None if admin else max(0, DAILY - used_today(deps, user)), "daily": DAILY}
    if path == "/api/search":
        return {"results": search(deps, (q.get("q") or [""])[0])}
    if path == "/api/browse":
        return browse(deps, user, (q.get("kind") or [""])[0], (q.get("sort") or ["az"])[0], (q.get("page") or ["1"])[0])
    if path == "/api/episodes":
        return {"seasons": seasons(deps, (q.get("ref") or [""])[0])}
    if path == "/api/resolve":
        return resolve(deps, q)
    if path == "/api/reports":
        return view(deps, user, admin)
    if path == "/api/report":
        rid = (q.get("id") or [""])[0]
        if not RID.match(rid):
            raise Refused(400, "bad request")
        return view(deps, user, admin, rid)
    raise Refused(404, "not found")


def serve(dn, deps, port):
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "report"
        sys_version = ""
        timeout = 15

        def _serve(self, method):
            try:
                n = max(0, min(int(self.headers.get("Content-Length") or 0), 16384))
            except ValueError:
                n = 0
            body = self.rfile.read(n) if n else b""
            status, ctype, data = handle(dn, method, self.path, dict(self.headers.items()), body, deps)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "private, max-age=86400" if status == 200 and ctype.startswith("image/") else "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._serve("GET")

        def do_POST(self):
            self._serve("POST")

        def log_message(self, fmt, *args):
            pass

    start(dn, deps)
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"report listening on {port}{' (dry run)' if getattr(deps, 'report_dry_run', False) else ''}", flush=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
