#!/usr/bin/env python3
"""Dry run of the codec minimum size (scripts/guard-size.jq, README "Codec minimum size") over the library: which existing Radarr/Sonarr files would fall below their codec minimum. Report only, nothing changes; the rule itself acts at grab time (scripts/grab-guard.sh).

Usage: scripts/codec-size-report.py [-n EXAMPLES]   (default 15 examples per app; reads /srv/appdata/<app>/config.xml, env CSR_RADARR_CONFIG / CSR_SONARR_CONFIG)
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
RULE = HERE / "guard-size.jq"


def movie_items(movies, profiles):
    names = {p["id"]: p["name"] for p in profiles}
    items = []
    for m in movies:
        f = m.get("movieFile")
        if not f:
            continue
        japanese = (m.get("originalLanguage") or {}).get("name") == "Japanese" and "Animation" in (m.get("genres") or [])
        items.append({"app": "radarr", "title": f"{m['title']} ({m.get('year')})", "file": f.get("relativePath", ""), "quality": f["quality"]["quality"]["name"],
                      "codec": (f.get("mediaInfo") or {}).get("videoCodec") or "", "size": f.get("size", 0), "runtime": m.get("runtime", 0), "episodes": 1,
                      "anime": names.get(m.get("qualityProfileId"), "").startswith("Anime") or japanese})
    return items


def episode_items(series, files, episodes):
    items = []
    for s in series:
        count = {}
        for e in episodes.get(s["id"], []):
            if e.get("episodeFileId"):
                count[e["episodeFileId"]] = count.get(e["episodeFileId"], 0) + 1
        for f in files.get(s["id"], []):
            items.append({"app": "sonarr", "title": f"{s['title']} ({s.get('year')})", "file": f.get("relativePath", ""), "quality": f["quality"]["quality"]["name"],
                          "codec": (f.get("mediaInfo") or {}).get("videoCodec") or "", "size": f.get("size", 0), "runtime": s.get("runtime", 0),
                          "episodes": count.get(f["id"], 1), "anime": s.get("seriesType") == "anime"})
    return items


def check(items):
    """Each item plus the rule's codec, min, perMin, checked (quality in scope) and ok."""
    feed = "\n".join(json.dumps({"quality": i["quality"], "title": i["file"], "codec": i["codec"], "size": i["size"], "runtime": i["runtime"], "episodes": i["episodes"]})
                     for i in items)
    out = subprocess.run(["jq", "-c", "-f", str(RULE)], input=feed, capture_output=True, text=True, check=True).stdout.splitlines()
    return [i | json.loads(o) for i, o in zip(items, out)]


def summary(results, examples=15):
    lines = []
    for app in ("radarr", "sonarr"):
        rs = [r for r in results if r["app"] == app and r["checked"]]
        if not rs:
            continue
        below = [r for r in rs if not r["ok"]]
        codecs = sorted({r["codec"] for r in rs})
        per = ", ".join(f"{c} {sum(r['codec'] == c for r in below)}/{sum(r['codec'] == c for r in rs)}" for c in codecs)
        anime = [r for r in rs if r["anime"]]
        lines.append(f"{app}: {len(rs)} files checked, {len(below)} below ({per})" + (f"; anime {sum(not r['ok'] for r in anime)} below of {len(anime)}" if anime else ""))
        for r in sorted(below, key=lambda r: r["perMin"] or 0)[:examples]:
            lines.append(f"  below: {r['title']}{' [anime]' if r['anime'] else ''} ({r['codec']} {r['perMin']} MB/min < {r['min']}, {r['quality']}) {r['file']}")
    return lines


def client(config):
    xml = pathlib.Path(config).read_text()
    get = lambda k: (re.search(f"<{k}>([^<]*)</{k}>", xml) or [None, ""])[1]
    base = f"http://localhost:{get('Port')}{get('UrlBase')}/api/v3"

    def call(path):
        req = urllib.request.Request(base + path, headers={"X-Api-Key": get("ApiKey")})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    return call


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-n", type=int, default=15, help="examples per app (default 15)")
    args = ap.parse_args()
    radarr = client(os.environ.get("CSR_RADARR_CONFIG", "/srv/appdata/radarr/config.xml"))
    sonarr = client(os.environ.get("CSR_SONARR_CONFIG", "/srv/appdata/sonarr/config.xml"))
    items = movie_items(radarr("/movie"), radarr("/qualityprofile"))
    series = sonarr("/series")
    items += episode_items(series, {s["id"]: sonarr(f"/episodefile?seriesId={s['id']}") for s in series},
                           {s["id"]: sonarr(f"/episode?seriesId={s['id']}") for s in series})
    print("\n".join(summary(check(items), args.n)))
    print("next: README \"Codec minimum size\"; existing files are never touched, the rule acts at grab time", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
