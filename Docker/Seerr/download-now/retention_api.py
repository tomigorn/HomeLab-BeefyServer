"""Storage retention backend (README "Storage retention"): /_download-now/retention/ — title state for the title page and the request dialog, a batch for request cards, the would-delete list for managers, and the "Keep forever" toggle.
Data: retention.json written by Movie-Downloads' scripts/retention.py (read-only mount), the keep store keep.json (this backend's, 65127:buntu 2750, read by the classifier). Rules: retention_rules.py.
Who: any Seerr session reads; keep = ADMIN or a request right; back to temporary = a requester of the title or ADMIN / MANAGE_REQUESTS; the test film never changes. POST only with Origin + JSON."""
import datetime
import json
import os
import pathlib
import tempfile
import threading
import time
import urllib.parse

import retention_rules as rr

ADMIN, MANAGE, REQUEST, REQUEST_MOVIE, REQUEST_TV = 2, 16, 32, 262144, 524288
BODY_MAX = 1024
BATCH_MAX = 100
MIN_GAP = 3
PER_HOUR = 30
RECENT = {}
LOOKUPS = {}
LOOKUP_TTL = 600
LOOKUP_MAX = 500
LOCK = threading.Lock()
now_ts = time.time


class StoreUnreadable(Exception):
    pass


def perms(me):
    p = me.get("permissions") if isinstance(me, dict) else None
    return p if isinstance(p, int) and not isinstance(p, bool) else 0


def may_keep(me):
    return bool(perms(me) & (ADMIN | REQUEST | REQUEST_MOVIE | REQUEST_TV))


def manager(me):
    return bool(perms(me) & (ADMIN | MANAGE))


def read_doc(path):
    try:
        data = json.loads(pathlib.Path(path).read_text())
        return data if isinstance(data, dict) and isinstance(data.get("titles"), dict) else {"titles": {}}
    except (OSError, ValueError, TypeError):
        return {"titles": {}}


def read_store(path):
    p = pathlib.Path(path)
    if not p.exists():
        return {"titles": {}}
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise StoreUnreadable(type(e).__name__) from None
    if not isinstance(data, dict) or not isinstance(data.get("titles"), dict):
        raise StoreUnreadable("shape")
    return data


def overrides(path):
    try:
        titles = read_store(path)["titles"]
    except StoreUnreadable:
        return {}
    return {k: v["keep"] for k, v in titles.items() if isinstance(v, dict) and isinstance(v.get("keep"), bool)}


def write_store(path, data):
    p = pathlib.Path(path)
    if p.exists():
        try:
            os.replace(p, p.with_name(p.name + ".bak"))
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".keep-")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.chmod(tmp, 0o640)
    os.replace(tmp, p)


def enabled_on(doc):
    try:
        return datetime.date.fromisoformat(doc["enabledOn"]) if doc.get("deleteEnabled") and doc.get("enabledOn") else None
    except (TypeError, ValueError):
        return None


def card(entry, override, today, since):
    st = rr.effective(entry, override, today, since)
    return {k: st[k] for k in ("state", "keep", "source", "deleteOn", "soon", "overdue", "postponed", "why")}


def year_of(media, kind):
    s = (media or {}).get("releaseDate" if kind == "movie" else "firstAirDate") or ""
    return int(s[:4]) if s[:4].isdigit() else None


def lookup_default(dn, deps, kind, tmdb, media, today):
    """reasons for a title not in the library yet: Radarr/Sonarr lookup (IMDb rating, votes, year; cached 10 min), else the year from Seerr."""
    k, t = rr.key(kind, tmdb), now_ts()
    hit = LOOKUPS.get(k)
    if hit and t - hit[0] < LOOKUP_TTL:
        return hit[1]
    why = _lookup(dn, deps, kind, tmdb, media, today)
    with LOCK:
        if len(LOOKUPS) >= LOOKUP_MAX:
            LOOKUPS.clear()
        LOOKUPS[k] = (t, why)
    return why


def _lookup(dn, deps, kind, tmdb, media, today):
    year = year_of(media, kind)
    try:
        if kind == "movie":
            x = deps.arr("radarr")(f"/movie/lookup/tmdb?tmdbId={tmdb}")
            r = (x or {}).get("ratings") or {}
            imdb = r.get("imdb") or {}
            return rr.reasons(kind, (x or {}).get("year") or year, today.year, (r.get("tmdb") or {}).get("votes"), imdb.get("value"), imdb.get("votes"))
        tvdb = ((media or {}).get("externalIds") or {}).get("tvdbId")
        if not isinstance(tvdb, int) or isinstance(tvdb, bool) or tvdb <= 0:
            raise dn.ApiError("no tvdb id")
        found = deps.arr("sonarr")("/series/lookup?term=" + urllib.parse.quote(f"tvdb:{tvdb}"))
        x = found[0] if isinstance(found, list) and found else {}
        r = x.get("ratings") or {}
        return rr.reasons(kind, x.get("year") or year, today.year, r.get("votes"), r.get("value"), r.get("votes"))
    except (dn.ApiError, AttributeError, TypeError, KeyError, IndexError):
        return rr.reasons(kind, year, today.year)


def title_state(dn, deps, me, kind, tmdb, media, today, lookup=True):
    doc = read_doc(deps.retention_file)
    k = rr.key(kind, tmdb)
    ov = overrides(deps.retention_keep).get(k)
    entry = doc["titles"].get(k)
    out = {"key": k, "known": isinstance(entry, dict), "deleteEnabled": bool(doc.get("deleteEnabled")), "updated": doc.get("updated")}
    if out["known"]:
        out.update({f: entry.get(f) for f in ("imported", "ssdUntil", "hddFrom", "tier", "reasons", "default")})
        out.update(card(entry, ov, today, enabled_on(doc)))
    else:
        why = lookup_default(dn, deps, kind, tmdb, media, today) if lookup else rr.reasons(kind, year_of(media, kind), today.year)
        out.update(reasons=why, default=bool(why))
        out.update(card({"default": bool(why), "due": None, "protected": rr.protected(k, [])}, ov, today, None))
    locked = out["source"] == "protected"
    out["canKeep"] = not locked and may_keep(me)
    out["canUnkeep"] = not locked and (manager(me) or dn.owns(me, media))
    return out


def wanted(url):
    q = urllib.parse.parse_qs(url.query)
    kind, tmdb = (q.get("type") or [None])[0], (q.get("tmdb") or [""])[0]
    if kind not in ("movie", "tv") or not dn_tmdb(tmdb):
        return None
    return kind, int(tmdb)


def dn_tmdb(s):
    return isinstance(s, str) and s.isdigit() and not s.startswith("0") and 0 < len(s) <= 10


def full_list(deps, today):
    doc = read_doc(deps.retention_file)
    ovs, since = overrides(deps.retention_keep), enabled_on(doc)
    items, counts = [], {"titles": 0, "keep": 0, "temporary": 0, "old": 0, "niche": 0, "goated": 0, "override": 0, "protected": 0, "soon": 0}
    for k, e in doc["titles"].items():
        if not isinstance(e, dict) or not rr.parse_key(k):
            continue
        st = rr.effective(e, ovs.get(k), today, since)
        counts["titles"] += 1
        counts["keep" if st["keep"] else "temporary"] += 1
        for r in e.get("reasons") or []:
            if r in counts:
                counts[r] += 1
        counts["override"] += st["source"] == "override"
        counts["protected"] += st["source"] == "protected"
        if not st["keep"] and st["deleteOn"]:
            counts["soon"] += st["soon"]
            items.append({"key": k, **{f: e.get(f) for f in ("t", "y", "kind", "imported", "tier", "size")}, **{f: st[f] for f in ("deleteOn", "why", "soon", "overdue", "postponed")}})
    items.sort(key=lambda i: (i["deleteOn"], i["key"]))
    return {"deleteEnabled": bool(doc.get("deleteEnabled")), "enabledOn": doc.get("enabledOn"), "updated": doc.get("updated"), "hdd": doc.get("hdd"),
            "rules": doc.get("rules"), "counts": counts, "items": items, "promote": doc.get("promote") or []}


def limited(uid):
    t = now_ts()
    hits = [x for x in RECENT.get(uid, []) if t - x < 3600]
    if hits and t - hits[-1] < MIN_GAP or len(hits) >= PER_HOUR:
        RECENT[uid] = hits
        return True
    RECENT[uid] = hits + [t]
    return False


def keep(dn, deps, me, req, auth, today):
    kind, tmdb, want = req.get("type"), req.get("tmdbId"), req.get("keep")
    if kind not in ("movie", "tv") or not isinstance(tmdb, int) or isinstance(tmdb, bool) or not 0 < tmdb < 10 ** 10 or not isinstance(want, bool):
        return 400, {"error": "bad request"}
    with LOCK:
        if limited(me["id"]):
            return 429, {"error": "too many"}
    try:
        media = deps.seerr(f"/{kind}/{tmdb}", auth)
    except dn.ApiError:
        return 404, {"error": "unknown title"}
    before = title_state(dn, deps, me, kind, tmdb, media, today)
    if not (before["canKeep"] if want else before["canUnkeep"]):
        return 403, {"error": "forbidden"}
    k = rr.key(kind, tmdb)
    with LOCK:
        try:
            store = read_store(deps.retention_keep)
        except StoreUnreadable as e:
            print(f"retention: keep store unreadable ({e}), not written", flush=True)
            return 503, {"error": "store"}
        store["titles"][k] = {"keep": want, "by": me["id"], "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
        write_store(deps.retention_keep, store)
    print(f"retention: user {me['id']} set {k} to {'keep forever' if want else 'temporary'}", flush=True)
    return 200, title_state(dn, deps, me, kind, tmdb, media, today)


def handle(dn, method, path, headers, body, deps, today=None):
    today = today or datetime.date.today()
    url = urllib.parse.urlsplit(path)
    sub = url.path[len("/_download-now/retention/"):]
    h = {k.lower(): v for k, v in headers.items()}
    req = {}
    if method == "POST" and sub == "keep":
        if h.get("origin") not in deps.origins:
            return 403, {"error": "origin"}
        if h.get("content-type", "").split(";")[0].strip() != "application/json":
            return 415, {"error": "json only"}
        if len(body or b"") > BODY_MAX:
            return 413, {"error": "too-big"}
        try:
            req = json.loads(body or b"{}")
        except (ValueError, RecursionError):
            return 400, {"error": "bad request"}
        if not isinstance(req, dict):
            return 400, {"error": "bad request"}
    elif not (method == "GET" and sub in ("title", "titles", "list")):
        return 404, {"error": "not found"}
    if not getattr(deps, "retention_file", None) or not getattr(deps, "retention_keep", None):
        return 404, {"error": "not configured"}
    auth = dn.auth_of(headers)
    try:
        me = deps.seerr("/auth/me", auth) if auth else None
    except dn.ApiError:
        me = None
    if not isinstance(me, dict) or not isinstance(me.get("id"), int) or isinstance(me.get("id"), bool):
        return 403, {"error": "forbidden"}
    if sub == "keep":
        return keep(dn, deps, me, req, auth, today)
    if sub == "list":
        return (200, full_list(deps, today)) if manager(me) else (403, {"error": "forbidden"})
    if sub == "titles":
        keys = [k for k in (urllib.parse.parse_qs(url.query).get("keys") or [""])[0].split(",") if k]
        if len(keys) > BATCH_MAX:
            return 400, {"error": "too many keys"}
        doc = read_doc(deps.retention_file)
        ovs, since = overrides(deps.retention_keep), enabled_on(doc)
        out = {k: card(doc["titles"][k], ovs.get(k), today, since) for k in keys if rr.parse_key(k) and isinstance(doc["titles"].get(k), dict)}
        return 200, {"titles": out, "deleteEnabled": bool(doc.get("deleteEnabled"))}
    w = wanted(url)
    if not w:
        return 400, {"error": "bad request"}
    try:
        media = deps.seerr(f"/{w[0]}/{w[1]}", auth)
    except dn.ApiError:
        media = None
    wants_default = (urllib.parse.parse_qs(url.query).get("default") or [""])[0] == "1"
    return 200, title_state(dn, deps, me, w[0], w[1], media, today, lookup=wants_default)
