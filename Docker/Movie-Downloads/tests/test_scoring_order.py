#!/usr/bin/env python3
"""Release ranking order (README "Release scoring") from recyclarr/recyclarr.yml + scripts/language_formats.py, both apps, every profile: HDR > language tier > extra languages > audio/group; rejects stay rejected; upgrades stop where they did; Swiss German unchanged.
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
    "radarr": {"4k": "4K HDR - UHD Bluray + WEB (1080p fallback)", "any": "Any quality - old & rare (upgrades to 4K)",
               "hd": "HD Bluray + WEB", "anime": "Anime - Remux + WEB 1080p"},
    "sonarr": {"4k": "4K HDR - WEB-2160p (1080p fallback)", "any": "Any quality - old & rare (upgrades to 4K)",
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
    if res == 2160:
        out.add("2160p: HDR bonus offset")
        if not hdr:
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
            minor = ANIME_MINOR | {"audio": MINOR[app]["audio"]} if kind == "anime" else MINOR[app]
            resolutions = (1080, 2160) if kind in ("4k", "any") else (1080,)
            self.cases.append((app, kind, name, twin, orig, minor, resolutions, profile(app, name)))

    def tiers(self, twin, orig):
        """Language tiers, best first, without extras."""
        return [[orig, "German"], [orig]] if twin else [[orig, "English", "German"], [orig, "English"], [orig, "German"], [orig]]

    def extras(self, app, orig):
        return [l["name"] for l in languages(app) if l["name"] not in lf.NOT_EXTRA and l["name"] != orig]

    def best(self, minor):
        return {"audio": max(minor["audio"], key=lambda k: minor["audio"][k]), "group": max(minor["group"], key=lambda k: minor["group"][k]), "small": tuple(minor["small"])}


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

    def test_e_extra_language_beats_audio_and_group(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                five = score(p, matched(app, orig, [orig, "English", "German", "Portuguese", "French"], audio="DD+" if app == "radarr" else None))
                three = score(p, matched(app, orig, [orig, "English", "German"], **self.best(minor)))
                self.assertGreater(five, three)
                self.assertGreater(lf.EXTRA_SCORE, max(minor["audio"].values()) + max(minor["group"].values()) + sum(minor["small"].values()))

    def test_f_negatives_still_reject(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            richest = matched(app, orig, [orig, "English", "German"] + self.extras(app, orig), hdr=True, **self.best(minor))
            top = score(p, richest) + (p["scores"].get("Anime Dual Audio", 0))
            for cf, s in p["scores"].items():
                if s < 0 and cf not in MILD_NEGATIVES and cf != "2160p: HDR bonus offset":
                    with self.subTest(app=app, profile=name, reject=cf):
                        self.assertLess(top + s, p["min"])
            with self.subTest(app=app, profile=name, reject="dub only / no original audio"):
                self.assertLess(score(p, matched(app, orig if orig != "German" else "French", ["German"])), p["min"])
            if 2160 in resolutions:
                with self.subTest(app=app, profile=name, reject="2160p SDR"):
                    self.assertLess(score(p, matched(app, orig, [orig, "English", "German"] + self.extras(app, orig), 2160, hdr=False, **self.best(minor))), p["min"])

    def test_scores_fit_int32(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                self.assertLess(sum(s for s in p["scores"].values() if s > 0), 2**31 - 1)
                self.assertGreater(sum(s for s in p["scores"].values() if s < 0), -2**31)


class Upgrades(Base):
    def test_cutoff_at_original_plus_english(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            top_tier, below = (self.tiers(twin, orig)[0], self.tiers(twin, orig)[1]) if twin else (self.tiers(twin, orig)[1], self.tiers(twin, orig)[2])
            for res in resolutions:
                hdr = res == 2160
                with self.subTest(app=app, profile=name, res=res):
                    self.assertGreaterEqual(score(p, matched(app, orig, top_tier, res, hdr=hdr)), p["cutoff"])
                    self.assertLess(score(p, matched(app, orig, below + self.extras(app, orig), res, hdr=hdr, **self.best(minor))), p["cutoff"])

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
