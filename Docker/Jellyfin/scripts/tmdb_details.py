"""Slim TMDB details for the details overlay (home-placeholders/details.js), from Seerr's TMDB access (no own key): texts in en + de, cast, scores, trailers.
Used by popular.py (Popular pages) and upcoming-popularity.py (Coming soon); both write a sidecar next to allow.json that the popular backend serves by opaque id only."""
import json
import os
import re
import secrets
import urllib.error

IMG_RE = re.compile(r"^/[A-Za-z0-9_.-]+$")
IMDB_RE = re.compile(r"^tt\d+$")
YT_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
VIDEO_TYPES = {"Trailer": 0, "Teaser": 1}
TRAILERS_PER_LANG = 5
CAST = 10
COUNTRY = "CH"
FAILURES = (OSError, urllib.error.URLError, ValueError, KeyError, TypeError)


def _img(path):
    return path if isinstance(path, str) and IMG_RE.match(path) else None


def _num(v, digits=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return round(float(v), digits) if digits is not None else int(v)


def videos(en, de):
    """YouTube trailers and teasers of the en and de answers, each language trailers first then larger first, at most TRAILERS_PER_LANG each; a key seen in en is not repeated for de."""
    seen, out = set(), []
    for lang, lst in (("en", en), ("de", de)):
        mine = []
        for v in lst or []:
            key = v.get("key")
            if v.get("site") != "YouTube" or v.get("type") not in VIDEO_TYPES or not isinstance(key, str) or not YT_RE.match(key) or key in seen:
                continue
            seen.add(key)
            mine.append({"key": key, "name": str(v.get("name") or "")[:200], "type": v["type"], "size": _num(v.get("size")) or 0, "lang": lang})
        mine.sort(key=lambda x: (VIDEO_TYPES[x["type"]], -x["size"]))
        out += mine[:TRAILERS_PER_LANG]
    return out


def _cert(kind, en):
    if kind == "movie":
        for r in (en.get("releases") or {}).get("results") or []:
            if r.get("iso_3166_1") == COUNTRY:
                return next((d["certification"] for d in r.get("release_dates") or [] if d.get("certification")), None)
        return None
    return next((r.get("rating") or None for r in (en.get("contentRatings") or {}).get("results") or [] if r.get("iso_3166_1") == COUNTRY), None)


def _runtime(kind, en):
    if kind == "movie":
        return _num(en.get("runtime")) or None
    for v in (en.get("episodeRunTime") or []) + [(en.get("lastEpisodeToAir") or {}).get("runtime"), (en.get("nextEpisodeToAir") or {}).get("runtime")]:
        if _num(v):
            return _num(v)
    return None


def project(kind, tmdb, en, de, ratings):
    """The overlay's data for one title; every image path, IMDb id and trailer key checked here (and again in the browser)."""
    de = de or {}
    title = en.get("title") or en.get("name") or ""
    imdb = en.get("imdbId") or (en.get("externalIds") or {}).get("imdbId")
    r = ratings or {}
    rt = r.get("rt") if kind == "movie" else r
    rt = rt or {}
    cast = sorted((en.get("credits") or {}).get("cast") or [], key=lambda c: c.get("order") if isinstance(c.get("order"), int) else 999)[:CAST]
    poster = _img(en.get("posterPath"))
    return {
        "k": kind, "id": int(tmdb),
        "t": {"en": title, "de": de.get("title") or de.get("name") or title},
        "o": {"en": en.get("overview") or "", "de": de.get("overview") or ""},
        "y": int((en.get("releaseDate") or en.get("firstAirDate") or "0")[:4] or 0) or None,
        "g": {"en": [g["name"] for g in en.get("genres") or [] if g.get("name")], "de": [g["name"] for g in de.get("genres") or en.get("genres") or [] if g.get("name")]},
        "rt": _runtime(kind, en),
        "ns": _num(en.get("numberOfSeasons")) if kind == "tv" else None,
        "cert": _cert(kind, en),
        "p": {"en": poster, "de": _img(de.get("posterPath")) or poster},
        "b": _img(en.get("backdropPath")),
        "imdb": imdb if isinstance(imdb, str) and IMDB_RE.match(imdb) else None,
        "sc": {"imdb": _num((r.get("imdb") or {}).get("criticsScore"), 1) if kind == "movie" else None, "rtc": _num(rt.get("criticsScore")), "rta": _num(rt.get("audienceScore")),
               "tmdb": _num(en.get("voteAverage"), 1) if en.get("voteCount") else None},
        "cast": [{"n": str(c.get("name") or ""), "c": str(c.get("character") or ""), "p": _img(c.get("profilePath"))} for c in cast if c.get("name")],
        "v": videos(en.get("relatedVideos"), de.get("relatedVideos")),
    }


def ratings(seerr, kind, tmdb):
    """Rotten Tomatoes (+ IMDb for films) through Seerr; None when Seerr has none (404) or fails: never a failed title."""
    try:
        return seerr(f"/api/v1/movie/{int(tmdb)}/ratingscombined" if kind == "movie" else f"/api/v1/tv/{int(tmdb)}/ratings")
    except FAILURES:
        return None


def fetch(seerr, kind, tmdb):
    """(English Seerr answer, projection). The English and German answers may raise; the ratings never do."""
    en = seerr(f"/api/v1/{kind}/{int(tmdb)}?language=en")
    de = seerr(f"/api/v1/{kind}/{int(tmdb)}?language=de")
    return en, project(kind, tmdb, en, de, ratings(seerr, kind, tmdb))


def assign(path, refs):
    """{ref: opaque token}: the token of the previous sidecar for the same ref, else a new random one."""
    try:
        old = json.loads(path.read_text()).get("entries") or {}
    except (OSError, ValueError, AttributeError):
        old = {}
    known = {v.get("ref"): k for k, v in old.items() if isinstance(v, dict) and TOKEN_RE.match(k)}
    return {ref: known.get(ref) or secrets.token_urlsafe(18) for ref in refs}


def write_sidecar(path, stamp, infos, tokens=None):
    """{ref: projection} → {"updated", "entries": {opaque token: projection + ref}} (atomic, 0644: the backend runs as another uid). Returns {ref: token}."""
    tokens = tokens or assign(path, infos)
    data = {"updated": stamp, "entries": {tokens[ref]: dict(info, ref=ref) for ref, info in infos.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return tokens
