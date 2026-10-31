#!/usr/bin/env python3
"""Live check of the Swiss German setup in Radarr and Sonarr (README "Swiss German"): profile, scores, release profile, hook, tagged titles, ranking. Read-only, never searches.
Usage: scripts/swiss-german-check.py [radarr|sonarr]   (exit 1 on any FAIL; lists tagged titles still waiting for a Swiss German release)"""
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
PROFILE = "Swiss German (Mundart) only - any quality"
TAG = "audio-swiss-german"
GSW = next(l.split("\t")[2] for l in (HERE / "languages.tsv").read_text(encoding="utf-8").splitlines() if l.startswith("gsw\t"))
APPS = {
    "radarr": {"config": os.environ.get("SG_RADARR_CONFIG", "/srv/appdata/radarr/config.xml"), "kind": "film", "event": "onMovieAdded",
               "name": "Swiss.Check.2004.{}-TEST"},
    "sonarr": {"config": os.environ.get("SG_SONARR_CONFIG", "/srv/appdata/sonarr/config.xml"), "kind": "series", "event": "onSeriesAdd",
               "name": "Swiss.Check.S01E01.{}-TEST"},
}
fails = 0


def check(name, cond, detail=""):
    global fails
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f": {detail}" if detail and not cond else ""))
    fails += not cond


def client(config):
    xml = pathlib.Path(config).read_text()
    key = re.search(r"<ApiKey>([^<]+)</ApiKey>", xml).group(1)
    base = f"http://localhost:{re.search(r'<Port>([^<]+)</Port>', xml).group(1)}{(re.search(r'<UrlBase>([^<]*)</UrlBase>', xml) or [None, ''])[1]}/api/v3"

    def api(path, **query):
        url = base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"X-Api-Key": key}), timeout=60) as r:
            return json.load(r)
    return api


def waiting_movies(api, tagged):
    return [m["title"] for m in tagged if not re.search(GSW, (m.get("movieFile") or {}).get("sceneName") or (m.get("movieFile") or {}).get("relativePath") or "", re.I)]


def waiting_series(api, tagged):
    out = []
    for s in tagged:
        st = s.get("statistics") or {}
        files = api("/episodefile", seriesId=s["id"]) if st.get("episodeFileCount") else []
        swiss = [f for f in files if re.search(GSW, f.get("sceneName") or f.get("relativePath") or "", re.I)]
        missing = (st.get("episodeCount") or 0) - len(swiss)
        if missing > 0:
            out.append(f"{s['title']} ({missing} of {st.get('episodeCount') or 0} episodes)")
    return out


def check_app(app, cfg):
    print(f"== {app}")
    api = client(cfg["config"])
    profiles = api("/qualityprofile")
    sw = next((p for p in profiles if p["name"] == PROFILE), None)
    print("Profile")
    check(f"'{PROFILE}' exists", sw is not None)
    if sw is None:
        return
    score = {f["name"]: f["score"] for f in sw["formatItems"]}
    allowed = [i for i in sw["items"] if i["allowed"]]
    check("quality Unknown allowed (XVID names without a source)", "Unknown" in [q["quality"]["name"] for i in allowed for q in i.get("items", [])])
    if "language" in sw:
        check(f"language Any (a Swiss German dub of a foreign {cfg['kind']} passes)", sw["language"]["name"] == "Any", sw["language"]["name"])
    check("one allowed quality group (a Swiss German release replaces a better non-Swiss file)", len(allowed) == 1 and len(allowed[0].get("items", [])) > 10, [i.get("name") for i in allowed])
    check("upgrades on, until Swiss German + 2160p", sw["upgradeAllowed"] and sw["cutoffFormatScore"] == score.get("Audio: Swiss German", 0) + score.get("Resolution: 2160p", 0), sw["cutoffFormatScore"])
    check("Audio: Swiss German = 1000000", score.get("Audio: Swiss German") == 1000000, score.get("Audio: Swiss German"))
    # Radarr: own `Language: Not Original (sound film)` (silent films exempt) replaced the TRaSH one.
    no = "Language: Not Original (sound film)" if app == "radarr" else "Language: Not Original"
    check(f"German (dub only) and {no} = 0", score.get("German") == 0 and score.get(no) == 0, (score.get("German"), score.get(no)))
    check("rejects (LQ, BR-DISK, Upscaled, DV w/o HDR) <= -2000000", all(score.get(n, 0) <= -2000000 for n in ("LQ", "BR-DISK", "Upscaled", "DV (w/o HDR fallback)")))
    res = [score.get(f"Resolution: {r}", 0) for r in ("2160p", "1080p", "720p", "SD")]
    # Sonarr gives DVD a resolution (480p, so Resolution: SD); Radarr does not, hence its own Resolution: DVD.
    dvd = score.get("Resolution: DVD", res[-1]) if app == "radarr" else res[-1]
    check("resolution ranks 2160p > 1080p > 720p > SD = DVD > 0", res == sorted(res, reverse=True) and len(set(res)) == 4 and res[-1] == dvd > 0, res)
    others = [p["name"] for p in profiles if p["name"] != PROFILE and any(f["name"] == "Audio: Swiss German" and f["score"] for f in p["formatItems"])]
    check("Audio: Swiss German is 0 in every other profile", not others, others)

    print("Tag, release profile, hook")
    tags = {t["label"]: t["id"] for t in api("/tag")}
    check(f"tag {TAG} exists", TAG in tags)
    rp = [r for r in api("/releaseprofile") if r["name"].startswith(TAG + ":")]
    check(f"release profile '{TAG}: …' enabled, only on that tag, term = languages.tsv", len(rp) == 1 and rp[0]["enabled"] and rp[0]["tags"] == [tags.get(TAG)] and rp[0]["required"] == [f"/{GSW}/i"], rp)
    hook = [n for n in api("/notification") if n["name"] == "Language policy"]
    check(f"Connect 'Language policy' runs {cfg['event']}", len(hook) == 1 and hook[0][cfg["event"]], hook and hook[0][cfg["event"]])

    print("Titles")
    titles = api("/movie" if app == "radarr" else "/series")
    tagged = [t for t in titles if tags.get(TAG) in t.get("tags", [])]
    wrong = [t["title"] for t in tagged if t["qualityProfileId"] != sw["id"]]
    untagged = [t["title"] for t in titles if t["qualityProfileId"] == sw["id"] and tags.get(TAG) not in t.get("tags", [])]
    check(f"every {TAG} {cfg['kind']} is on the Swiss German profile", not wrong, wrong)
    check(f"every {cfg['kind']} on the Swiss German profile has {TAG}", not untagged, untagged)

    print("Ranking (GET /parse, never grabs)")

    def total(words):
        return sum(score.get(cf["name"], 0) for cf in api("/parse", title=cfg["name"].format(words)).get("customFormats", []))

    swiss_sd = total("SwissGerman.DVDRip.x264")
    german_4k = total("German.DL.2160p.UHD.BluRay.HDR.DTS-HD.MA.x265")
    swiss_1080 = total("SwissGerman.1080p.BluRay.x264")
    swiss_unknown = total("SwissGerman.XVID")
    swiss_720 = total("SwissGerman.720p.WEB.h264")
    check("Swiss German DVD beats High German 2160p HDR (upgrade over an existing file)", swiss_sd > german_4k, (swiss_sd, german_4k))
    check("Swiss German 1080p > DVD > Unknown quality (Unknown is allowed, ranks lowest)", swiss_1080 > swiss_sd > swiss_unknown, (swiss_1080, swiss_sd, swiss_unknown))
    check("Swiss German 1080p beats Swiss German 720p", swiss_1080 > swiss_720, (swiss_1080, swiss_720))

    waiting = (waiting_movies if app == "radarr" else waiting_series)(api, tagged)
    print(f"Waiting for a Swiss German release ({len(waiting)}): {', '.join(waiting) or '-'}")


def main():
    for app in sys.argv[1:] or list(APPS):
        check_app(app, APPS[app])
    print("== both")
    timer = subprocess.run(["systemctl", "is-active", "request-tags.timer"], capture_output=True, text=True).stdout.strip()
    check("request-tags.timer active (Seerr tags on existing titles)", timer == "active", timer)
    print(f"{'FAILED' if fails else 'all checks passed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
