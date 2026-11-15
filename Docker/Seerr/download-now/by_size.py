"""GB sort of the Requests page (README "Request sizes"): GET /_download-now/by-size with Seerr's own list query plus hg_size=<mode>. Reads every request of the caller's Seerr view (asked as the caller, so Seerr's own visibility rules apply), sorts them by request-state.json "sizes" and answers one page in Seerr's /request format, so Seerr renders the sorted page itself."""
import json
import math
import pathlib
import re
import urllib.parse

MODES = ("desc", "asc", "file-desc", "file-asc", "hour-desc", "hour-asc")
FILTERS = {"all", "approved", "available", "pending", "processing", "unavailable", "failed", "deleted", "completed"}
MEDIA_TYPES = {"all", "movie", "tv"}
BATCH = 500
NUMBER = re.compile(r"^(0|[1-9][0-9]{0,6})$")


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else 0


# total: bytes on disk (else queued); file: per file (a film is one file); hour: per hour of running time (same as request-sizes.js)
def sort_value(entry, mode):
    if not isinstance(entry, dict):
        return None
    metric = mode.split("-")[0]
    size = num(entry.get("size"))
    if metric not in ("file", "hour"):
        return size if size > 0 else num(entry.get("queued")) or None
    if size <= 0:
        return None
    files = num(entry.get("files")) if num(entry.get("files")) > 0 else 1 if "of" not in entry else 0
    if not files:
        return None
    if metric == "file":
        return size / files
    runtime = num(entry.get("runtime"))
    return size / (files * runtime) if runtime > 0 else None


def ranked(requests, sizes, mode):
    desc = mode.endswith("desc")
    values = [sort_value(sizes.get(str(r.get("id"))), mode) if isinstance(r, dict) else None for r in requests]
    order = sorted(range(len(requests)), key=lambda i: (values[i] is None, 0 if values[i] is None else -values[i] if desc else values[i], i))
    return [requests[i] for i in order]


def read_sizes(path):
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return {}
    sizes = data.get("sizes") if isinstance(data, dict) else None
    return sizes if isinstance(sizes, dict) else {}


def params(query):
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query, keep_blank_values=True).items()}
    take, skip = q.get("take", ""), q.get("skip", "0")
    if not NUMBER.match(take) or not 1 <= int(take) <= 100 or not NUMBER.match(skip):
        return None
    if q.get("hg_size") not in MODES or q.get("filter", "all") not in FILTERS or q.get("mediaType", "all") not in MEDIA_TYPES:
        return None
    by = q.get("requestedBy")
    if by is not None and not NUMBER.match(by):
        return None
    return {"take": int(take), "skip": int(skip), "mode": q["hg_size"], "filter": q.get("filter", "all"), "mediaType": q.get("mediaType", "all"), "requestedBy": by}


def every_request(dn, p, auth, deps):
    out, skip = [], 0
    while True:
        path = f"/request?take={BATCH}&skip={skip}&filter={p['filter']}&mediaType={p['mediaType']}&sort=added&sortDirection=desc"
        page = deps.seerr(path + (f"&requestedBy={p['requestedBy']}" if p["requestedBy"] else ""), auth)
        results = page.get("results") if isinstance(page, dict) else None
        if not isinstance(results, list):
            raise dn.ApiError("seerr: no results")
        out.extend(results)
        total = num((page.get("pageInfo") or {}).get("results"))
        skip += BATCH
        if not results or skip >= total:
            return out


def handle(dn, method, path, headers, body, deps):
    if method != "GET":
        return 404, {"error": "not found"}
    auth = dn.auth_of(headers)
    try:
        me = deps.seerr("/auth/me", auth) if auth else None
    except dn.ApiError:
        me = None
    if not isinstance(me, dict) or not isinstance(me.get("id"), int):
        return 403, {"error": "forbidden"}
    p = params(urllib.parse.urlsplit(path).query)
    if not p:
        return 400, {"error": "bad request"}
    try:
        everything = every_request(dn, p, auth, deps)
    except dn.ApiError:
        return 502, {"error": "seerr not reachable"}
    sizes = read_sizes(deps.request_state_file) if deps.request_state_file else {}
    rows = ranked(everything, sizes, p["mode"])
    take, skip = p["take"], p["skip"]
    return 200, {"pageInfo": {"pages": math.ceil(len(rows) / take), "pageSize": take, "results": len(rows), "page": math.ceil(skip / take) + 1},
                 "results": rows[skip:skip + take]}
