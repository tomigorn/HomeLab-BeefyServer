"""Delete early backend (README "Delete early", "My library"): /_download-now/delete-early/ — GET state of a title (with the plan: who must OK it), POST delete / bulk / answer / restore.
Who: a Seerr session with a request right (or ADMIN) whose forward-auth header X-authentik-groups (set by fastpi's Traefik from Authentik on every request) names streaming-requesters; no header = nobody. Never: the test film, automatic keep forever. Rules: retention_rules.delete_plan.
This backend holds no key that deletes: it writes job files (<dir>/*.req) for Movie-Downloads' scripts/delete-early.py, which checks the plan again (fan-favourite setters, who is mid-watch), deletes into the recycle bin, restores and notifies, and keeps the state file and the mid-watch snapshot read here."""
import datetime
import json
import os
import pathlib
import secrets
import threading
import time
import urllib.parse

import retention_api as ra
import retention_rules as rr

GROUP = rr.GROUP
WEEK_MAX = rr.DELETE_WEEK_MAX
WEEK = rr.WEEK
BODY_MAX = 1024
BULK_BODY_MAX = 2048
MIN_GAP = 3
PER_HOUR = 30
ACTIVE = ("queued", "pending", "approved", "deleted")
COUNTED = ("queued", "pending", "approved", "deleted", "restored", "failed")
SHOWN = ("pending", "approved", "deleted", "denied", "expired", "refused", "failed", "restored")
RECENT = {}
LOCK = threading.Lock()


class StateUnreadable(Exception):
    pass


in_group = ra.in_group
may_request = ra.may_request


def when(s):
    try:
        d = datetime.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def read_state(path):
    """The runner's state; missing = nothing happened yet, unreadable = StateUnreadable."""
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except FileNotFoundError:
        return {"items": {}}
    except (OSError, ValueError) as e:
        raise StateUnreadable(type(e).__name__) from None
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        raise StateUnreadable("shape")
    return data


def read_jobs(d):
    out = []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for n in names:
        if n.endswith(".req"):
            try:
                j = json.loads(pathlib.Path(d, n).read_text())
            except (OSError, ValueError):
                continue
            if isinstance(j, dict):
                out.append(j)
    return out


def write_job(d, name, body):
    tmp = pathlib.Path(d, f".{name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f)
    os.chmod(tmp, 0o660)
    os.replace(tmp, pathlib.Path(d, name + ".req"))


def live_state(it, now):
    """A pending request older than its expiry counts as expired (= denied) before the runner marks it; a bin past its date is gone."""
    st = it.get("state")
    if st == "pending" and (when(it.get("expires")) or now) <= now:
        return "expired"
    if st == "deleted" and (when(it.get("binUntil")) or now) <= now:
        return "purged"
    return st


def used(state, jobs, uid, now):
    n = sum(1 for it in state["items"].values() if isinstance(it, dict) and it.get("by") == uid and it.get("state") in COUNTED and (when(it.get("at")) or now - WEEK) > now - WEEK
            and live_state(it, now) != "expired")
    return n + sum(1 for j in jobs if j.get("op") == "delete" and j.get("user") == uid)


def scope_of(season):
    return "all" if season is None else season


def overlaps(a, b):
    return a is None or b is None or a == b


def busy(state, jobs, kind, tmdb, season, now):
    for it in state["items"].values():
        if isinstance(it, dict) and it.get("kind") == kind and it.get("tmdb") == tmdb and live_state(it, now) in ACTIVE and overlaps(it.get("season"), season):
            return True
    return any(j.get("op") == "delete" and j.get("kind") == kind and j.get("tmdb") == tmdb and overlaps(j.get("season"), season) for j in jobs)


def seasons_of(media):
    out = []
    for s in ((media or {}).get("mediaInfo") or {}).get("seasons") or []:
        n = s.get("seasonNumber") if isinstance(s, dict) else None
        if isinstance(n, int) and not isinstance(n, bool) and n >= 0 and s.get("status") in (4, 5) and n not in out:
            out.append(n)
    return sorted(out)


def requesters(media):
    out = []
    for r in ((media or {}).get("mediaInfo") or {}).get("requests") or []:
        u = (r.get("requestedBy") or {}).get("id") if isinstance(r, dict) else None
        if isinstance(u, int) and not isinstance(u, bool) and r.get("status") != 3 and u not in out:
            out.append(u)
    return out


def library_entry(deps, kind, tmdb, media):
    doc = ra.read_doc(deps.retention_file)
    return ra.find_entry(doc, kind, tmdb, media)


def refusal(me, headers, kind, tmdb, entry):
    """Why the caller may not delete this title, or None."""
    if not in_group(headers):
        return "group"
    if not may_request(me):
        return "permission"
    return rr.delete_plan(rr.key(kind, tmdb), [], entry, {}, [], me.get("id"))["refused"]


def watching_file(deps):
    w = getattr(deps, "delete_watching", None)
    return w or str(pathlib.Path(deps.delete_state).with_name("delete-early-watching.json"))


def watchers_of(deps, key):
    """[(Seerr uid, name)] mid-watch from the runner's snapshot (refreshed every 30 min; the runner checks again when it deletes)."""
    try:
        data = json.loads(pathlib.Path(watching_file(deps)).read_text())
        rows = data["titles"].get(key) or []
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []
    return [(r.get("uid"), r.get("name")) for r in rows if isinstance(r, dict)]


def plan(deps, me, kind, tmdb, media, entry_key, entry, store=None):
    """The asks this delete would need: [{reason, names}] (fan setters, viewers mid-watch)."""
    store = store if store is not None else ra.store_or_empty(deps.retention_keep)
    tvdb = (entry or {}).get("tvdb") or (ra.tvdb_of(media) if kind == "tv" else None)
    setters = {u: m.get("name") for u, m in ra.title_marks(store, (rr.key(kind, tmdb), entry_key), tvdb).items()}
    p = rr.delete_plan(rr.key(kind, tmdb), [], entry, setters, watchers_of(deps, entry_key), me.get("id"))
    return [{"reason": a["reason"], "names": a["names"]} for a in p["asks"]]


def public(it, me, now):
    st = live_state(it, now)
    uid = me.get("id")
    approver = st == "pending" and any(a.get("answer") is None and uid in (a.get("approvers") or []) for a in it.get("asks") or [] if isinstance(a, dict))
    can_restore = st == "deleted" and (it.get("by") == uid or uid in (it.get("requesters") or []) or ra.manager(me))
    return {"id": it.get("id"), "state": st, "season": it.get("season"), "byName": it.get("byName"), "mine": it.get("by") == uid, "approver": approver,
            "asks": [{"reason": a.get("reason"), "names": a.get("names") or [], "answer": a.get("answer")} for a in it.get("asks") or [] if isinstance(a, dict)],
            "expires": it.get("expires"), "binUntil": it.get("binUntil"), "canRestore": bool(can_restore), "why": it.get("why"), "at": it.get("at")}


def title_items(state, me, kind, tmdb, now):
    out = []
    for it in state["items"].values():
        if not isinstance(it, dict) or it.get("kind") != kind or it.get("tmdb") != tmdb:
            continue
        st = live_state(it, now)
        recent = (when(it.get("updated") or (it.get("expires") if st == "expired" else None) or it.get("at")) or now) > now - datetime.timedelta(days=1)
        if st in ("pending", "approved", "deleted") or (st in SHOWN and recent):
            out.append(public(it, me, now))
    return sorted(out, key=lambda x: x.get("at") or "")


def limited(uid):
    t = time.time()
    hits = [x for x in RECENT.get(uid, []) if t - x < 3600]
    if hits and t - hits[-1] < MIN_GAP or len(hits) >= PER_HOUR:
        RECENT[uid] = hits
        return True
    RECENT[uid] = hits + [t]
    return False


def is_id(v):
    return isinstance(v, str) and 0 < len(v) <= 40 and all(c in "0123456789abcdefghijklmnopqrstuvwxyz-" for c in v)


def account(headers):
    h = {k.lower(): v for k, v in headers.items()}
    v = h.get("x-authentik-username")
    return v[:80] if isinstance(v, str) else None


def do_delete(dn, deps, me, req, auth, headers, now):
    kind, tmdb, season = req.get("type"), req.get("tmdbId"), req.get("season")
    if kind not in ("movie", "tv") or not isinstance(tmdb, int) or isinstance(tmdb, bool) or not 0 < tmdb < 10 ** 10:
        return 400, {"error": "bad request"}
    if season is not None and (kind != "tv" or not isinstance(season, int) or isinstance(season, bool) or not 0 <= season < 1000):
        return 400, {"error": "bad request"}
    try:
        media = deps.seerr(f"/{kind}/{tmdb}", auth)
    except dn.ApiError:
        return 404, {"error": "unknown title"}
    _, entry = library_entry(deps, kind, tmdb, media)
    why = refusal(me, headers, kind, tmdb, entry)
    if why:
        return (409, {"error": why}) if why == "library" else (403, {"error": why})
    if season is not None and season not in seasons_of(media):
        return 400, {"error": "season"}
    with LOCK:
        try:
            state = read_state(deps.delete_state)
        except StateUnreadable as e:
            print(f"delete-early: runner state unreadable ({e}), nothing queued", flush=True)
            return 503, {"error": "state"}
        jobs = read_jobs(deps.delete_jobs)
        if busy(state, jobs, kind, tmdb, season, now):
            return 409, {"error": "already"}
        if used(state, jobs, me["id"], now) >= WEEK_MAX:
            return 429, {"error": "quota"}
        if limited(me["id"]):
            return 429, {"error": "too many"}
        jid = new_job(deps, me, headers, kind, tmdb, season, media, entry, now)
    print(f"delete-early: user {me['id']} asks to delete {rr.key(kind, tmdb)} {'whole' if season is None else f'season {season}'}", flush=True)
    return 202, {"state": "queued", "id": jid}


def new_job(deps, me, headers, kind, tmdb, season, media, entry, now):
    jid = secrets.token_hex(8)
    job = {"op": "delete", "id": jid, "user": me["id"], "userName": me.get("displayName") or me.get("username") or f"user {me['id']}", "account": account(headers),
           "kind": kind, "tmdb": tmdb, "tvdb": ra.tvdb_of(media) if kind == "tv" else None, "season": season,
           "title": (media or {}).get("title") or (media or {}).get("name") or (entry or {}).get("t") or "", "year": (entry or {}).get("y"),
           "requesters": requesters(media), "at": now.isoformat(timespec="seconds")}
    write_job(deps.delete_jobs, f"delete-{jid}", job)
    return jid


def do_bulk(dn, deps, me, req, auth, headers, now):
    """Whole titles from My library, at most the deletions left this week; any refused title = nothing sent."""
    items = req.get("items")
    if not isinstance(items, list) or not 0 < len(items) <= WEEK_MAX:
        return 400, {"error": "bad request"}
    wanted = []
    for x in items:
        kind, tmdb = (x or {}).get("type") if isinstance(x, dict) else None, (x or {}).get("tmdbId") if isinstance(x, dict) else None
        if kind not in ("movie", "tv") or not isinstance(tmdb, int) or isinstance(tmdb, bool) or not 0 < tmdb < 10 ** 10 or (kind, tmdb) in wanted:
            return 400, {"error": "bad request"}
        wanted.append((kind, tmdb))
    if not in_group(headers):
        return 403, {"error": "group"}
    if not may_request(me):
        return 403, {"error": "permission"}
    found, refused = [], {}
    for kind, tmdb in wanted:
        try:
            media = deps.seerr(f"/{kind}/{tmdb}", auth)
        except dn.ApiError:
            media = None
        _, entry = library_entry(deps, kind, tmdb, media)
        why = refusal(me, headers, kind, tmdb, entry) if media is not None else "library"
        if why:
            refused[rr.key(kind, tmdb)] = why
        found.append((kind, tmdb, media, entry))
    with LOCK:
        try:
            state = read_state(deps.delete_state)
        except StateUnreadable as e:
            print(f"delete-early: runner state unreadable ({e}), nothing queued", flush=True)
            return 503, {"error": "state"}
        jobs = read_jobs(deps.delete_jobs)
        for kind, tmdb, _, _ in found:
            if rr.key(kind, tmdb) not in refused and busy(state, jobs, kind, tmdb, None, now):
                refused[rr.key(kind, tmdb)] = "already"
        if refused:
            return 409, {"error": "refused", "titles": refused}
        if used(state, jobs, me["id"], now) + len(found) > WEEK_MAX:
            return 429, {"error": "quota"}
        if limited(me["id"]):
            return 429, {"error": "too many"}
        ids = [new_job(deps, me, headers, kind, tmdb, None, media, entry, now) for kind, tmdb, media, entry in found]
    print(f"delete-early: user {me['id']} asks to delete {len(ids)} titles: {', '.join(rr.key(k, t) for k, t, _, _ in found)}", flush=True)
    return 202, {"state": "queued", "ids": ids}


def find_item(deps, iid):
    state = read_state(deps.delete_state)
    it = state["items"].get(iid)
    return it if isinstance(it, dict) else None


def do_answer(deps, me, req, headers, now):
    iid, ok = req.get("id"), req.get("approve")
    if not is_id(iid) or not isinstance(ok, bool):
        return 400, {"error": "bad request"}
    if not in_group(headers):
        return 403, {"error": "group"}
    try:
        it = find_item(deps, iid)
    except StateUnreadable:
        return 503, {"error": "state"}
    if not it:
        return 404, {"error": "unknown"}
    if live_state(it, now) != "pending":
        return 409, {"error": "closed"}
    if not public(it, me, now)["approver"]:
        return 403, {"error": "forbidden"}
    with LOCK:
        if limited(me["id"]):
            return 429, {"error": "too many"}
        write_job(deps.delete_jobs, f"answer-{iid}-{me['id']}", {"op": "answer", "id": iid, "user": me["id"], "userName": me.get("displayName") or f"user {me['id']}",
                                                                  "account": account(headers), "approve": ok, "at": now.isoformat(timespec="seconds")})
    print(f"delete-early: user {me['id']} {'approves' if ok else 'denies'} {iid}", flush=True)
    return 202, {"state": "queued"}


def do_restore(deps, me, req, headers, now):
    iid = req.get("id")
    if not is_id(iid):
        return 400, {"error": "bad request"}
    if not in_group(headers):
        return 403, {"error": "group"}
    try:
        it = find_item(deps, iid)
    except StateUnreadable:
        return 503, {"error": "state"}
    if not it:
        return 404, {"error": "unknown"}
    if live_state(it, now) != "deleted":
        return 409, {"error": "closed"}
    if not public(it, me, now)["canRestore"]:
        return 403, {"error": "forbidden"}
    name = f"restore-{iid}"
    with LOCK:
        if os.path.exists(os.path.join(deps.delete_jobs, name + ".req")):
            return 202, {"state": "queued"}
        if limited(me["id"]):
            return 429, {"error": "too many"}
        write_job(deps.delete_jobs, name, {"op": "restore", "id": iid, "user": me["id"], "userName": me.get("displayName") or f"user {me['id']}", "account": account(headers),
                                           "manager": ra.manager(me), "at": now.isoformat(timespec="seconds")})
    print(f"delete-early: user {me['id']} restores {iid}", flush=True)
    return 202, {"state": "queued"}


def get_state(dn, deps, me, url, auth, headers, now):
    w = ra.wanted(url)
    if not w:
        return 400, {"error": "bad request"}
    kind, tmdb = w
    try:
        media = deps.seerr(f"/{kind}/{tmdb}", auth)
    except dn.ApiError:
        media = None
    ek, entry = library_entry(deps, kind, tmdb, media)
    try:
        state = read_state(deps.delete_state)
    except StateUnreadable:
        state = {"items": {}}
    jobs = read_jobs(deps.delete_jobs)
    why = refusal(me, headers, kind, tmdb, entry)
    queued = [scope_of(j.get("season")) for j in jobs if j.get("op") == "delete" and j.get("kind") == kind and j.get("tmdb") == tmdb]
    return 200, {"key": rr.key(kind, tmdb), "allowed": why is None, "reason": why, "quota": {"used": used(state, jobs, me["id"], now), "max": WEEK_MAX},
                 "seasons": seasons_of(media) if kind == "tv" else [], "queued": queued, "items": title_items(state, me, kind, tmdb, now),
                 "plan": plan(deps, me, kind, tmdb, media, ek, entry) if why is None else []}


def handle(dn, method, path, headers, body, deps, now=None):
    now = now or datetime.datetime.now().astimezone()
    url = urllib.parse.urlsplit(path)
    sub = url.path[len("/_download-now/delete-early/"):]
    h = {k.lower(): v for k, v in headers.items()}
    req = {}
    if method == "POST" and sub in ("delete", "bulk", "answer", "restore"):
        if h.get("origin") not in deps.origins:
            return 403, {"error": "origin"}
        if h.get("content-type", "").split(";")[0].strip() != "application/json":
            return 415, {"error": "json only"}
        if len(body or b"") > (BULK_BODY_MAX if sub == "bulk" else BODY_MAX):
            return 413, {"error": "too-big"}
        try:
            req = json.loads(body or b"{}")
        except (ValueError, RecursionError):
            return 400, {"error": "bad request"}
        if not isinstance(req, dict):
            return 400, {"error": "bad request"}
    elif not (method == "GET" and sub == "state"):
        return 404, {"error": "not found"}
    if not all(getattr(deps, a, None) for a in ("retention_file", "retention_keep", "delete_jobs", "delete_state")):
        return 404, {"error": "not configured"}
    auth = dn.auth_of(headers)
    try:
        me = deps.seerr("/auth/me", auth) if auth else None
    except dn.ApiError:
        me = None
    if not isinstance(me, dict) or not isinstance(me.get("id"), int) or isinstance(me.get("id"), bool):
        return 403, {"error": "forbidden"}
    if sub == "state":
        return get_state(dn, deps, me, url, auth, headers, now)
    if sub == "delete":
        return do_delete(dn, deps, me, req, auth, headers, now)
    if sub == "bulk":
        return do_bulk(dn, deps, me, req, auth, headers, now)
    if sub == "answer":
        return do_answer(deps, me, req, headers, now)
    return do_restore(deps, me, req, headers, now)
