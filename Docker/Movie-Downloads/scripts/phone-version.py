#!/usr/bin/env python3
"""Phone version release picker (README "Phone versions", owner 2026-11-19, fastpi reports/specs/2026-11-19-phone-versions-design.md): which release a phone gets instead of a big 4K server copy. A smaller version is always a second download, never an own conversion.
Strict order, a failed rule falls through to the next option:
1. 1080p WEB-DL with HDR (HDR10 or Dolby Vision with an HDR10 fallback)
2. 1080p WEB-DL without HDR (not an x265 re-encode)
3. 1080p encode: trusted group (a TRaSH tier format), HEVC 10-bit, at least 15 MB per minute, source Blu-ray or WEB-DL
4. a 4K release at least 40 % smaller than the server copy
5. the server copy itself
Every option: no remux, no audio-description release, no hardcoded subtitles (HC, KORSUB, hardsub in the name), none of the title profile's negative formats (LQ, Upscaled, bad groups, DV without fallback, ...), German, English or the original language present, the right title.
The candidates are Radarr/Sonarr's own release search (GET /release): their parsed quality, formats and languages. Nothing is grabbed here.
Usage: scripts/phone-version.py --movie <radarr id> | --episode <sonarr episode id>   (one indexer search, prints the pick and why the others failed) | -h"""
import importlib.util
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("language_block", HERE / "language-block.py")
lb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lb)

# a release search asks every indexer through the arr and can take minutes
RELEASE_TIMEOUT = 600
MIN_BYTES_PER_MINUTE = 15_000_000
MAX_4K_SHARE = 0.60
# negative profile formats the order itself decides on (x265 re-encodes go to option 3, languages to the language rule)
HANDLED = {"x265 (no HDR/DV)", "x265 (HD)", "Remux: smaller encode wins", "SDR", "German", "Language: Not Original", "Language: Not Original (sound film)", "v0"}
NOT_HDR = {"SDR", "x265 (no HDR/DV)", "Generated Dynamic HDR", "DV (w/o HDR fallback)"}
TIER = re.compile(r"\bTier 0\d$")
HEVC = re.compile(r"\b(x265|h\.?265|hevc)\b", re.I)
AUDIO_DESCRIPTION = re.compile(r"audio[ ._-]?description|\bdescriptive[ ._-]?audio\b", re.I)
HARDCODED_SUBS = re.compile(r"(?<![a-z0-9])(hc|hc[ ._-]?subs?|hard[ ._-]?subs?|hard[ ._-]?subbed|hard[ ._-]?coded([ ._-]?subs?)?|korsub|kor[ ._-]?sub)(?![a-z0-9])", re.I)
WRONG_TITLE = re.compile(r"unknown (movie|series)|unable to match|wrong (movie|series|episode)", re.I)
SPEAKABLE = {"German", "English", "Swiss German"}
OPTIONS = {1: "1080p WEB-DL with HDR", 2: "1080p WEB-DL", 3: "1080p encode (strict rules)", 4: "smaller 4K release", 5: "the server copy"}


def bad_formats(profile):
    return {f["name"] for f in profile.get("formatItems") or [] if f.get("score", 0) < 0} - HANDLED


def is_hdr(cfs):
    return any(("HDR" in c or c.startswith("DV") or c in ("PQ", "HLG")) and c not in NOT_HDR for c in cfs)


def common(r, title, bad):
    """Why a release fails the rules every option shares, or None."""
    q = r["quality"]
    if q.get("modifier") == "remux" or q["name"].startswith("Remux"):
        return "remux"
    if any(WRONG_TITLE.search(x) for x in r.get("rejections") or []):
        return "another title"
    if AUDIO_DESCRIPTION.search(r.get("title") or ""):
        return "audio description"
    if HARDCODED_SUBS.search(r.get("title") or ""):
        return "hardcoded subtitles"
    hit = sorted(set(r.get("customFormats") or []) & (bad - HANDLED))
    if hit:
        return "bad tag: " + ", ".join(hit)
    langs = set(r.get("languages") or [])
    if not langs & (SPEAKABLE | {title.get("original")}):
        return "no German, English or original-language audio (" + (", ".join(sorted(langs)) or "none") + ")"
    return None


def encode_fails(r, title):
    cfs, why = set(r.get("customFormats") or []), []
    if not r["quality"]["name"].startswith(("Bluray-", "WEBDL-")):
        why.append("source not Blu-ray or WEB-DL")
    if not any(TIER.search(c) for c in cfs):
        why.append("no trusted group")
    if "10bit" not in cfs or not ("x265 (HD)" in cfs or HEVC.search(r.get("title") or "")):
        why.append("not HEVC 10-bit")
    if title.get("runtime") and r.get("size", 0) < MIN_BYTES_PER_MINUTE * title["runtime"]:
        why.append("under 15 MB per minute")
    return why


def option(r, title):
    """(option, None) or (None, why) for a release that passed the common rules."""
    q, cfs = r["quality"], set(r.get("customFormats") or [])
    if q.get("resolution") == 2160:
        if title.get("size") and r.get("size", 0) <= MAX_4K_SHARE * title["size"]:
            return 4, None
        return None, "4K, not 40 % smaller than the server copy"
    if q.get("resolution") != 1080:
        return None, f"{q['name']}: not 1080p or 4K"
    if q["name"] == "WEBDL-1080p" and is_hdr(cfs):
        return 1, None
    if q["name"] == "WEBDL-1080p" and "x265 (no HDR/DV)" not in cfs:
        return 2, None
    why = encode_fails(r, title)
    return (3, None) if not why else (None, "encode: " + ", ".join(why))


def pick(releases, title, bad):
    """{"option", "label", "release" (None = the server copy), "why", "rejected": [{"title", "why"}]}."""
    if title.get("resolution") != 2160:
        return {"option": 5, "label": OPTIONS[5], "release": None, "why": "the server copy is not 4K", "rejected": []}
    found, rejected = {}, []
    for r in releases:
        why = common(r, title, bad)
        n = None
        if why is None:
            n, why = option(r, title)
        if n:
            found.setdefault(n, []).append(r)
        else:
            rejected.append({"title": r.get("title"), "why": why})
    for n in sorted(found):
        best = sorted(found[n], key=lambda r: (-(r.get("customFormatScore") or 0), r.get("size") or 0))[0]
        return {"option": n, "label": OPTIONS[n], "release": best, "why": None, "rejected": rejected}
    return {"option": 5, "label": OPTIONS[5], "release": None, "why": "no smaller release passed the rules", "rejected": rejected}


def flatten(r):
    """A /release record in the shape pick() reads."""
    q = (r.get("quality") or {}).get("quality") or {}
    return {"title": r.get("title"), "size": r.get("size") or 0, "indexer": r.get("indexer"), "protocol": r.get("protocol"),
            "quality": {"name": q.get("name") or "", "resolution": q.get("resolution") or 0, "modifier": q.get("modifier") or ""},
            "customFormats": [c.get("name") for c in r.get("customFormats") or []], "customFormatScore": r.get("customFormatScore") or 0,
            "languages": [x.get("name") for x in r.get("languages") or []], "rejections": list(r.get("rejections") or [])}


def server_title(app, api, ident):
    """The title facts pick() needs (runtime in minutes, original language, the server copy's size and resolution), the profile, and the /release query."""
    if app == "radarr":
        m = api("GET", f"/movie/{ident}")
        f = m.get("movieFile") or {}
        q = ((f.get("quality") or {}).get("quality") or {})
        return ({"name": f"{m.get('title')} ({m.get('year')})", "runtime": m.get("runtime") or 0, "original": (m.get("originalLanguage") or {}).get("name"),
                 "size": f.get("size") or 0, "resolution": q.get("resolution") or 0}, m.get("qualityProfileId"), {"movieId": ident})
    e = api("GET", f"/episode/{ident}")
    s = api("GET", f"/series/{e['seriesId']}")
    f = api("GET", f"/episodefile/{e['episodeFileId']}") if e.get("episodeFileId") else {}
    q = ((f.get("quality") or {}).get("quality") or {})
    return ({"name": f"{s.get('title')} S{e.get('seasonNumber', 0):02d}E{e.get('episodeNumber', 0):02d}", "runtime": e.get("runtime") or s.get("runtime") or 0,
             "original": (s.get("originalLanguage") or {}).get("name"), "size": f.get("size") or 0, "resolution": q.get("resolution") or 0},
            s.get("qualityProfileId"), {"episodeId": ident})


def main(argv):
    if not argv or "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0 if argv else 2
    if len(argv) != 2 or argv[0] not in ("--movie", "--episode") or not argv[1].isdigit():
        print("usage: scripts/phone-version.py --movie <radarr id> | --episode <sonarr episode id> | -h", file=sys.stderr)
        return 2
    app = "radarr" if argv[0] == "--movie" else "sonarr"
    api = lb.client(os.environ.get(f"LB_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml"), timeout=RELEASE_TIMEOUT)
    try:
        title, profile_id, query = server_title(app, api, int(argv[1]))
        bad = bad_formats(api("GET", f"/qualityprofile/{profile_id}"))
        releases = [flatten(r) for r in api("GET", "/release", query) or []]
    except lb.ApiError as e:
        print(f"phone-version: {e}", file=sys.stderr)
        return 1
    p = pick(releases, title, bad)
    print(f"{title['name']}: server copy {title['size'] / 1e9:.1f} GB {title['resolution']}p, {title['runtime']} min, original {title['original']}; {len(releases)} releases")
    if p["release"]:
        r = p["release"]
        print(f"pick: option {p['option']} ({p['label']}): {r['title']} · {r['size'] / 1e9:.1f} GB · {r['indexer']} · score {r['customFormatScore']}")
    else:
        print(f"pick: option 5 ({p['label']}): {p['why']}")
    for x in p["rejected"][:15]:
        print(f"  not: {x['title']}: {x['why']}")
    if len(p["rejected"]) > 15:
        print(f"  … {len(p['rejected']) - 15} more not taken")
    print("next: nothing is grabbed; the fetch step is not built yet (README \"Phone versions\")")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
