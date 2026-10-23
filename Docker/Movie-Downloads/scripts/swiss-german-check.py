#!/usr/bin/env python3
"""Live check of the Swiss German setup in Radarr (README "Swiss German"): profile, scores, release profile, hook, tagged titles, ranking. Read-only, never searches.
Usage: scripts/swiss-german-check.py   (exit 1 on any FAIL; lists tagged films still waiting for a Swiss German release)"""
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = os.environ.get("SG_RADARR_CONFIG", "/srv/appdata/radarr/config.xml")
PROFILE = "Swiss German (Mundart) only - any quality"
TAG = "audio-swiss-german"
xml = pathlib.Path(CONFIG).read_text()
KEY = re.search(r"<ApiKey>([^<]+)</ApiKey>", xml).group(1)
BASE = f"http://localhost:{re.search(r'<Port>([^<]+)</Port>', xml).group(1)}{(re.search(r'<UrlBase>([^<]*)</UrlBase>', xml) or [None, ''])[1]}/api/v3"
GSW = next(l.split("\t")[2] for l in (HERE / "languages.tsv").read_text(encoding="utf-8").splitlines() if l.startswith("gsw\t"))
fails = 0


def api(path, **query):
    url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"X-Api-Key": KEY}), timeout=30) as r:
        return json.load(r)


def check(name, cond, detail=""):
    global fails
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f": {detail}" if detail and not cond else ""))
    fails += not cond


profiles = api("/qualityprofile")
sw = next((p for p in profiles if p["name"] == PROFILE), None)
print("Profile")
check(f"'{PROFILE}' exists", sw is not None)
if sw is None:
    sys.exit(1)
score = {f["name"]: f["score"] for f in sw["formatItems"]}
allowed = [i for i in sw["items"] if i["allowed"]]
check("quality Unknown allowed (XVID names without a source)", "Unknown" in [q["quality"]["name"] for i in allowed for q in i.get("items", [])])
check("language Any (a Swiss German dub of a foreign film passes)", sw["language"]["name"] == "Any", sw["language"]["name"])
check("one allowed quality group (a Swiss German release replaces a better non-Swiss file)", len(allowed) == 1 and len(allowed[0].get("items", [])) > 10, [i.get("name") for i in allowed])
check("upgrades on, until Swiss German + 2160p", sw["upgradeAllowed"] and sw["cutoffFormatScore"] == score.get("Audio: Swiss German", 0) + score.get("Resolution: 2160p", 0), sw["cutoffFormatScore"])
check("Audio: Swiss German = 1000000", score.get("Audio: Swiss German") == 1000000, score.get("Audio: Swiss German"))
check("German (dub only) and Language: Not Original = 0", score.get("German") == 0 and score.get("Language: Not Original") == 0, (score.get("German"), score.get("Language: Not Original")))
check("rejects (LQ, BR-DISK, Upscaled, DV w/o HDR) <= -2000000", all(score.get(n, 0) <= -2000000 for n in ("LQ", "BR-DISK", "Upscaled", "DV (w/o HDR fallback)")))
res = [score.get(f"Resolution: {r}", 0) for r in ("2160p", "1080p", "720p", "SD")]
check("resolution ranks 2160p > 1080p > 720p > SD = DVD > 0", res == sorted(res, reverse=True) and len(set(res)) == 4 and res[-1] == score.get("Resolution: DVD") > 0, res)
others = [p["name"] for p in profiles if p["name"] != PROFILE and any(f["name"] == "Audio: Swiss German" and f["score"] for f in p["formatItems"])]
check("Audio: Swiss German is 0 in every other profile", not others, others)

print("Tag, release profile, hook")
tags = {t["label"]: t["id"] for t in api("/tag")}
check(f"tag {TAG} exists", TAG in tags)
rp = [r for r in api("/releaseprofile") if r["name"].startswith(TAG + ":")]
check(f"release profile '{TAG}: …' enabled, only on that tag, term = languages.tsv", len(rp) == 1 and rp[0]["enabled"] and rp[0]["tags"] == [tags.get(TAG)] and rp[0]["required"] == [f"/{GSW}/i"], rp)
hook = [n for n in api("/notification") if n["name"] == "Language policy"]
check("Connect 'Language policy' runs on movie added", len(hook) == 1 and hook[0]["onMovieAdded"], hook and hook[0]["onMovieAdded"])
timer = subprocess.run(["systemctl", "is-active", "request-tags.timer"], capture_output=True, text=True).stdout.strip()
check("request-tags.timer active (Seerr tags on existing titles)", timer == "active", timer)

print("Titles")
movies = api("/movie")
tagged = [m for m in movies if tags.get(TAG) in m.get("tags", [])]
wrong = [m["title"] for m in tagged if m["qualityProfileId"] != sw["id"]]
untagged = [m["title"] for m in movies if m["qualityProfileId"] == sw["id"] and tags.get(TAG) not in m.get("tags", [])]
check(f"every {TAG} film is on the Swiss German profile", not wrong, wrong)
check(f"every film on the Swiss German profile has {TAG}", not untagged, untagged)

print("Ranking (GET /parse, never grabs)")


def total(title):
    return sum(score.get(cf["name"], 0) for cf in api("/parse", title=title).get("customFormats", []))


swiss_sd = total("Swiss.Check.2004.SwissGerman.DVDRip.x264-TEST")
german_4k = total("Swiss.Check.2004.German.DL.2160p.UHD.BluRay.HDR.DTS-HD.MA.x265-TEST")
swiss_1080 = total("Swiss.Check.2004.SwissGerman.1080p.BluRay.x264-TEST")
swiss_unknown = total("Swiss.Check.2004.SwissGerman.XVID-TEST")
swiss_720 = total("Swiss.Check.2004.SwissGerman.720p.WEB.h264-TEST")
check("Swiss German DVD beats High German 2160p HDR (upgrade over an existing file)", swiss_sd > german_4k, (swiss_sd, german_4k))
check("Swiss German 1080p > DVD > Unknown quality (Unknown is allowed, ranks lowest)", swiss_1080 > swiss_sd > swiss_unknown, (swiss_1080, swiss_sd, swiss_unknown))
check("Swiss German 1080p beats Swiss German 720p", swiss_1080 > swiss_720, (swiss_1080, swiss_720))

waiting = [m["title"] for m in tagged if not re.search(GSW, (m.get("movieFile") or {}).get("sceneName") or (m.get("movieFile") or {}).get("relativePath") or "", re.I)]
print(f"Waiting for a Swiss German release ({len(waiting)}): {', '.join(waiting) or '-'}")
print(f"{'FAILED' if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
