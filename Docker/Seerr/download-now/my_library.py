"""My library (README "My library"): GET /_download-now/library/list — every downloaded title (retention.json) and every whole title in the Delete early recycle bin, with its state (temporary, keep, soon, bin), the fan-favourite marks, what an early delete would need, the requests waiting for the caller's OK and both weekly counts.
Every decision comes from retention_api / delete_early (rules: retention_rules); this module only puts them together for the page. Actions go through /_download-now/retention/keep and /_download-now/delete-early/."""
import datetime
import threading
import time
import urllib.parse

import delete_early as de
import retention_api as ra
import retention_rules as rr

MINE = {}
MINE_TTL = 60
PAGE = 100
PAGES_MAX = 10
LOCK = threading.Lock()


def requested_by(dn, deps, me, auth):
    """Keys the caller requested (Seerr /request?requestedBy=, cached 60 s), None when Seerr does not answer."""
    uid, t = me["id"], time.time()
    with LOCK:
        hit = MINE.get(uid)
    if hit and t - hit[0] < MINE_TTL:
        return hit[1]
    out = set()
    try:
        for page in range(PAGES_MAX):
            got = deps.seerr(f"/request?take={PAGE}&skip={page * PAGE}&filter=all&sort=added&requestedBy={uid}", auth) or {}
            rows = got.get("results") or []
            for r in rows:
                m = (r or {}).get("media") or {}
                if m.get("mediaType") in ("movie", "tv") and isinstance(m.get("tmdbId"), int):
                    out.add(rr.key(m["mediaType"], m["tmdbId"]))
            if len(rows) < PAGE:
                break
    except (dn.ApiError, AttributeError, TypeError, ValueError):
        return None
    with LOCK:
        MINE[uid] = (t, out)
    return out


def visible(it, now):
    st = de.live_state(it, now)
    return st in ("pending", "approved", "deleted")


def build(dn, deps, me, auth, headers, now):
    today = now.date()
    doc = ra.read_doc(deps.retention_file)
    since = ra.enabled_on(doc)
    store = ra.store_or_empty(deps.retention_keep)
    try:
        state = de.read_state(deps.delete_state)
    except de.StateUnreadable:
        state = {"items": {}}
    jobs = de.read_jobs(deps.delete_jobs)
    uid = me.get("id")
    act = ra.may_act(me, headers)
    mine = requested_by(dn, deps, me, auth)
    items_by = {}
    for it in state["items"].values():
        if isinstance(it, dict) and it.get("kind") in ("movie", "tv") and isinstance(it.get("tmdb"), int) and visible(it, now):
            items_by.setdefault(rr.key(it["kind"], it["tmdb"]), []).append(it)
    titles = []
    for k, e in doc["titles"].items():
        parsed = rr.parse_key(k)
        if not isinstance(e, dict) or not parsed:
            continue
        kind, tmdb = parsed
        marks = ra.title_marks(store, (k,), e.get("tvdb"))
        st = rr.effective(e, True if marks else None, today, since)
        out = {"key": k, "kind": kind, "tmdb": tmdb, "t": e.get("t"), "y": e.get("y"), "size": e.get("size"), "tier": e.get("tier"), "deleteOn": st["deleteOn"],
               "source": st["source"], "reasons": e.get("reasons") or [], "why": st["why"], "postponed": st["postponed"],
               "state": "keep" if st["keep"] else "soon" if st["soon"] else "temporary"}
        locked = st["source"] == "protected" or (st["source"] == "default" and st["keep"])
        used = rr.marks_used(store.get("marked"), uid, now)
        out["marks"] = [{"name": m.get("name"), "mine": u == uid} for u, m in sorted(marks.items(), key=lambda x: (str(x[1].get("at") or ""), str(x[1].get("name") or "")))]
        out["canMark"] = not locked and uid not in marks and act and used < rr.MARK_WEEK_MAX
        out["canUnmark"] = st["source"] != "protected" and uid in marks
        out["items"] = [de.public(it, me, now) for it in items_by.pop(k, [])]
        why = de.refusal(me, headers, kind, tmdb, e)
        busy = de.busy(state, jobs, kind, tmdb, None, now)
        out["del"] = {"allowed": why is None and not busy, "reason": why or ("already" if busy else None),
                      "plan": de.plan(deps, me, kind, tmdb, None, k, e, store) if why is None and not busy else []}
        out["mine"] = uid in marks or (mine is not None and k in mine)
        titles.append(out)
    for k, its in items_by.items():
        whole = [it for it in its if it.get("season") is None and de.live_state(it, now) == "deleted"]
        if not whole:
            continue
        kind, tmdb = rr.parse_key(k) or (None, None)
        if not kind:
            continue
        it = whole[0]
        titles.append({"key": k, "kind": kind, "tmdb": tmdb, "t": it.get("title"), "y": it.get("year"), "size": None, "tier": None, "deleteOn": None, "source": None, "reasons": [],
                       "why": None, "postponed": None, "state": "bin", "marks": [], "canMark": False, "canUnmark": False, "items": [de.public(x, me, now) for x in its],
                       "del": {"allowed": False, "reason": "bin", "plan": []}, "mine": uid == it.get("by") or uid in (it.get("requesters") or []) or (mine is not None and k in mine)})
    titles.sort(key=lambda t: (str(t.get("t") or "").lower(), t["key"]))
    asked = []
    for it in state["items"].values():
        if isinstance(it, dict) and de.live_state(it, now) == "pending":
            p = de.public(it, me, now)
            if p["approver"]:
                asked.append({"key": rr.key(it["kind"], it["tmdb"]), "kind": it["kind"], "t": it.get("title"), "y": it.get("year"), "item": p})
    return {"titles": titles, "asked": asked, "group": act, "deleteEnabled": bool(doc.get("deleteEnabled")), "mineKnown": mine is not None, "updated": doc.get("updated"),
            "quota": {"delete": {"used": de.used(state, jobs, uid, now), "max": rr.DELETE_WEEK_MAX}, "marks": {"used": rr.marks_used(store.get("marked"), uid, now), "max": rr.MARK_WEEK_MAX}}}


def handle(dn, method, path, headers, body, deps, now=None):
    now = now or datetime.datetime.now().astimezone()
    url = urllib.parse.urlsplit(path)
    if method != "GET" or url.path != "/_download-now/library/list":
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
    return 200, build(dn, deps, me, auth, headers, now)
