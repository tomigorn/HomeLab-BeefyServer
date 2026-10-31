#!/usr/bin/env python3
"""Live check of the release scoring order (README "Release scoring") in Radarr and Sonarr: real release names through GET /parse (never searches, never grabs), scored with every profile's live scores.
Radarr also checks the editions (m): a longer cut beats audio, group and extras, never a language rung or HDR, and is what reaches the cutoff.
Real quality only (n, owner 2026-10-31): lossless audio formats equal, Atmos/DTS:X a small plus, lossy ranked, repack and tier group higher, no score for size, more languages or a season pack, a remux never above a 2160p encode, minimum upgrade step 1.
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
    "radarr": {"config": os.environ.get("SC_RADARR_CONFIG", "/srv/appdata/radarr/config.xml"), "titles": "/movie", "name": "{t}.{y}{e}.{l}.{v}-GRP",
               "profiles": {"4k": "4K HDR - UHD Bluray + WEB (1080p fallback)", "any": "4K preferred · older versions allowed", "hd": "HD Bluray + WEB"}},
    "sonarr": {"config": os.environ.get("SC_SONARR_CONFIG", "/srv/appdata/sonarr/config.xml"), "titles": "/series", "name": "{t}.S01E01.{l}.{v}-GRP",
               "profiles": {"4k": "4K HDR - WEB-2160p (1080p fallback)", "any": "4K preferred · older versions allowed", "hd": "WEB-1080p"}},
}
TWIN = " - English original"
PREFERRED = ["Italian", "Spanish", "French", "Japanese", "Portuguese", "Danish", "Korean"]
V2160 = {"radarr": "2160p.UHD.BluRay.{a}.HDR.x265", "sonarr": "2160p.NF.WEB-DL.{a}.HDR.H.265"}
V2160_SDR = {"radarr": "2160p.UHD.BluRay.{a}.x265", "sonarr": "2160p.NF.WEB-DL.{a}.H.265"}
V1080 = {"radarr": "1080p.BluRay.{a}.x264", "sonarr": "1080p.NF.WEB-DL.{a}.H.264"}
V1080_HDR = {"radarr": "1080p.BluRay.{a}.HDR.x265", "sonarr": "1080p.NF.WEB-DL.{a}.HDR.H.265"}
TIER01 = "FLUX"
HD_TIER01 = "decibeL"
UHD_TIER01 = "CtrlHD"
REMUX_CF = "Remux: smaller encode wins"
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

    def names(t, langs, video, audio, group="GRP", pack=False, ed=""):
        name = cfg["name"].format(t=dotted(t["title"]), y=t["year"], e=f".{ed}" if ed else "", l=".".join(x.upper() for x in langs), v=video[app].format(a=audio))
        name = name[:-len("GRP")] + group
        return name.replace(".S01E01.", ".S01.") if pack else name

    def scorer(t):
        cache = {}

        def score(profile, langs, video, audio="DDP5.1", group="GRP", pack=False, ed=""):
            name = names(t, langs, video, audio, group, pack, ed)
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
        check(f"(b) original+English+German+{others[1]}+{others[0]} equals original+English+German (more languages score nothing)", score(prof, [o, "English", "German", others[1], others[0]], v) == score(prof, [o, "English", "German"], v))
        check(f"(c) original+German+{others[0]} equals original+German", score(prof, [o, "German", others[0]], v) == score(prof, [o, "German"], v))
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
        ed = "Extended" if app == "radarr" else ""
        check(f"{'(j) ' if kind != 'hd' else ''}upgrade cutoff reached by {'2160p HDR ' if kind != 'hd' else ''}original+English{' Extended' if ed else ''}", score(prof, [o, "English"], v, ed=ed) >= p["cutoffFormatScore"])
        check("upgrade cutoff not reached by original+German+extras", score(prof, [o, "German"] + others[:3], v, "TrueHD.Atmos.7.1", ed=ed) < p["cutoffFormatScore"])
        check("(n) minimum upgrade step 1", p["minUpgradeFormatScore"] == 1, p["minUpgradeFormatScore"])
        if kind != "hd":
            a = lambda audio: score(prof, [o, "English"], V2160, audio)
            check("(n) TrueHD equals DTS-HD MA", a("TrueHD.7.1") == a("DTS-HD.MA.7.1"), (a("TrueHD.7.1"), a("DTS-HD.MA.7.1")))
            check("(n) FLAC and LPCM equal TrueHD", a("FLAC.2.0") == a("LPCM.2.0") == a("TrueHD.7.1"), (a("FLAC.2.0"), a("LPCM.2.0")))
            check("(n) TrueHD Atmos beats DTS-HD MA", a("TrueHD.Atmos.7.1") > a("DTS-HD.MA.7.1"))
            check("(n) TrueHD Atmos equals DTS:X", a("TrueHD.Atmos.7.1") == a("DTS-X.7.1"))
            check("(n) EAC3 beats AAC", a("EAC3.5.1") > a("AAC2.0") and a("DDP5.1") > a("AAC2.0"))
            check("(n) TrueHD beats DD+ Atmos beats DD+", a("TrueHD.7.1") > a("DDP5.1.Atmos") > a("DDP5.1"))
            check("(n) DD+ beats DD, DTS core equals DD, DD beats AAC", a("DDP5.1") > a("DD5.1") == a("DTS.5.1") > a("AAC2.0"))
            check("(n) better audio upgrades below the cutoff (extras add nothing)", score(prof, [o] + others[:3], v, "TrueHD.Atmos.7.1") - score(prof, [o], v, "AAC") >= 1)
        check("(n) REPACK beats the same release", score(prof, [o, "English"], v, "DDP5.1.REPACK") > score(prof, [o, "English"], v))
        grp = TIER01 if app == "sonarr" else (HD_TIER01 if kind == "hd" else UHD_TIER01)
        check(f"(n) tier group ({grp}) beats a no-name group", score(prof, [o, "English"], v, group=grp) > score(prof, [o, "English"], v))
        if app == "radarr":
            check("(m) theatrical original+English+extras TrueHD Atmos stays below the cutoff", score(prof, [o, "English"] + others[:2], v, "TrueHD.Atmos.7.1") < p["cutoffFormatScore"])
            for cut in ("Extended", "Directors.Cut", "Uncut", "Ultimate.Edition"):
                cfs = api("/parse", title=names(t, [o, "English"], v, "DDP5.1", ed=cut)).get("customFormats", [])
                check(f"(m) `{cut}` matches `Edition: longer cut`", "Edition: longer cut" in [c["name"] for c in cfs], [c["name"] for c in cfs])
            cfs = [c["name"] for c in api("/parse", title=names(t, [o, "English"], v, "DDP5.1", ed="Theatrical")).get("customFormats", [])]
            check("(m) `Theatrical` is `Theatrical Cut`, not a longer cut", "Theatrical Cut" in cfs and "Edition: longer cut" not in cfs, cfs)
            for same in ("25th.Anniversary.Edition", "Remastered.Edition", "TV.Version"):
                cfs = [c["name"] for c in api("/parse", title=names(t, [o, "English"], v, "DDP5.1", ed=same)).get("customFormats", [])]
                check(f"(m) `{same}` is not a longer cut", "Edition: longer cut" not in cfs, cfs)
            step = score(prof, [o, "English"], v, ed=ed) - score(prof, [o, "English"] + others[:2], v, "TrueHD.Atmos.7.1")
            check("(m) Extended original+English beats theatrical original+English+extras TrueHD Atmos by the upgrade step", step >= p["minUpgradeFormatScore"], step)
            check("(m) Extended original+German+extras loses to theatrical original+English", score(prof, [o, "German"] + others[:2], v, "TrueHD.Atmos.7.1", ed=ed) < score(prof, [o, "English"], v))
            if kind != "hd":
                check("(m) Extended 2160p SDR original+English+German loses to theatrical 2160p HDR original", score(prof, [o, "English", "German"], V2160_SDR, "TrueHD.Atmos.7.1", ed=ed) < score(prof, [o], V2160))
    if app == "sonarr":
        prof = cfg["profiles"]["4k"]
        pack = names(t, [o, "English"], V2160, "DDP5.1", pack=True)
        score(prof, [o, "English"], V2160, pack=True)
        check("(k) season pack is parsed as `Season Pack`", "Season Pack" in cache[pack][0], cache[pack][0])
        check("(k) season pack equals the same release as a single episode (no swap)", score(prof, [o, "English"], V2160, pack=True) == score(prof, [o, "English"], V2160))
        check(f"(k) single episode from WEB Tier 01 ({TIER01}) beats a no-name season pack", score(prof, [o, "English"], V2160, group=TIER01) > score(prof, [o, "English"], V2160, pack=True))
        check("(k) single episode original+English+German beats season pack original+English", score(prof, [o, "English", "German"], V2160) > score(prof, [o, "English"], V2160, pack=True))
        check("(k) 2160p HDR single episode beats 2160p SDR season pack", score(prof, [o, "English"], V2160) > score(prof, [o, "English"], V2160_SDR, pack=True))
    # Groups TRaSH's group match misses without a dash (1337x): own `Bad Group: <name>` CFs reject every tag shape everywhere.
    for grp in ("BlackBit", "Vyndros"):
        dash = names(t, [o, "English", "German"], V2160, "DDP5.1", group=grp)
        stem = dash[:-len("-" + grp)]
        for name in (dash, stem + " " + grp + ".mkv", stem + "." + grp, stem + "_" + grp, stem + " [" + grp + "]", stem + " (" + grp.upper() + ")"):
            cfs = [c["name"] for c in api("/parse", title=name).get("customFormats", [])]
            check(f"(l) `{name}` matches `Bad Group: {grp}`", f"Bad Group: {grp}" in cfs, cfs)
            for prof, p in profiles.items():
                s = {f["name"]: f["score"] for f in p["formatItems"]}
                check(f"(l) {grp} rejected in {prof}", sum(s.get(c, 0) for c in cfs) < p["minFormatScore"], sum(s.get(c, 0) for c in cfs))
        for name in (stem + "-" + grp + "er", stem + "-X" + grp):
            cfs = [c["name"] for c in api("/parse", title=name).get("customFormats", [])]
            check(f"(l) `{name}` does not match `Bad Group: {grp}`", f"Bad Group: {grp}" not in cfs, cfs)
    single = names(t, [o], V1080, "DDP5.1")
    score(cfg["profiles"]["4k"], [o], V1080)
    check("original-only release gets no extra-language bonus", not any(c.startswith("Extra audio: ") for c in cache[single][0]), cache[single][0])
    if english:
        e = english[0]
        escore, ecache = scorer(e)
        twin = cfg["profiles"]["4k"] + TWIN
        print(f"{e['title']} ({e['year']}, original English) on {twin}")
        check("twin: 1080p HDR English beats 1080p SDR English+German", escore(twin, ["English"], V1080_HDR) > escore(twin, ["English", "German"], V1080))
        check("twin: English+German+2 extras equals English+German", escore(twin, ["English", "German", "Spanish", "French"], V2160) == escore(twin, ["English", "German"], V2160))
        check("twin: English+4 extras loses to English+German", escore(twin, ["English", "Spanish", "French", "Italian", "Portuguese"], V2160) < escore(twin, ["English", "German"], V2160))
        check("twin: (h) 2160p SDR English accepted", escore(twin, ["English"], V2160_SDR) >= profiles[twin]["minFormatScore"])
        check("twin: (i) 2160p HDR English beats 2160p SDR English+German+2 extras", escore(twin, ["English"], V2160) > escore(twin, ["English", "German", "Spanish", "French"], V2160_SDR))
        check("twin: (j) 2160p SDR English+German+2 extras stays below the cutoff", escore(twin, ["English", "German", "Spanish", "French"], V2160_SDR) < profiles[twin]["cutoffFormatScore"])
        eed = "Extended" if app == "radarr" else ""
        check(f"twin: (j) upgrade cutoff reached by 2160p HDR English{' Extended' if eed else ''}", escore(twin, ["English"], V2160, ed=eed) >= profiles[twin]["cutoffFormatScore"])
        if eed:
            check("twin: (m) theatrical 2160p HDR English stays below the cutoff", escore(twin, ["English"], V2160, "TrueHD.Atmos.7.1") < profiles[twin]["cutoffFormatScore"])
            check("twin: (m) Extended English loses to theatrical English+German", escore(twin, ["English"], V2160, ed=eed) < escore(twin, ["English", "German"], V2160))
        check("twin: German DL still outscores English-only at grab time", escore(twin, ["English", "German"], V2160) > escore(twin, ["English"], V2160))
        cache.update(ecache)
    scored = {f["format"] for q in profiles.values() for f in q["formatItems"] if f["score"]}
    sized = [c["name"] for c in api("/customformat") if c["id"] in scored and any("Size" in x["implementation"] for x in c["specifications"])]
    check("(n) no scored custom format has a size condition (a bigger file of the same quality scores the same)", not sized, sized)
    if app == "radarr":
        remux_check(api, profiles, t, names)
    print(" release name | languages parsed | custom formats | 4K profile score")
    for name, (cfs, langs) in cache.items():
        if name.startswith(dotted(t["title"])):
            s = {f["name"]: f["score"] for f in profiles[cfg["profiles"]["4k"]]["formatItems"]}
            print(f"  {name} | {', '.join(langs)} | {', '.join(c for c in cfs if not c.startswith('Resolution'))} | {sum(s.get(c, 0) for c in cfs)}")


def rank(p, quality):
    for i, item in enumerate(reversed([x for x in p["items"] if x["allowed"]])):
        if quality in ([q["quality"]["name"] for q in item["items"]] if item.get("items") else [item["quality"]["name"]]):
            return i
    return None


def remux_check(api, profiles, t, names):
    o = (t.get("originalLanguage") or {}).get("name")
    remux = names(t, [o, "English"], {"radarr": "2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.{a}"}, "TrueHD.Atmos.7.1", group="FraMeSToR")
    imax = names(t, [o, "English"], {"radarr": "IMAX.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.{a}"}, "TrueHD.Atmos.7.1", group="GRP")
    enc = names(t, [o, "English"], {"radarr": "2160p.UHD.BluRay.{a}.DV.HDR.x265"}, "DDP5.1", group=UHD_TIER01)
    web = names(t, [o, "English"], {"radarr": "2160p.AMZN.WEB-DL.{a}.DV.HDR.H.265"}, "DDP5.1.Atmos", group="FLUX")
    plain = names(t, [o, "English"], {"radarr": "2160p.WEB-DL.{a}.DV.HDR.H.265"}, "AAC2.0", group="GRP")
    sdr = names(t, [o], {"radarr": "2160p.UHD.BluRay.REMUX.HEVC{a}"}, "", group="GRP")
    parsed = {n: api("/parse", title=n) for n in (remux, imax, enc, web, plain, sdr)}
    q = {n: parsed[n]["parsedMovieInfo"]["quality"]["quality"]["name"] for n in parsed}
    cfs = {n: [c["name"] for c in parsed[n].get("customFormats", [])] for n in parsed}
    check("(n) remux parsed as Remux-2160p with `Remux: smaller encode wins`", q[remux] == "Remux-2160p" and REMUX_CF in cfs[remux], (q[remux], cfs[remux]))
    for prof in (APPS["radarr"]["profiles"]["4k"], APPS["radarr"]["profiles"]["any"], APPS["radarr"]["profiles"]["4k"] + TWIN, APPS["radarr"]["profiles"]["any"] + TWIN):
        p = profiles[prof]
        s = {f["name"]: f["score"] for f in p["formatItems"]}
        sc = {n: sum(s.get(c, 0) for c in cfs[n]) for n in parsed}
        r = {n: rank(p, q[n]) for n in parsed}
        check(f"(n) {prof}: remux allowed, below Bluray-2160p, same group as WEB 2160p", None not in r.values() and r[enc] < r[remux] == r[web], r)
        check(f"(n) {prof}: DV tier-1 encode ranks at or above a DV remux", None not in r.values() and (r[enc], -sc[enc]) <= (r[remux], -sc[remux]), (r, sc))
        for have in (web, plain):
            for cand in (remux, imax):
                check(f"(n) {prof}: `{have}` in place, `{cand}` appears: no upgrade", sc[cand] - sc[have] < p["minUpgradeFormatScore"], sc)
        check(f"(n) {prof}: SDR remux, original only, no audio named: still grabbable", sc[sdr] >= p["minFormatScore"], (sc[sdr], p["minFormatScore"]))


def main():
    for app in sys.argv[1:] or list(APPS):
        check_app(app, APPS[app])
    print(f"{'FAILED' if fails else 'all checks passed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
