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
LOOKUP_PER_MIN = 20
LOOKUP_HITS = {}
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
    try:
        os.stat(p)
    except FileNotFoundError:
        try:
            os.stat(p.with_name(p.name + ".bak"))
        except FileNotFoundError:
            return {"titles": {}}
        except OSError as e:
            raise StoreUnreadable(type(e).__name__) from None
        raise StoreUnreadable("missing while its .bak exists")
    except OSError as e:
        raise StoreUnreadable(type(e).__name__) from None
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise StoreUnreadable(type(e).__name__) from None
    if not isinstance(data, dict) or not isinstance(data.get("titles"), dict):
        raise StoreUnreadable("shape")
    return data


def overrides(path):
    """{key | tvdb/<id>: keep}; a series choice also counts for its TVDB id (Seerr and Sonarr may disagree on the TMDB id)."""
    try:
        titles = read_store(path)["titles"]
    except StoreUnreadable:
        return {}
    out = {}
    for k, v in titles.items():
        if isinstance(v, dict) and isinstance(v.get("keep"), bool):
            out[k] = v["keep"]
            if isinstance(v.get("tvdb"), int) and v["tvdb"] > 0:
                alias = f"tvdb/{v['tvdb']}"
                out[alias] = out.get(alias, False) or v["keep"]
    return out


def override_for(ovs, key, entry):
    """A choice for the key or the series' TVDB id; records that disagree: keep wins (as Movie-Downloads' retention.py)."""
    tvdb = (entry or {}).get("tvdb")
    found = [ovs[k] for k in (key, f"tvdb/{tvdb}" if tvdb else None) if k in ovs]
    return True if True in found else False if found else None


def write_store(path, data):
    """The store is never missing: new content to a temp file (fsync), the old file hard-linked to .bak, then the temp file atomically over it."""
    p = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".keep-")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o640)
    if p.exists():
        link = p.with_name(f".keep-bak-{os.getpid()}-{threading.get_ident()}")
        try:
            os.unlink(link)
        except FileNotFoundError:
            pass
        os.link(p, link)
        os.replace(link, p.with_name(p.name + ".bak"))
    os.replace(tmp, p)
    d = os.open(p.parent, os.O_RDONLY)
    try:
        os.fsync(d)
    finally:
        os.close(d)


def enabled_on(doc):
    try:
        return datetime.date.fromisoformat(doc["enabledOn"]) if doc.get("deleteEnabled") and doc.get("enabledOn") else None
    except (TypeError, ValueError):
        return None


def tvdb_of(media):
    for v in (((media or {}).get("externalIds") or {}).get("tvdbId"), ((media or {}).get("mediaInfo") or {}).get("tvdbId")):
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            return v
    return None


def find_entry(doc, kind, tmdb, media):
    """(retention.json key, entry): by Seerr's TMDB id, a series also by its TVDB id."""
    k = rr.key(kind, tmdb)
    if isinstance(doc["titles"].get(k), dict):
        return k, doc["titles"][k]
    tvdb = tvdb_of(media) if kind == "tv" else None
    if tvdb:
        for key, e in doc["titles"].items():
            if isinstance(e, dict) and e.get("kind") == "tv" and e.get("tvdb") == tvdb:
                return key, e
    return k, None


def is_kept(deps, key):
    """True unless the title is known and temporary: unknown titles, unreadable data and the test film count as kept (for other backends that would delete files)."""
    try:
        doc = json.loads(pathlib.Path(deps.retention_file).read_text())
        entry = doc["titles"].get(key)
        ovs = overrides(deps.retention_keep)
        read_store(deps.retention_keep)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StoreUnreadable):
        return True
    if not isinstance(entry, dict) or rr.protected(key, []):
        return True
    return rr.effective(entry, override_for(ovs, key, entry), datetime.date.today())["keep"]


def card(entry, override, today, since):
    st = rr.effective(entry, override, today, since)
    return {k: st[k] for k in ("state", "keep", "source", "deleteOn", "soon", "overdue", "postponed", "why")}


def year_of(media, kind):
    s = (media or {}).get("releaseDate" if kind == "movie" else "firstAirDate") or ""
    return int(s[:4]) if s[:4].isdigit() else None


def lookup_default(dn, deps, kind, tmdb, media, today, uid=None):
    """reasons for a title not in the library yet: Radarr/Sonarr lookup (IMDb rating, votes, year; cached 10 min, LOOKUP_PER_MIN uncached per user), else the year from Seerr."""
    k, t = rr.key(kind, tmdb), now_ts()
    hit = LOOKUPS.get(k)
    if hit and t - hit[0] < LOOKUP_TTL:
        return hit[1]
    with LOCK:
        recent = [x for x in LOOKUP_HITS.get(uid, []) if t - x < 60]
        over = len(recent) >= LOOKUP_PER_MIN
        LOOKUP_HITS[uid] = recent if over else recent + [t]
    if over:
        return rr.reasons(kind, year_of(media, kind), today.year)
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
    ek, entry = find_entry(doc, kind, tmdb, media)
    ovs = overrides(deps.retention_keep)
    found = [v for v in (override_for(ovs, k, None), override_for(ovs, ek, entry if entry else {"tvdb": tvdb_of(media) if kind == "tv" else None})) if v is not None]
    ov = True if True in found else False if found else None
    out = {"key": k, "entryKey": ek, "known": isinstance(entry, dict), "deleteEnabled": bool(doc.get("deleteEnabled")), "updated": doc.get("updated")}
    if out["known"]:
        out.update({f: entry.get(f) for f in ("imported", "ssdUntil", "hddFrom", "tier", "reasons", "default")})
        out.update(card(entry, ov, today, enabled_on(doc)))
    else:
        why = lookup_default(dn, deps, kind, tmdb, media, today, me.get("id")) if lookup else rr.reasons(kind, year_of(media, kind), today.year)
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
        st = rr.effective(e, override_for(ovs, k, e), today, since)
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
        rec = {"keep": want, "by": me["id"], "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
        if kind == "tv" and tvdb_of(media):
            rec["tvdb"] = tvdb_of(media)
            for other in store["titles"].values():
                if isinstance(other, dict) and other.get("tvdb") == rec["tvdb"]:
                    other.update(keep=want, by=me["id"], at=rec["at"])
        store["titles"][k] = rec
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
        out = {k: card(doc["titles"][k], override_for(ovs, k, doc["titles"][k]), today, since) for k in keys if rr.parse_key(k) and isinstance(doc["titles"].get(k), dict)}
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
