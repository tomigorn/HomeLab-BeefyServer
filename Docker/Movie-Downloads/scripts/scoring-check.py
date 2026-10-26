#!/usr/bin/env python3
"""Live check of the release scoring order (README "Release scoring") in Radarr and Sonarr: real release names through GET /parse (never searches, never grabs), scored with every profile's live scores.
Usage: scripts/scoring-check.py [radarr|sonarr]   (exit 1 on any FAIL; prints the score of every name per profile)"""
import json
import os
import pathlib
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

APPS = {
    "radarr": {"config": os.environ.get("SC_RADARR_CONFIG", "/srv/appdata/radarr/config.xml"), "titles": "/movie", "name": "{t}.{y}.{l}.{v}-GRP",
               "profiles": {"4k": "4K HDR - UHD Bluray + WEB (1080p fallback)", "any": "Any quality - old & rare (upgrades to 4K)", "hd": "HD Bluray + WEB"}},
    "sonarr": {"config": os.environ.get("SC_SONARR_CONFIG", "/srv/appdata/sonarr/config.xml"), "titles": "/series", "name": "{t}.S01E01.{l}.{v}-GRP",
               "profiles": {"4k": "4K HDR - WEB-2160p (1080p fallback)", "any": "Any quality - old & rare (upgrades to 4K)", "hd": "WEB-1080p"}},
}
TWIN = " - English original"
PREFERRED = ["Italian", "Spanish", "French", "Japanese", "Portuguese", "Danish", "Korean"]
V2160 = {"radarr": "2160p.UHD.BluRay.{a}.HDR.x265", "sonarr": "2160p.NF.WEB-DL.{a}.HDR.H.265"}
V2160_SDR = {"radarr": "2160p.UHD.BluRay.{a}.x265", "sonarr": "2160p.NF.WEB-DL.{a}.H.265"}
V1080 = {"radarr": "1080p.BluRay.{a}.x264", "sonarr": "1080p.NF.WEB-DL.{a}.H.264"}
V1080_HDR = {"radarr": "1080p.BluRay.{a}.HDR.x265", "sonarr": "1080p.NF.WEB-DL.{a}.HDR.H.265"}
TIER01 = "FLUX"
HD_TIER01 = "decibeL"
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


def dotted(title):
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", ".", plain).strip(".")


def check_app(app, cfg):
    print(f"== {app}")
    api = client(cfg["config"])
    profiles = {p["name"]: p for p in api("/qualityprofile")}
    titles = api(cfg["titles"])
    lang = lambda t: (t.get("originalLanguage") or {}).get("name")
    foreign = sorted((t for t in titles if lang(t) in PREFERRED), key=lambda t: PREFERRED.index(lang(t)))
    english = [t for t in titles if lang(t) == "English"]
    if not foreign:
        print("  no title with a common non-English original language in the library: skipped")
        return

    def names(t, langs, video, audio, group="GRP", pack=False):
        name = cfg["name"].format(t=dotted(t["title"]), y=t["year"], l=".".join(x.upper() for x in langs), v=video[app].format(a=audio))
        name = name[:-len("GRP")] + group
        return name.replace(".S01E01.", ".S01.") if pack else name

    def scorer(t):
        cache = {}

        def score(profile, langs, video, audio="DDP5.1", group="GRP", pack=False):
            name = names(t, langs, video, audio, group, pack)
            if name not in cache:
                parsed = api("/parse", title=name)
                cache[name] = ([c["name"] for c in parsed.get("customFormats", [])], [l["name"] for l in parsed.get("languages", [])])
            s = {f["name"]: f["score"] for f in profiles[profile]["formatItems"]}
            return sum(s.get(c, 0) for c in cache[name][0])
        return score, cache

    t = foreign[0]
    o = lang(t)
    others = [x for x in ("Spanish", "Portuguese", "French", "Italian") if x != o]
    score, cache = scorer(t)
    print(f"{t['title']} ({t['year']}, original {o})")
    for kind, prof in cfg["profiles"].items():
        p = profiles[prof]
        print(f" {prof} (min {p['minFormatScore']}, cutoff {p['cutoffFormatScore']}, min upgrade step {p['minUpgradeFormatScore']})")
        # The 1080p-only profiles reject every x265 encode (Golden Rule HD), so their HDR case is an x264 name.
        hdr1080 = {a: x.replace("x265", "x264").replace("H.265", "H.264") for a, x in V1080_HDR.items()} if kind == "hd" else V1080_HDR
        check("(a) 1080p HDR original+English beats 1080p SDR original+English+German", score(prof, [o, "English"], hdr1080) > score(prof, [o, "English", "German"], V1080))
        if kind != "hd":
            check("(a) 2160p HDR original+English beats 2160p SDR original+English+German", score(prof, [o, "English"], V2160) > score(prof, [o, "English", "German"], V2160_SDR))
            check("(h) 2160p SDR original+English accepted", score(prof, [o, "English"], V2160_SDR) >= p["minFormatScore"])
            check(f"(i) 2160p HDR original only beats 2160p SDR original+English+German+{others[0]}+{others[1]} TrueHD Atmos",
                  score(prof, [o], V2160) > score(prof, [o, "English", "German"] + others[:2], V2160_SDR, "TrueHD.Atmos.7.1"))
            check("(j) 2160p SDR original+English+German+extras stays below the cutoff", score(prof, [o, "English", "German"] + others[:2], V2160_SDR, "TrueHD.Atmos.7.1") < p["cutoffFormatScore"])
        v = V2160 if kind != "hd" else V1080
        check(f"(b) original+English+German+{others[1]}+{others[0]} beats original+English+German", score(prof, [o, "English", "German", others[1], others[0]], v) > score(prof, [o, "English", "German"], v))
        check(f"(c) original+German+{others[0]} beats original+German", score(prof, [o, "German", others[0]], v) > score(prof, [o, "German"], v))
        check(f"(d) original+English+{'+'.join(others[:3])} loses to original+English+German", score(prof, [o, "English"] + others[:3], v) < score(prof, [o, "English", "German"], v))
        if app == "radarr" and kind == "hd":
            check(f"(e) 3 languages from HD Bluray Tier 01 ({HD_TIER01}) beats 5 languages from no-name group (audio formats unscored here)", score(prof, [o, "English", "German"], v, group=HD_TIER01) > score(prof, [o, "English", "German", others[0], others[1]], v))
        elif app == "radarr":
            check("(e) 3 languages TrueHD Atmos beats 5 languages DD+", score(prof, [o, "English", "German"], v, "TrueHD.Atmos.7.1") > score(prof, [o, "English", "German", others[0], others[1]], v, "DDP5.1"))
        else:
            check(f"(e) original+English from WEB Tier 01 ({TIER01}) beats original+English+{others[0]} from no-name group", score(prof, [o, "English"], v, group=TIER01) > score(prof, [o, "English", others[0]], v))
        check("(f) dub only (German) rejected", score(prof, ["German"], v) < p["minFormatScore"])
        if kind != "hd":
            check("(f) x265 1080p without HDR rejected", score(prof, [o, "English", "German"] + others[:2], {app: V1080[app].replace("x264", "x265").replace("H.264", "H.265")}) < p["minFormatScore"])
        check(f"{'(j) ' if kind != 'hd' else ''}upgrade cutoff reached by {'2160p HDR ' if kind != 'hd' else ''}original+English", score(prof, [o, "English"], v) >= p["cutoffFormatScore"])
        check("upgrade cutoff not reached by original+German+extras", score(prof, [o, "German"] + others[:3], v, "TrueHD.Atmos.7.1") < p["cutoffFormatScore"])
        gain = score(prof, [o] + others[:3], v, "TrueHD.Atmos.7.1") - score(prof, [o], v, "AAC")
        check("extras and audio alone upgrade below the cutoff", gain >= p["minUpgradeFormatScore"] == 1, gain)
    if app == "sonarr":
        prof = cfg["profiles"]["4k"]
        pack = names(t, [o, "English"], V2160, "DDP5.1", pack=True)
        score(prof, [o, "English"], V2160, pack=True)
        check("(k) season pack is parsed as `Season Pack`", "Season Pack" in cache[pack][0], cache[pack][0])
        check("(k) season pack beats the same release as a single episode", score(prof, [o, "English"], V2160, pack=True) > score(prof, [o, "English"], V2160))
        check(f"(k) season pack from no-name group beats single episode from WEB Tier 01 ({TIER01})", score(prof, [o, "English"], V2160, pack=True) > score(prof, [o, "English"], V2160, group=TIER01))
        check("(k) single episode original+English+German beats season pack original+English", score(prof, [o, "English", "German"], V2160) > score(prof, [o, "English"], V2160, pack=True))
        check("(k) 2160p HDR single episode beats 2160p SDR season pack", score(prof, [o, "English"], V2160) > score(prof, [o, "English"], V2160_SDR, pack=True))
    # BlackBit (Bad Dual Groups) without a dash before the group escaped TRaSH's group match (1337x, 2026-10-26): own `Bad Group: BlackBit` rejects it everywhere.
    dash = names(t, [o, "English", "German"], V2160, "DDP5.1", group="BlackBit")
    for name in (dash, dash[:-len("-BlackBit")] + " BlackBit.mkv"):
        cfs = [c["name"] for c in api("/parse", title=name).get("customFormats", [])]
        check(f"(l) `{name}` matches `Bad Group: BlackBit`", "Bad Group: BlackBit" in cfs, cfs)
        for prof, p in profiles.items():
            s = {f["name"]: f["score"] for f in p["formatItems"]}
            check(f"(l) BlackBit rejected in {prof}", sum(s.get(c, 0) for c in cfs) < p["minFormatScore"], sum(s.get(c, 0) for c in cfs))
    single = names(t, [o], V1080, "DDP5.1")
    score(cfg["profiles"]["4k"], [o], V1080)
    check("original-only release gets no extra-language bonus", not any(c.startswith("Extra audio: ") for c in cache[single][0]), cache[single][0])
    if english:
        e = english[0]
        escore, ecache = scorer(e)
        twin = cfg["profiles"]["4k"] + TWIN
        print(f"{e['title']} ({e['year']}, original English) on {twin}")
        check("twin: 1080p HDR English beats 1080p SDR English+German", escore(twin, ["English"], V1080_HDR) > escore(twin, ["English", "German"], V1080))
        check("twin: English+German+2 extras beats English+German", escore(twin, ["English", "German", "Spanish", "French"], V2160) > escore(twin, ["English", "German"], V2160))
        check("twin: English+4 extras loses to English+German", escore(twin, ["English", "Spanish", "French", "Italian", "Portuguese"], V2160) < escore(twin, ["English", "German"], V2160))
        check("twin: (h) 2160p SDR English accepted", escore(twin, ["English"], V2160_SDR) >= profiles[twin]["minFormatScore"])
        check("twin: (i) 2160p HDR English beats 2160p SDR English+German+2 extras", escore(twin, ["English"], V2160) > escore(twin, ["English", "German", "Spanish", "French"], V2160_SDR))
        check("twin: (j) 2160p SDR English+German+2 extras stays below the cutoff", escore(twin, ["English", "German", "Spanish", "French"], V2160_SDR) < profiles[twin]["cutoffFormatScore"])
        check("twin: (j) upgrade cutoff reached by 2160p HDR English+German", escore(twin, ["English", "German"], V2160) >= profiles[twin]["cutoffFormatScore"])
        cache.update(ecache)
    print(" release name | languages parsed | custom formats | 4K profile score")
    for name, (cfs, langs) in cache.items():
        if name.startswith(dotted(t["title"])):
            s = {f["name"]: f["score"] for f in profiles[cfg["profiles"]["4k"]]["formatItems"]}
            print(f"  {name} | {', '.join(langs)} | {', '.join(c for c in cfs if not c.startswith('Resolution'))} | {sum(s.get(c, 0) for c in cfs)}")


def main():
    for app in sys.argv[1:] or list(APPS):
        check_app(app, APPS[app])
    print(f"{'FAILED' if fails else 'all checks passed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
