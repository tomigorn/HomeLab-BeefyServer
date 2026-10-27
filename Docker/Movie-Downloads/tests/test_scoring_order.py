#!/usr/bin/env python3
"""Release ranking order (README "Release scoring") from recyclarr/recyclarr.yml + scripts/language_formats.py, both apps, every profile: HDR > language tier > Sonarr season pack > audio/group/streaming > extra languages (tie-breaker); 2160p SDR allowed; rejects stay rejected; upgrades stop at 4K HDR + original + English; Swiss German unchanged.
Custom formats are matched by a model: the native language formats are evaluated from their real definitions (Radarr's LanguageSpecification), TRaSH ones by their documented conditions.
Usage: python3 tests/test_scoring_order.py   (needs python3-yaml)"""
import json
import pathlib
import re
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import language_formats as lf  # noqa: E402

YML = ROOT / "recyclarr" / "recyclarr.yml"
SWISS = lf.SWISS
TWIN = lf.ENGLISH_ORIGINAL_SUFFIX
PROFILES = {
    "radarr": {"4k": "4K HDR - UHD Bluray + WEB (1080p fallback)", "any": "4K preferred · older versions allowed",
               "hd": "HD Bluray + WEB", "anime": "Anime - Remux + WEB 1080p"},
    "sonarr": {"4k": "4K HDR - WEB-2160p (1080p fallback)", "any": "4K preferred · older versions allowed",
               "hd": "WEB-1080p", "anime": "Anime - Bluray + WEB 1080p"},
}
# TRaSH default scores Recyclarr syncs and recyclarr.yml does not override (as synced 2026-10-26): audio, group tiers, streaming, repacks.
MINOR = {
    "radarr": {"audio": {"TrueHD ATMOS": 5000, "DTS X": 4500, "DD+": 1750, "DD": 750, None: 0},
               "group": {"UHD Bluray Tier 01": 1800, "HD Bluray Tier 01": 1800, "WEB Tier 01": 1700, "WEB Tier 03": 1600, None: 0},
               "small": {"MA": 20, "IMF": 20, "CRiT": 20, "BCORE": 15, "Repack3": 7}},
    "sonarr": {"audio": {None: 0},
               "group": {"WEB Tier 01": 1700, "WEB Scene": 1600, None: 0},
               "small": {"NF": 75, "UHD Streaming Boost": 75, "HD Streaming Boost": 75, "Repack3": 7}},
}
ANIME_MINOR = {"group": {"Anime BD Tier 01": 1400, "Remux Tier 01": 975, None: 0}, "small": {"VRV": 10, "CR": 6, "v4": 4, "Repack3": 7}}
MILD_NEGATIVES = {"v0"}  # TRaSH ranking penalty (-51), not a reject
SEASON_PACK = "Season Pack"
SEASON_PACK_SCORE = 2000


def load_yaml():
    class Loader(yaml.SafeLoader):
        pass
    Loader.add_constructor("!secret", lambda loader, node: "secret")
    return yaml.load(YML.read_text(), Loader=Loader)


def cf_names(app):
    names = {m.group(1): m.group(2).strip() for m in re.finditer(r"^[ \t]*-[ \t]*(?:trash_id:[ \t]*)?([0-9a-f]{32})[ \t]*#[ \t]*(.+)$", YML.read_text(), re.M)}
    for f in (ROOT / "recyclarr" / "custom-formats" / app).glob("*.json"):
        d = json.loads(f.read_text())
        names[d["trash_id"]] = d["name"]
    return names


def languages(app):
    return json.loads((ROOT / "tests" / "fixtures" / f"{app}-languages.json").read_text())


CFG = load_yaml()


def profile(app, name):
    inst = CFG[app]["movies" if app == "radarr" else "series"]
    names = cf_names(app)
    p = next(q for q in inst["quality_profiles"] if q.get("name") == name or (name in ("HD Bluray + WEB", "WEB-1080p") and "name" not in q and names.get(q["trash_id"]) == name))
    scores = {}
    for block in inst["custom_formats"]:
        for target in block["assign_scores_to"]:
            if target.get("name") == name and "score" in target:
                for tid in block["trash_ids"]:
                    scores[names[tid]] = target["score"]
    for f in lf.formats(languages(app)):
        scores[f["name"]] = lf.score(name, f["name"])
    return {"scores": scores, "min": p.get("min_format_score", 100 if "Anime" in name else 0), "cutoff": p["upgrade"]["until_score"],
            "min_upgrade": p.get("min_upgrade_format_score", 1), "except": p["reset_unmatched_scores"]}


def lang_spec_ok(spec, langs, original):
    v = {f["name"]: f["value"] for f in spec["fields"]}
    compared = original if v["value"] == lf.ORIGINAL else v["value"]
    hit = any(l != compared for l in langs) if v["exceptLanguage"] else compared in langs
    return hit != spec["negate"]


def matched(app, original, langs, res=1080, hdr=False, audio=None, group=None, small=(), extra=()):
    """Custom formats a release matches; langs are language names as the parser reports them (none = the original)."""
    ids = {l["name"]: l["id"] for l in languages(app)}
    o = ids[original]
    L = {ids[l] for l in langs} or {o}
    out = set(extra) | set(small)
    for f in lf.formats(languages(app)):
        if all(lang_spec_ok(s, L, o) for s in f["specifications"]):
            out.add(f["name"])
    if ids["German"] in L and o in L and o != ids["German"]:
        out.add("German DL")
    if o not in L:
        out.add("Language: Not Original")
        if ids["German"] in L:
            out.add("German")
    if hdr:
        out.add("HDR")
    if res == 2160 and not hdr:
        out.add("SDR")
    out |= {x for x in (audio, group) if x}
    return out


def score(p, cfs):
    return sum(p["scores"].get(c, 0) for c in cfs)


def every_profile():
    for app, kinds in PROFILES.items():
        for kind, base in kinds.items():
            for name in (base, base + TWIN) if kind in ("4k", "any") else (base,):
                yield app, kind, name


class Base(unittest.TestCase):
    def setUp(self):
        self.cases = []
        for app, kind, name in every_profile():
            twin = name.endswith(TWIN)
            orig = "English" if twin else ("Italian" if app == "radarr" else "Spanish")
            # The TRaSH HD and anime templates score no audio formats.
            minor = ANIME_MINOR | {"audio": {None: 0}} if kind == "anime" else MINOR[app] | ({"audio": {None: 0}} if kind == "hd" else {})
            resolutions = (1080, 2160) if kind in ("4k", "any") else (1080,)
            prof = profile(app, name)
            prof["scores"] = {k: v for group in minor.values() for k, v in group.items() if k} | prof["scores"]
            self.cases.append((app, kind, name, twin, orig, minor, resolutions, prof))

    def tiers(self, twin, orig):
        """Language tiers, best first, without extras."""
        return [[orig, "German"], [orig]] if twin else [[orig, "English", "German"], [orig, "English"], [orig, "German"], [orig]]

    def extras(self, app, orig):
        return [l["name"] for l in languages(app) if l["name"] not in lf.NOT_EXTRA and l["name"] != orig]

    def best(self, minor):
        return {"audio": max(minor["audio"], key=lambda k: minor["audio"][k]), "group": max(minor["group"], key=lambda k: minor["group"][k]), "small": tuple(minor["small"]), "extra": (SEASON_PACK,)}

    def best_single(self, minor):
        return self.best(minor) | {"extra": ()}


class Order(Base):

    def test_a_hdr_beats_any_sdr(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            for res in resolutions:
                with self.subTest(app=app, profile=name, res=res):
                    hdr = score(p, matched(app, orig, [orig] if twin else [orig, "English"], res, hdr=True))
                    sdr = score(p, matched(app, orig, [orig, "English", "German"], res, hdr=False))
                    self.assertGreater(hdr, sdr)
                    worst_hdr = score(p, matched(app, orig, [orig], res, hdr=True))
                    best_sdr = score(p, matched(app, orig, [orig, "English", "German"] + self.extras(app, orig), res, **self.best(minor)))
                    self.assertGreater(worst_hdr, best_sdr)

    def test_b_c_more_languages_win_within_a_tier(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            for tier in self.tiers(twin, orig):
                with self.subTest(app=app, profile=name, tier=tier):
                    base = score(p, matched(app, orig, tier))
                    two = score(p, matched(app, orig, tier + ["Portuguese", "Spanish" if orig != "Spanish" else "French"]))
                    one = score(p, matched(app, orig, tier + ["Portuguese"]))
                    self.assertGreater(two, one)
                    self.assertGreater(one, base)

    def test_d_tiers_first(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            tiers = self.tiers(twin, orig)
            for hi, lo in zip(tiers, tiers[1:]):
                with self.subTest(app=app, profile=name, higher=hi, lower=lo):
                    plain = score(p, matched(app, orig, hi))
                    stuffed = score(p, matched(app, orig, lo + self.extras(app, orig), **self.best(minor)))
                    self.assertGreater(plain, stuffed)
            if not twin:
                with self.subTest(app=app, profile=name, case="orig+EN+ES+FR+IT < orig+EN+DE"):
                    others = [l for l in ("Spanish", "French", "Italian", "Portuguese") if l != orig][:3]
                    self.assertLess(score(p, matched(app, orig, [orig, "English"] + others)), score(p, matched(app, orig, [orig, "English", "German"])))

    def test_e_audio_and_group_beat_extra_languages(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                if max(minor["audio"].values()) > 0:
                    five = score(p, matched(app, orig, [orig, "English", "German", "Portuguese", "French"], audio="DD+"))
                    three = score(p, matched(app, orig, [orig, "English", "German"], audio="TrueHD ATMOS"))
                else:
                    five = score(p, matched(app, orig, [orig, "English", "German", "Portuguese", "French"]))
                    three = score(p, matched(app, orig, [orig, "English", "German"], group=self.best(minor)["group"]))
                self.assertGreater(three, five)
                bonuses = [v for k in ("audio", "group") for v in minor[k].values() if v > 0]
                if kind != "anime":
                    bonuses += [v for k, v in minor["small"].items() if v > 0 and not k.startswith("Repack")]
                self.assertLess(lf.EXTRA_SCORE, min(bonuses))

    def test_e_streaming_web_tier_01_beats_an_extra_dub(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if app == "sonarr" and kind != "anime":
                with self.subTest(profile=name):
                    tier = [orig, "German"] if twin else [orig, "English"]
                    dub = "Italian" if orig != "Italian" else "French"
                    self.assertGreater(score(p, matched(app, orig, tier, 2160, hdr=True, group="WEB Tier 01", small=("UHD Streaming Boost",))),
                                       score(p, matched(app, orig, tier + [dub], 2160, hdr=True)))

    def test_extra_score(self):
        self.assertEqual(lf.EXTRA_SCORE, 10)

    def test_f_negatives_still_reject(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            richest = matched(app, orig, [orig, "English", "German"] + self.extras(app, orig), hdr=True, **self.best(minor))
            top = score(p, richest) + (p["scores"].get("Anime Dual Audio", 0))
            for cf, s in p["scores"].items():
                if s < 0 and cf not in MILD_NEGATIVES:
                    with self.subTest(app=app, profile=name, reject=cf):
                        self.assertLess(top + s, p["min"])
            with self.subTest(app=app, profile=name, reject="dub only / no original audio"):
                self.assertLess(score(p, matched(app, orig if orig != "German" else "French", ["German"])), p["min"])

    def test_f_bad_formats_stay_rejected_at_2160p(self):
        bad = {"radarr": ["DV (w/o HDR fallback)", "Upscaled", "BR-DISK", "LQ", "LQ (Release Title)", "Generated Dynamic HDR", "x265 (no HDR/DV)", "3D", "AV1"],
               "sonarr": ["DV (w/o HDR fallback)", "Upscaled", "BR-DISK", "LQ", "LQ (Release Title)", "x265 (no HDR/DV)", "AV1"]}
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if kind in ("4k", "any"):
                with self.subTest(app=app, profile=name):
                    self.assertEqual({c: p["scores"].get(c) for c in bad[app]}, {c: -50000000 for c in bad[app]})

    def test_h_2160p_sdr_accepted(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if 2160 in resolutions:
                with self.subTest(app=app, profile=name):
                    self.assertEqual(p["scores"].get("SDR", 0), 0)
                    top = [orig, "German"] if twin else [orig, "English"]
                    self.assertGreaterEqual(score(p, matched(app, orig, top, 2160, hdr=False)), p["min"])
                    self.assertGreaterEqual(score(p, matched(app, orig, [orig], 2160, hdr=False)), p["min"])

    def test_i_2160p_hdr_beats_2160p_sdr_with_more_languages(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if 2160 in resolutions:
                with self.subTest(app=app, profile=name):
                    hdr = score(p, matched(app, orig, [orig], 2160, hdr=True))
                    sdr = score(p, matched(app, orig, [orig, "English", "German"] + self.extras(app, orig), 2160, hdr=False, **self.best(minor)))
                    self.assertGreater(hdr, sdr)

    def test_no_hdr_offset_left(self):
        for app in PROFILES:
            with self.subTest(app=app):
                self.assertNotIn("2160p: HDR bonus offset", cf_names(app).values())
                self.assertNotIn("2160p: HDR bonus offset", YML.read_text())

    def test_scores_fit_int32(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                self.assertLess(sum(s for s in p["scores"].values() if s > 0), 2**31 - 1)
                self.assertGreater(sum(s for s in p["scores"].values() if s < 0), -2**31)


class Upgrades(Base):
    def test_cutoff_values(self):
        want = {"4k": (25000000, 20000000), "any": (25000000, 20000000), "hd": (5000000, None), "anime": (5000000, None)}
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                self.assertEqual(p["cutoff"], want[kind][twin])

    def test_j_4k_cutoff_at_hdr_plus_original_plus_english(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if 2160 not in resolutions:
                continue
            top_tier, below = ([orig], None) if twin else (self.tiers(twin, orig)[1], self.tiers(twin, orig)[2])
            with self.subTest(app=app, profile=name):
                self.assertGreaterEqual(score(p, matched(app, orig, top_tier, 2160, hdr=True)), p["cutoff"])
                if twin:
                    self.assertEqual(p["cutoff"], 20000000)
                    self.assertGreater(score(p, matched(app, orig, [orig, "German"], 2160, hdr=True)), score(p, matched(app, orig, [orig], 2160, hdr=True)))
                    self.assertGreaterEqual(score(p, matched(app, orig, [orig, "German"], 2160, hdr=True)), p["cutoff"])
                else:
                  self.assertLess(score(p, matched(app, orig, below + self.extras(app, orig), 2160, hdr=True, **self.best(minor))), p["cutoff"])
                richest_sdr = [orig, "English", "German"] + self.extras(app, orig)
                self.assertLess(score(p, matched(app, orig, richest_sdr, 2160, hdr=False, **self.best(minor))), p["cutoff"])

    def test_1080p_profiles_cutoff_at_original_plus_english(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if 2160 in resolutions:
                continue
            with self.subTest(app=app, profile=name):
                self.assertGreaterEqual(score(p, matched(app, orig, [orig, "English"])), p["cutoff"])
                self.assertLess(score(p, matched(app, orig, [orig, "German"] + self.extras(app, orig), **self.best(minor))), p["cutoff"])

    def test_extras_and_audio_upgrade_below_cutoff(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                self.assertEqual(p["min_upgrade"], 1)
                gain = score(p, matched(app, orig, [orig] + self.extras(app, orig), **self.best(minor))) - score(p, matched(app, orig, [orig]))
                self.assertGreaterEqual(gain, p["min_upgrade"])

    def test_extra_scores_survive_the_recyclarr_reset(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                self.assertTrue(any(re.match(pat, lf.EXTRA_PREFIX + "Spanish", re.I) for pat in p["except"].get("except_patterns") or []))
                if not twin:
                    self.assertIn(lf.ORIGINAL_ENGLISH, p["except"].get("except") or [])


class SeasonPack(Base):
    def test_season_pack_format_is_a_sonarr_release_type(self):
        d = json.loads((ROOT / "recyclarr" / "custom-formats" / "sonarr" / "season-pack.json").read_text())
        self.assertEqual(d["name"], SEASON_PACK)
        self.assertRegex(d["trash_id"], r"^[0-9a-f]{32}$")
        self.assertEqual([(s["implementation"], s["negate"], s["required"], s["fields"]["value"]) for s in d["specifications"]], [("ReleaseTypeSpecification", False, True, 3)])
        ids = [json.loads(f.read_text())["trash_id"] for a in ("radarr", "sonarr") for f in (ROOT / "recyclarr" / "custom-formats" / a).glob("*.json")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_season_pack_scores(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                want = SEASON_PACK_SCORE if app == "sonarr" and kind != "anime" else 0
                self.assertEqual(p["scores"].get(SEASON_PACK, 0), want)
        self.assertEqual(profile("sonarr", SWISS)["scores"].get(SEASON_PACK, 0), 0)

    def test_season_pack_beats_the_best_single_episode(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if app == "sonarr" and kind != "anime":
                for res in resolutions:
                    with self.subTest(profile=name, res=res):
                        tier = [orig, "German"] if twin else [orig, "English"]
                        pack = score(p, matched(app, orig, tier, res, hdr=True, extra=(SEASON_PACK,)))
                        single = score(p, matched(app, orig, tier + self.extras(app, orig)[:5], res, hdr=True, **self.best_single(minor)))
                        self.assertGreater(pack, single)

    def test_season_pack_never_beats_a_better_language_tier_or_hdr(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if app != "sonarr":
                continue
            tiers = self.tiers(twin, orig)
            for hi, lo in zip(tiers, tiers[1:]):
                with self.subTest(profile=name, higher=hi, lower=lo):
                    self.assertGreater(score(p, matched(app, orig, hi)), score(p, matched(app, orig, lo, **self.best(minor))))
            for res in resolutions:
                with self.subTest(profile=name, res=res, case="HDR single episode beats SDR season pack"):
                    self.assertGreater(score(p, matched(app, orig, [orig], res, hdr=True)), score(p, matched(app, orig, [orig, "English", "German"], res, **self.best(minor))))


class NativeFormats(unittest.TestCase):
    def test_original_is_not_counted_twice_on_a_single_language_release(self):
        for app, orig in (("radarr", "Spanish"), ("sonarr", "Japanese")):
            with self.subTest(app=app):
                self.assertFalse(any(c.startswith(lf.EXTRA_PREFIX) for c in matched(app, orig, [orig])))
                self.assertIn(lf.EXTRA_PREFIX + "French", matched(app, orig, [orig, "French"]))

    def test_english_and_german_are_never_extras(self):
        for app in PROFILES:
            names = {f["name"] for f in lf.formats(languages(app))}
            self.assertNotIn(lf.EXTRA_PREFIX + "English", names)
            self.assertNotIn(lf.EXTRA_PREFIX + "German", names)
            self.assertIn(lf.EXTRA_PREFIX + "Portuguese (Brazil)", names)

    def test_original_plus_english_definition_unchanged(self):
        f = next(f for f in lf.formats(languages("radarr")) if f["name"] == lf.ORIGINAL_ENGLISH)
        self.assertEqual([(s["name"], s["fields"][0]["value"], s["fields"][1]["value"]) for s in f["specifications"]],
                         [("English", 1, False), ("Original Language", -2, False), ("Not Only The Original Language", -2, True)])


class OwnRejects(unittest.TestCase):
    """Groups TRaSH's group match misses when the tag has no dash (`... H.265 BlackBit.mkv`, 1337x): own title-regex rejects, same shape for each."""
    GROUPS = {"Bad Group: BlackBit": "BlackBit", "Bad Group: Vyndros": "Vyndros"}

    def definition(self, app, name):
        return next(d for d in (json.loads(f.read_text()) for f in (ROOT / "recyclarr" / "custom-formats" / app).glob("*.json")) if d["name"] == name)

    def test_group_matched_in_the_release_title_in_every_tag_shape(self):
        for app in PROFILES:
            for name, g in self.GROUPS.items():
                with self.subTest(app=app, group=g):
                    specs = self.definition(app, name)["specifications"]
                    self.assertEqual([(x["implementation"], x["negate"], x["required"]) for x in specs], [("ReleaseTitleSpecification", False, True)])
                    rx = re.compile(specs[0]["fields"]["value"], re.I)
                    hits = [
                        f"Movie.2001.2160p.UHD.BluRay.HDR.x265-{g}",
                        f"Movie.2001.1080p.BluRay.DDP5.1.H.265 {g}.mkv",
                        f"Movie.2001.1080p.BluRay.x265.{g}",
                        f"Movie.2001.1080p.BluRay.x265_{g}",
                        f"Movie.2001.1080p.BluRay.x265-{g}.mkv",
                        f"Movie.2001.1080p.BluRay.x265 - {g}",
                        f"Movie (2001) [{g}]",
                        f"Movie (2001) ({g})",
                        f"Movie (2001) 1080p x265 HEVC 10bit DDP 5.1 {g}",
                        f"Movie.2001.1080p.WEB-DL.H.265.{g.upper()}",
                        f"Movie.2001.1080p.WEB-DL.H.265-{g.lower()}",
                        f"{g}",
                        f"{g} Movie.2001.1080p",
                    ]
                    for hit in hits:
                        self.assertTrue(rx.search(hit), hit)
                    misses = [
                        f"Movie.2001.1080p.BluRay.x264-{g}er",
                        f"Movie.2001.1080p.BluRay.x264-{g}s",
                        f"Movie.2001.1080p.BluRay.x264-X{g}",
                        f"Movie.2001.1080p.BluRay.x264-{g}2",
                        f"Movie.2001.1080p.BluRay.x264-1{g}",
                        f"Movie.2001.1080p.BluRay.x264-{g[:-1]}",
                        f"{g}um.2001.1080p.WEB-DL",
                        "Black Bitch (2001) 1080p WEB-DL",
                        "Movie.2001.1080p.BluRay.x264-NTb",
                    ]
                    for miss in misses:
                        self.assertFalse(rx.search(miss), miss)

    def test_blackbit_does_not_match_vyndros_and_back(self):
        for app in PROFILES:
            rx = {n: re.compile(self.definition(app, n)["specifications"][0]["fields"]["value"], re.I) for n in self.GROUPS}
            self.assertFalse(rx["Bad Group: BlackBit"].search("Movie.2001.x265-Vyndros"))
            self.assertFalse(rx["Bad Group: Vyndros"].search("Movie.2001.x265-BlackBit"))

    def test_group_rejected_in_every_profile(self):
        for name in self.GROUPS:
            for app, kind, prof in list(every_profile()) + [(app, "swiss", SWISS) for app in PROFILES]:
                with self.subTest(app=app, profile=prof, cf=name):
                    self.assertEqual(profile(app, prof)["scores"].get(name), -2000000 if prof == SWISS else -50000000)


class SwissGerman(unittest.TestCase):
    def test_g_swiss_profile_unchanged(self):
        for app in PROFILES:
            with self.subTest(app=app):
                p = profile(app, SWISS)
                s = p["scores"]
                self.assertEqual(s["Audio: Swiss German"], 1000000)
                self.assertEqual([s["Resolution: 2160p"], s["Resolution: 1080p"], s["Resolution: 720p"], s["Resolution: SD"]], [200000, 150000, 100000, 50000])
                self.assertEqual((s["German DL"], s[lf.ORIGINAL_ENGLISH]), (20000, 20000))
                self.assertEqual((p["cutoff"], p["min"]), (1200000, 0))
                self.assertTrue(all(s[c] == 0 for c in s if c.startswith(lf.EXTRA_PREFIX)))
                self.assertNotIn("HDR", s)
                self.assertNotIn("2160p: HDR bonus offset", s)
                self.assertTrue(all(v == -2000000 for c, v in s.items() if c in ("LQ", "BR-DISK", "Upscaled", "DV (w/o HDR fallback)")))

    def test_g_swiss_german_tag_still_required(self):
        row = next(l for l in (ROOT / "scripts" / "languages.tsv").read_text().splitlines() if l.startswith("gsw\t"))
        self.assertNotEqual(row.split("\t")[2], "-")


if __name__ == "__main__":
    unittest.main(verbosity=1)
