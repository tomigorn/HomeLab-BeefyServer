"""My request (README "My request"): the panel on Seerr's title page. Shows what a request set (profile, audio/subtitle tags, Can wait, seasons) and lets its requester (or a manager) change it or delete it.
Approved requests change the Radarr/Sonarr title (Seerr edits only pending requests); pending ones change in Seerr, which hands them to Radarr/Sonarr on approval. Only audio-*/subs-* tags, only existing profiles."""
import datetime
import json
import re
import urllib.parse

ALLOWED = re.compile(r"^(audio|subs)-[a-z]+(?:-[a-z]+)*$")
AUTO = ("old-rare",)
PENDING, APPROVED, DECLINED = 1, 2, 3
ADDED_GRACE = 600


def human(label):
    kind, _, rest = label.partition("-")
    return {"label": label, "kind": kind, "name": " ".join(w.capitalize() for w in rest.split("-"))}


def _when(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _live(media):
    info = media.get("mediaInfo") if isinstance(media, dict) else None
    reqs = info.get("requests") if isinstance(info, dict) else None
    return [r for r in reqs if isinstance(r, dict) and r.get("status") != DECLINED and isinstance(r.get("requestedBy"), dict)] if isinstance(reqs, list) else []


def _owner(r):
    return r["requestedBy"].get("id")


class Ctx:
    def __init__(self, dn, kind, tmdb, me, manager, media, deps):
        self.dn, self.kind, self.tmdb, self.me, self.manager, self.media, self.deps = dn, kind, tmdb, me, manager, media, deps
        self.app = dn.APP[kind]
        self.arr = deps.arr(self.app)
        self.live = _live(media)
        self.title = dn.find_title(kind, self.arr, media, tmdb)
        self.tags = {t["id"]: t["label"] for t in self.arr("/tag") if isinstance(t, dict)}
        self.ids = {v: k for k, v in self.tags.items()}
        self.profiles = [{"id": p["id"], "name": p["name"]} for p in self.arr("/qualityprofile") if isinstance(p, dict)]
        self.dn_id = self.ids.get(dn.TAG)

    def visible(self):
        return [r for r in self.live if self.manager or _owner(r) == self.me["id"]]

    def shared(self, r):
        return any(_owner(o) != _owner(r) for o in self.live if o is not r)

    def editable(self, r):
        return self.manager or (_owner(r) == self.me["id"] and not self.shared(r))

    def delete_effect(self, r):
        if any(o is not r for o in self.live):
            return "request-only"
        if self.title is None:
            return "clear"
        added, created = _when(self.title.get("added")), _when(r.get("createdAt"))
        return "clear" if added is not None and created is not None and added >= created - ADDED_GRACE else "request-only"

    def settings(self, r):
        if r.get("status") == APPROVED and self.title is not None:
            return self.title.get("qualityProfileId"), [self.tags.get(i) for i in self.title.get("tags") or []]
        return r.get("profileId"), [self.tags.get(i) for i in r.get("tags") or []]

    def describe(self, r):
        pid, labels = self.settings(r)
        labels = [l for l in labels if l]
        out = {"id": r["id"], "status": r.get("status"), "is4k": bool(r.get("is4k")), "mine": _owner(r) == self.me["id"] and not self.manager,
               "by": r["requestedBy"].get("displayName") if self.manager else None, "createdAt": r.get("createdAt"),
               "seasons": sorted(s.get("seasonNumber") for s in r.get("seasons") or [] if isinstance(s, dict)),
               "profile": next((p for p in self.profiles if p["id"] == pid), {"id": pid, "name": str(pid)}),
               "tags": sorted((human(l) for l in labels if ALLOWED.match(l)), key=lambda t: t["label"]),
               "auto": [l for l in labels if l in AUTO], "canWait": self.dn.TAG not in labels,
               "editable": self.editable(r), "deleteEffect": self.delete_effect(r)}
        if self.manager:
            out["mine"] = _owner(r) == self.me["id"]
        roots = self.arr("/rootfolder")
        if isinstance(roots, list) and len(roots) > 1:
            out["rootFolder"] = (self.title or {}).get("rootFolderPath") or r.get("rootFolder")
        return out


def read(dn, kind, tmdb, me, manager, media, deps):
    c = Ctx(dn, kind, tmdb, me, manager, media, deps)
    reqs = c.visible()
    if not reqs:
        return 403, {"error": "forbidden"}
    return 200, {"requests": [c.describe(r) for r in reqs], "profiles": c.profiles,
                 "tags": [human(l) for l in sorted(c.ids) if ALLOWED.match(l)], "manager": manager}


def _editor(c, body):
    key = "movieIds" if c.kind == "movie" else "seriesIds"
    c.arr("/movie/editor" if c.kind == "movie" else "/series/editor", "PUT", {key: [c.title["id"]], **body})


def update(c, r, want):
    pid0, labels0 = c.settings(r)
    labels0 = [l for l in labels0 if l]
    pid = want.get("profileId", pid0)
    tags = want.get("tags")
    cur = {l for l in labels0 if ALLOWED.match(l)}
    new = set(tags) if tags is not None else cur
    can_wait = want.get("canWait", c.dn.TAG not in labels0)
    changed = []
    if pid != pid0:
        changed.append(f"profile {pid0} -> {pid}")
    if new != cur:
        changed.append("tags " + " ".join([f"+{t}" for t in sorted(new - cur)] + [f"-{t}" for t in sorted(cur - new)]))
    if can_wait != (c.dn.TAG not in labels0):
        changed.append("can wait on" if can_wait else "can wait off (download now)")
    if not changed:
        return changed
    if r.get("status") == PENDING or c.title is None:
        if c.deps.seerr_admin is None:
            raise c.dn.ApiError("no Seerr admin key")
        ids = [c.ids[l] for l in sorted(new)] + [i for i in r.get("tags") or [] if c.tags.get(i) and not ALLOWED.match(c.tags[i]) and c.tags[i] != c.dn.TAG]
        if not can_wait and c.dn_id is not None:
            ids.append(c.dn_id)
        body = {"mediaType": c.kind, "serverId": r.get("serverId"), "profileId": pid, "rootFolder": r.get("rootFolder"), "languageProfileId": r.get("languageProfileId"), "tags": ids, "is4k": bool(r.get("is4k"))}
        if c.kind == "tv":
            body["seasons"] = sorted(s.get("seasonNumber") for s in r.get("seasons") or [])
        c.deps.seerr_admin(f"/request/{r['id']}", "PUT", body)
        return changed
    if pid != pid0:
        _editor(c, {"qualityProfileId": pid})
    if new - cur:
        _editor(c, {"tags": [c.ids[l] for l in sorted(new - cur)], "applyTags": "add"})
    if cur - new:
        _editor(c, {"tags": [c.ids[l] for l in sorted(cur - new)], "applyTags": "remove"})
    if can_wait != (c.dn.TAG not in labels0):
        if can_wait:
            _editor(c, {"tags": [c.dn_id], "applyTags": "remove"})
        else:
            title, p = c.dn.assess(c.kind, c.tmdb, c.media, c.deps)
            if title:
                c.dn.apply(c.kind, p, c.deps)
    return changed


def delete(c, r):
    if c.deps.seerr_admin is None:
        raise c.dn.ApiError("no Seerr admin key")
    effect = c.delete_effect(r)
    if effect == "clear":
        c.deps.seerr_admin(f"/media/{c.media['mediaInfo']['id']}", "DELETE")
    else:
        c.deps.seerr_admin(f"/request/{r['id']}", "DELETE")
    return effect


def validate(want, c):
    out = {}
    if "profileId" in want:
        if not isinstance(want["profileId"], int) or isinstance(want["profileId"], bool) or want["profileId"] not in {p["id"] for p in c.profiles}:
            return None
        out["profileId"] = want["profileId"]
    if "tags" in want:
        t = want["tags"]
        if not isinstance(t, list) or len(t) > 30 or not all(isinstance(x, str) and ALLOWED.match(x) and x in c.ids for x in t):
            return None
        out["tags"] = t
    if "canWait" in want:
        if not isinstance(want["canWait"], bool):
            return None
        out["canWait"] = want["canWait"]
    return out


def handle(dn, method, path, headers, body, deps):
    url = urllib.parse.urlsplit(path)
    h = {k.lower(): v for k, v in headers.items()}
    if method not in ("GET", "POST"):
        return 404, {"error": "not found"}
    if method == "POST":
        if h.get("origin") not in deps.origins:
            return 403, {"error": "origin"}
        if h.get("content-type", "").split(";")[0].strip() != "application/json":
            return 415, {"error": "json only"}
        try:
            req = json.loads(body or b"{}")
            kind, tmdb, action, rid = req.get("type"), str(req.get("tmdbId", "")), req.get("action"), req.get("requestId")
        except (ValueError, AttributeError, RecursionError):
            return 400, {"error": "bad request"}
        if action not in ("update", "delete") or not isinstance(rid, int) or isinstance(rid, bool):
            return 400, {"error": "bad request"}
    else:
        q = urllib.parse.parse_qs(url.query)
        kind, tmdb, req = (q.get("type") or [None])[0], (q.get("tmdb") or [""])[0], {}
    if kind not in dn.APP or not dn.TMDB.match(tmdb):
        return 400, {"error": "bad request"}
    auth = dn.auth_of(headers)
    try:
        me = deps.seerr("/auth/me", auth) if auth else None
        media = deps.seerr(f"/{kind}/{tmdb}", auth) if isinstance(me, dict) else None
    except dn.ApiError:
        me = media = None
    if not isinstance(me, dict) or not isinstance(me.get("id"), int) or not isinstance(media, dict):
        return 403, {"error": "forbidden"}
    manager = dn.allowed(me)
    try:
        if method == "GET":
            return read(dn, kind, int(tmdb), me, manager, media, deps)
        with dn.LOCK:
            c = Ctx(dn, kind, int(tmdb), me, manager, media, deps)
            r = next((x for x in c.visible() if x.get("id") == rid), None)
            if r is None or not c.editable(r) and not (action == "delete" and _owner(r) == me["id"]):
                return 403, {"error": "forbidden"}
            if action == "delete":
                effect = delete(c, r)
                print(f"user {me['id']} ({'manager' if manager else 'own request'}) request {rid} {kind} {tmdb}: deleted ({effect})", flush=True)
                return 200, {"deleted": effect}
            want = validate(req, c)
            if want is None:
                return 400, {"error": "bad request"}
            changed = update(c, r, want)
            print(f"user {me['id']} ({'manager' if manager else 'own request'}) request {rid} {kind} {tmdb}: {', '.join(changed) or 'no change'}", flush=True)
            c2 = Ctx(dn, kind, int(tmdb), me, manager, media, deps)
            return 200, {"changed": changed, "request": c2.describe(r)}
    except Exception as e:
        print(f"my request {kind} {tmdb}: {type(e).__name__} {dn.redact(e)}", flush=True)
        return 502, {"error": "not reachable"}
