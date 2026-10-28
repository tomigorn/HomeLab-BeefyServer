#!/usr/bin/env python3
"""Original audio as the default track (README "Languages"): for every Radarr movie / Sonarr episode file (.mkv) the audio track in the title's original language gets flag-default and the name "<Language> (Original)", every other audio track loses flag-default.
Jellyfin plays the preferred language, else the default-flagged track; German DL releases flag the German dub, this fixes the file. Files with more than one hard link (still seeding) are skipped until the seed is gone.
Usage: scripts/original-audio.py [--dry-run] [--queue]
--queue: only the files Radarr/Sonarr queued on import/upgrade (scripts/original-audio-hook.sh writes <id><TAB><container path> to <appdata>/<app>/original-audio.queue); files still seeding are dropped, the nightly full run gets them."""
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

DATA_HOST = os.environ.get("OA_DATA_HOST", "/srv/video")
JELLYFIN = os.environ.get("OA_JELLYFIN", "http://127.0.0.1:8096")
JELLYFIN_KEY = pathlib.Path(os.environ.get("OA_JELLYFIN_KEY", pathlib.Path(__file__).resolve().parent.parent.parent / "Jellyfin" / "secrets" / "api-key"))

LANGUAGES = {
    "english": "eng", "german": "ger deu", "french": "fre fra", "spanish": "spa", "italian": "ita", "portuguese": "por",
    "dutch": "dut nld", "flemish": "dut nld", "swedish": "swe", "danish": "dan", "norwegian": "nor nob nno", "finnish": "fin",
    "icelandic": "ice isl", "russian": "rus", "polish": "pol", "czech": "cze ces", "slovak": "slo slk", "hungarian": "hun",
    "romanian": "rum ron", "bulgarian": "bul", "greek": "gre ell", "turkish": "tur", "ukrainian": "ukr", "serbian": "srp",
    "croatian": "hrv", "slovenian": "slv", "bosnian": "bos", "macedonian": "mac mkd", "albanian": "alb sqi", "lithuanian": "lit",
    "latvian": "lav", "estonian": "est", "arabic": "ara", "hebrew": "heb", "persian": "per fas", "hindi": "hin", "bengali": "ben",
    "tamil": "tam", "telugu": "tel", "malayalam": "mal", "kannada": "kan", "marathi": "mar", "punjabi": "pan", "urdu": "urd",
    "japanese": "jpn", "korean": "kor", "chinese": "chi zho", "mandarin": "chi zho", "cantonese": "chi zho", "thai": "tha",
    "vietnamese": "vie", "indonesian": "ind", "malay": "may msa", "tagalog": "tgl fil", "filipino": "fil tgl", "latin": "lat",
    "irish": "gle", "welsh": "wel cym", "catalan": "cat", "basque": "baq eus", "galician": "glg", "georgian": "geo kat",
    "armenian": "arm hye", "mongolian": "mon", "nepali": "nep", "swahili": "swa", "afrikaans": "afr",
}


class ToolError(Exception):
    pass


def language_codes(name):
    codes = LANGUAGES.get((name or "").strip().lower())
    return set(codes.split()) if codes else None


def parse_tracks(info):
    out = []
    for tr in info.get("tracks", []):
        if tr.get("type") != "audio":
            continue
        p = tr.get("properties", {})
        out.append({"n": len(out) + 1, "lang": p.get("language") or "und", "name": p.get("track_name") or "",
                    "default": bool(p.get("default_track")), "commentary": bool(p.get("flag_commentary"))})
    return out


def is_commentary(tr):
    return re.search(r"commentary|kommentar|commentaire", tr["name"], re.I) is not None


def decide(language, tracks, nlink=1, suffix=".mkv"):
    if suffix.lower() != ".mkv":
        return {"action": "skip", "reason": "not mkv, skipped"}
    if nlink > 1:
        return {"action": "skip", "reason": "still seeding (hard links), later"}
    codes = language_codes(language)
    if not codes:
        return {"action": "skip", "reason": f"unknown language {language!r}"}
    hits = [x for x in tracks if x["lang"] in codes]
    if not hits:
        return {"action": "skip", "reason": f"no {language} audio track (dub only or und)"}
    cands = [x for x in hits if not (x.get("commentary") or is_commentary(x))]
    if not cands:
        return {"action": "skip", "reason": f"only {language} commentary tracks"}
    keep = next((x for x in cands if x["default"]), cands[0])
    edits = []
    for x in tracks:
        args = []
        if x is keep:
            if not x["default"]:
                args.append("flag-default=1")
            name = x["name"]
            if language.lower() in name.lower():
                if "original" not in name.lower():
                    args.append(f"name={name} (Original)")
            elif name != f"{language} (Original)":
                args.append(f"name={language} (Original)")
        elif x["default"]:
            args.append("flag-default=0")
        if is_commentary(x) and not x.get("commentary"):
            args.append("flag-commentary=1")
        if args:
            edits.append((f"a{x['n']}", args))
    if not edits:
        return {"action": "ok"}
    return {"action": "set", "track": keep["n"], "candidates": len(cands), "edits": edits}


def describe(plan, language):
    if plan["action"] == "ok":
        return "ok"
    if plan["action"] == "set":
        more = f" (of {plan['candidates']} candidates)" if plan.get("candidates", 1) > 1 else ""
        return f"set {language} track {plan['track']} default{more}"
    return "skip: " + plan["reason"]


def propedit_args(path, plan):
    args = [path]
    for sel, sets in plan["edits"]:
        args += ["--edit", f"track:{sel}"]
        for s in sets:
            args += ["--set", s]
    return args


def client(config):
    xml = pathlib.Path(config).read_text()
    get = lambda k: (re.search(f"<{k}>([^<]*)</{k}>", xml) or [None, ""])[1]
    base = f"http://localhost:{get('Port')}{get('UrlBase')}/api/v3"

    def call(path, query=None):
        url = base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"X-Api-Key": get("ApiKey")}), timeout=120) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise ToolError(f"GET {path}: {e}") from None
    return call


def parse_queue_line(line):
    """(id, container path) of one queue line, None for blank or malformed ones."""
    ident, _, path = line.strip("\r\n").partition("\t")
    return (ident.strip(), path.strip()) if ident.strip() and path.strip() else None


def claim_queue(path):
    """Takes the queue file (renamed first, so the app can keep appending) and returns its unique (id, path) items in order."""
    work = pathlib.Path(str(path) + ".work")
    if pathlib.Path(path).exists():
        with open(work, "a") as w:
            w.write(pathlib.Path(path).read_text())
        pathlib.Path(path).unlink()
    if not work.exists():
        return []
    items = []
    for line in work.read_text().splitlines():
        item = parse_queue_line(line)
        if item and item not in items:
            items.append(item)
    work.unlink()
    return items


def host_path(app_path):
    return str(pathlib.Path(DATA_HOST) / pathlib.Path(app_path).relative_to("/data"))


def media_files(app, api):
    """(label, language name, host path) per file."""
    if app == "radarr":
        for m in api("/movie"):
            if m.get("movieFile"):
                yield m["title"], (m.get("originalLanguage") or {}).get("name"), host_path(m["movieFile"]["path"])
    else:
        for s in api("/series"):
            lang = (s.get("originalLanguage") or {}).get("name")
            for f in api("/episodefile", {"seriesId": s["id"]}):
                yield s["title"], lang, host_path(f["path"])


def mkvmerge_info(path):
    r = subprocess.run(["mkvmerge", "-J", path], capture_output=True, text=True)
    if r.returncode > 1:
        raise ToolError(f"mkvmerge {path}: {r.stderr.strip() or r.stdout.strip()}")
    return json.loads(r.stdout)


def restore_mtime(path, ns):
    """Files of the apps' uids need CAP_FOWNER (unit), which mergerfs drops, so those go to the file's branch path."""
    try:
        os.utime(path, ns=(ns, ns))
    except PermissionError as denied:
        try:
            branch = os.getxattr(path, "user.mergerfs.fullpath").decode()
        except OSError:
            raise denied from None
        os.utime(branch, ns=(ns, ns))


def apply(path, plan):
    st = os.stat(path)
    r = subprocess.run(["mkvpropedit", *propedit_args(path, plan)], capture_output=True, text=True)
    restore_mtime(path, st.st_mtime_ns)
    if r.returncode != 0:
        raise ToolError(f"mkvpropedit {path}: {r.stdout.strip()} {r.stderr.strip()}")


def jellyfin(method, path, key):
    req = urllib.request.Request(JELLYFIN + path, method=method, headers={"Authorization": f'MediaBrowser Token="{key}"'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r) if r.length != 0 and method == "GET" else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise ToolError(f"Jellyfin {method} {path}: {e}") from None


def refresh_jellyfin(paths):
    """Re-probe the changed files (a plain library scan skips files whose mtime is unchanged)."""
    key = JELLYFIN_KEY.read_text().strip()
    items = jellyfin("GET", "/Items?Recursive=true&IncludeItemTypes=Movie,Episode&Fields=Path", key)["Items"]
    ids = {i["Path"]: i["Id"] for i in items if i.get("Path")}
    missing = 0
    for p in paths:
        jf = "/media/" + str(pathlib.Path(p).relative_to(pathlib.Path(DATA_HOST) / "media"))
        if jf in ids:
            jellyfin("POST", f"/Items/{ids[jf]}/Refresh?MetadataRefreshMode=FullRefresh&ImageRefreshMode=None&ReplaceAllMetadata=false", key)
        else:
            missing += 1
    if missing:
        jellyfin("POST", "/Library/Refresh", key)
    return len(paths) - missing


def handle_file(app, label, lang, path, dry, changed):
    """One file: decide, apply, print. Returns 1 on error, else 0."""
    name = pathlib.Path(path).name
    try:
        st = os.stat(path)
        tracks = parse_tracks(mkvmerge_info(path)) if path.lower().endswith(".mkv") and st.st_nlink == 1 and language_codes(lang) else []
        plan = decide(lang, tracks, st.st_nlink, pathlib.Path(path).suffix)
        if plan["action"] == "set" and not dry:
            apply(path, plan)
            changed.append(path)
        print(f"{app}: {label}: {name}: {describe(plan, lang)}{' (dry run)' if dry and plan['action'] == 'set' else ''}")
        return 0
    except (ToolError, OSError, ValueError, subprocess.CalledProcessError) as e:
        print(f"{app}: {label}: {name}: error: {e}", file=sys.stderr)
        return 1


def queued_files(app, api, config_dir):
    """(label, language name, host path) per queued import; titles the app no longer knows are dropped."""
    for ident, app_path in claim_queue(str(pathlib.Path(config_dir) / "original-audio.queue")):
        try:
            title = api(("/movie/" if app == "radarr" else "/series/") + ident)
        except ToolError as e:
            print(f"{app}: queued {app_path}: {e}", file=sys.stderr)
            continue
        yield title["title"], (title.get("originalLanguage") or {}).get("name"), host_path(app_path)


def main(argv):
    dry = "--dry-run" in argv
    queue = "--queue" in argv
    rc, changed = 0, []
    for app in ("radarr", "sonarr"):
        config = os.environ.get(f"OA_{app.upper()}_CONFIG", f"/srv/appdata/{app}/config.xml")
        try:
            api = client(config)
            files = list(queued_files(app, api, pathlib.Path(config).parent) if queue else media_files(app, api))
        except (ToolError, OSError) as e:
            print(f"{app}: API error: {e}", file=sys.stderr)
            rc = 1
            continue
        for label, lang, path in files:
            rc |= handle_file(app, label, lang, path, dry, changed)
    if changed:
        try:
            n = refresh_jellyfin(changed)
            print(f"jellyfin: {n} of {len(changed)} changed items refreshed")
        except (ToolError, OSError) as e:
            print(e, file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
