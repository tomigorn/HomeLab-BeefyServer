#!/usr/bin/env python3
"""Release ranking order (README "Release scoring", "Which version gets downloaded") from recyclarr/recyclarr.yml + scripts/language_formats.py, both apps, every profile: HDR > language tier > Radarr longer cut > audio/group/streaming/repack > bonuses (extras, a season pack, each extra dub); size scores nothing; lossless audio formats equal; a remux never outranks a 2160p encode; 2160p SDR allowed; rejects stay rejected; silent films are exempt from `Language: Not Original`; upgrades stop at 4K HDR + original + English (Radarr: + a longer cut), any real improvement upgrades below it (step 25), the bonuses alone never do; Swiss German unchanged.
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
               "hd": "WEB-1080p", "anime": "Anime - Bluray + WEB 1080p", "silent": "4K preferred · older versions allowed - silent film"},
}
# Profiles with the small bonuses (extra dubs, extras, a season pack) and the minimum upgrade step above them; anime and Swiss German keep step 1 and no bonuses.
BONUS_KINDS = ("4k", "any", "hd", "silent")
# Audio ladder (owner 2026-10-31, recyclarr.yml): lossless formats equal; objects (Atmos, DTS:X) a small plus on lossless; lossy by real quality, DD+ Atmos on top.
AUDIO = {"TrueHD ATMOS": 3000, "DTS X": 3000, "TrueHD": 2750, "DTS-HD MA": 2750, "FLAC": 2750, "PCM": 2750, "DD+ ATMOS": 2250, "ATMOS (undefined)": 2250,
         "DTS-HD HRA": 2000, "DD+": 1750, "DTS-ES": 1000, "DTS": 750, "DD": 750, "AAC": 500}
LOSSLESS = ("TrueHD", "DTS-HD MA", "FLAC", "PCM")
# TRaSH default scores Recyclarr syncs and recyclarr.yml does not override (as synced 2026-10-26): group tiers, streaming, repacks.
MINOR = {
    "radarr": {"audio": AUDIO | {None: 0},
               "group": {"UHD Bluray Tier 01": 1800, "UHD Bluray Tier 02": 1750, "UHD Bluray Tier 03": 1700, "HD Bluray Tier 01": 1800, "WEB Tier 01": 1700, "WEB Tier 02": 1650, "WEB Tier 03": 1600, None: 0},
               "small": {"MA": 20, "IMF": 20, "CRiT": 20, "BCORE": 15, "Repack/Proper": 25, "Repack2": 50, "Repack3": 75}},
    "sonarr": {"audio": AUDIO | {None: 0},
               "group": {"WEB Tier 01": 1700, "WEB Tier 02": 1650, "WEB Tier 03": 1600, "WEB Scene": 1600, None: 0},
               "small": {"NF": 75, "UHD Streaming Boost": 75, "HD Streaming Boost": 75, "Repack/Proper": 25, "Repack2": 50, "Repack3": 75}},
}
ANIME_MINOR = {"group": {"Anime BD Tier 01": 1400, "Remux Tier 01": 975, None: 0}, "small": {"VRV": 10, "CR": 6, "v1": 1, "v2": 2, "v4": 4, "Repack3": 7}}
# Ranking penalties, not rejects: TRaSH `v0` (-51); own `Remux: smaller encode wins` keeps a remux at or below a 2160p encode.
REMUX = "Remux: smaller encode wins"
REMUX_SCORE = -6000
REMUX_MIN = -10000
MILD_NEGATIVES = {"v0", REMUX}
SEASON_PACK = "Season Pack"
SEASON_PACK_SCORE = 5
# Radarr extras (own formats, recyclarr/custom-formats/radarr): the film plus bonus material wins a tie; an extras-only release stays rejected.
EXTRAS = "Extras: included"
EXTRAS_SCORE = 5
EXTRAS_ONLY = "Extras: only"
# Release titles name a handful of languages; up to this many extra dubs plus extras or a pack stay below the minimum upgrade step.
# `Extra audio: <original>` also fires once a second language is there (language_formats.py), so the original counts as one more.
MAX_TITLE_DUBS = 18
# Silent films (owner 2026-10-31): Radarr's own reject needs a film from 1930 on (YearSpecification); Sonarr moves pre-1930 series to the silent profile, where it is 0.
NOT_ORIGINAL = {"radarr": "Language: Not Original (sound film)", "sonarr": "Language: Not Original"}
SOUND_FROM = 1930
# Radarr only (owner 2026-10-30): longer cuts rank below HDR and every language rung, above audio/group/streaming; versions are tie-breakers.
EDITION = "Edition: longer cut"
EDITION_SCORE = 1000000
SWISS_EDITION_SCORE = 2000
VERSIONS = {"IMAX": 800, "IMAX Enhanced": 800, "Remaster": 25, "4K Remaster": 25, "Criterion Collection": 25}
THEATRICAL = "Theatrical Cut"
MIN_UPGRADE = 25
NO_BONUS_MIN_UPGRADE = 1


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


def matched(app, original, langs, res=1080, hdr=False, audio=None, group=None, small=(), extra=(), year=2000):
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
        if app == "sonarr" or year >= SOUND_FROM:
            out.add(NOT_ORIGINAL[app])
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
            if app == "radarr":
                minor = minor | {"small": minor["small"] | VERSIONS}
            resolutions = (1080, 2160) if kind in ("4k", "any", "silent") else (1080,)
            prof = profile(app, name)
            prof["scores"] = {k: v for group in minor.values() for k, v in group.items() if k} | prof["scores"]
            self.cases.append((app, kind, name, twin, orig, minor, resolutions, prof))

    def tiers(self, twin, orig):
        """Language tiers, best first, without extras."""
        return [[orig, "German"], [orig]] if twin else [[orig, "English", "German"], [orig, "English"], [orig, "German"], [orig]]

    def extras(self, app, orig):
        return [l["name"] for l in languages(app) if l["name"] not in lf.NOT_EXTRA and l["name"] != orig]

    def best(self, minor):
        return {"audio": max(minor["audio"], key=lambda k: minor["audio"][k]), "group": max(minor["group"], key=lambda k: minor["group"][k]), "small": tuple(minor["small"]), "extra": (SEASON_PACK, EXTRAS)}

    def best_single(self, minor):
        return self.best(minor) | {"extra": (EXTRAS,)}


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

    def test_b_c_each_extra_dub_adds_a_small_bonus(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            step = lf.EXTRA_SCORE if kind in BONUS_KINDS else 0
            for tier in self.tiers(twin, orig):
                with self.subTest(app=app, profile=name, tier=tier):
                    base = score(p, matched(app, orig, tier))
                    two = score(p, matched(app, orig, tier + ["Portuguese", "Spanish" if orig != "Spanish" else "French"]))
                    one = score(p, matched(app, orig, tier + ["Portuguese"]))
                    first = 2 * step if tier == [orig] and orig not in ("English", "German") else step
                    self.assertEqual(one, base + first)
                    self.assertEqual(two, one + step)


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
                self.assertGreater(min(bonuses), 0)

    def test_e_streaming_web_tier_01_beats_an_extra_dub(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if app == "sonarr" and kind != "anime":
                with self.subTest(profile=name):
                    tier = [orig, "German"] if twin else [orig, "English"]
                    dub = "Italian" if orig != "Italian" else "French"
                    self.assertGreater(score(p, matched(app, orig, tier, 2160, hdr=True, group="WEB Tier 01", small=("UHD Streaming Boost",))),
                                       score(p, matched(app, orig, tier + [dub], 2160, hdr=True)))

    def test_extra_score(self):
        self.assertEqual(lf.EXTRA_SCORE, 1)
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                self.assertEqual(p["scores"][lf.EXTRA_PREFIX + "Portuguese"], 1 if kind in BONUS_KINDS else 0)
        for app in PROFILES:
            self.assertEqual(profile(app, SWISS)["scores"][lf.EXTRA_PREFIX + "Portuguese"], 0)

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
        want = {"sonarr": {"4k": (25000000, 20000000), "any": (25000000, 20000000), "hd": (5000000, None), "anime": (5000000, None), "silent": (25000000, None)},
                "radarr": {"4k": (26000000, 21000000), "any": (26000000, 21000000), "hd": (6000000, None), "anime": (6000000, None)}}
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                self.assertEqual(p["cutoff"], want[app][kind][twin])

    def test_j_4k_cutoff_at_hdr_plus_original_plus_english(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if 2160 not in resolutions:
                continue
            top_tier, below = ([orig], None) if twin else (self.tiers(twin, orig)[1], self.tiers(twin, orig)[2])
            ed = (EDITION,) if app == "radarr" else ()
            with self.subTest(app=app, profile=name):
                self.assertGreaterEqual(score(p, matched(app, orig, top_tier, 2160, hdr=True, extra=ed)), p["cutoff"])
                if ed:
                    self.assertLess(score(p, matched(app, orig, top_tier + self.extras(app, orig), 2160, hdr=True, **self.best_single(minor))), p["cutoff"])
                if twin:
                    self.assertEqual(p["cutoff"], 21000000 if ed else 20000000)
                    self.assertGreater(score(p, matched(app, orig, [orig, "German"], 2160, hdr=True)), score(p, matched(app, orig, [orig], 2160, hdr=True)))
                    self.assertGreaterEqual(score(p, matched(app, orig, [orig, "German"], 2160, hdr=True)), p["cutoff"])
                else:
                    self.assertLess(score(p, matched(app, orig, below + self.extras(app, orig), 2160, hdr=True, **self.best_single(minor) | {"extra": ed})), p["cutoff"])
                richest_sdr = [orig, "English", "German"] + self.extras(app, orig)
                self.assertLess(score(p, matched(app, orig, richest_sdr, 2160, hdr=False, **self.best_single(minor) | {"extra": ed})), p["cutoff"])

    def test_1080p_profiles_cutoff_at_original_plus_english(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if 2160 in resolutions:
                continue
            ed = (EDITION,) if app == "radarr" else ()
            with self.subTest(app=app, profile=name):
                self.assertGreaterEqual(score(p, matched(app, orig, [orig, "English"], extra=ed)), p["cutoff"])
                if ed:
                    self.assertLess(score(p, matched(app, orig, [orig, "English"] + self.extras(app, orig), **self.best_single(minor))), p["cutoff"])
                self.assertLess(score(p, matched(app, orig, [orig, "German"] + self.extras(app, orig), **self.best(minor) | {"extra": ed + self.best(minor)["extra"]})), p["cutoff"])

    def test_min_upgrade_step(self):
        for app, kind, name in list(every_profile()) + [(a, "swiss", SWISS) for a in PROFILES]:
            with self.subTest(app=app, profile=name):
                self.assertEqual(profile(app, name)["min_upgrade"], MIN_UPGRADE if kind in BONUS_KINDS else NO_BONUS_MIN_UPGRADE)

    def test_every_real_improvement_upgrades_below_the_cutoff(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            tiers = self.tiers(twin, orig)
            with self.subTest(app=app, profile=name):
                base = matched(app, orig, [orig])
                fix, broken = ({"v2"}, {"v1"}) if kind == "anime" else ({"Repack/Proper"}, set())
                gains = {"repack / anime v2": score(p, base | fix) - score(p, base | broken),
                         "best group": score(p, base | {self.best(minor)["group"]}) - score(p, base)}
                if max(minor["audio"].values()) > 0:
                    gains["DD+ over AAC"] = score(p, base | {"DD+"}) - score(p, base | {"AAC"})
                    gains["TrueHD Atmos over DTS-HD MA"] = score(p, base | {"TrueHD ATMOS"}) - score(p, base | {"DTS-HD MA"})
                if kind != "anime":
                    gains["Repack2 over Repack"] = score(p, base | {"Repack2"}) - score(p, base | {"Repack/Proper"})
                    gains["group tier 02 -> 01"] = score(p, base | {"WEB Tier 01"}) - score(p, base | {"WEB Tier 02"})
                    gains["remaster" if app == "radarr" else "streaming service"] = score(p, base | {"Remaster" if app == "radarr" else "NF"}) - score(p, base)
                for what, gain in gains.items():
                    self.assertGreaterEqual(gain, p["min_upgrade"], what)
                for hi, lo in zip(tiers, tiers[1:]):
                    self.assertGreaterEqual(score(p, matched(app, orig, hi)) - score(p, matched(app, orig, lo + self.extras(app, orig), **self.best_single(minor))), p["min_upgrade"])
                for res in resolutions:
                    self.assertGreaterEqual(score(p, matched(app, orig, [orig], res, hdr=True)) - score(p, matched(app, orig, [orig, "English", "German"] + self.extras(app, orig), res, **self.best_single(minor))), p["min_upgrade"])

    def test_bonuses_alone_never_upgrade(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            for tier in self.tiers(twin, orig):
                with self.subTest(app=app, profile=name, tier=tier):
                    rich = matched(app, orig, tier + self.extras(app, orig)[:MAX_TITLE_DUBS], extra=(EXTRAS, SEASON_PACK))
                    self.assertLess(score(p, rich) - score(p, matched(app, orig, tier)), p["min_upgrade"])

    def test_extra_scores_survive_the_recyclarr_reset(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                self.assertTrue(any(re.match(pat, lf.EXTRA_PREFIX + "Spanish", re.I) for pat in p["except"].get("except_patterns") or []))
                if not twin:
                    self.assertIn(lf.ORIGINAL_ENGLISH, p["except"].get("except") or [])


class Editions(Base):
    def radarr(self):
        return [c for c in self.cases if c[0] == "radarr"]

    def test_edition_and_version_scores(self):
        self.assertIn(THEATRICAL, cf_names("radarr").values())
        self.assertNotIn("Special Edition", cf_names("radarr").values())
        for app, kind, name, twin, orig, minor, resolutions, p in self.radarr():
            with self.subTest(profile=name):
                raw = profile(app, name)["scores"]
                self.assertEqual({c: raw.get(c) for c in [EDITION, THEATRICAL, *VERSIONS]}, {EDITION: EDITION_SCORE, THEATRICAL: 0, **VERSIONS})
        self.assertEqual(profile("radarr", SWISS)["scores"].get(EDITION), SWISS_EDITION_SCORE)
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if app == "sonarr":
                self.assertNotIn(EDITION, p["scores"])

    def test_edition_beats_the_best_theatrical_of_the_same_languages(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.radarr():
            for tier in self.tiers(twin, orig):
                for res in resolutions:
                    with self.subTest(profile=name, tier=tier, res=res):
                        self.assertGreater(score(p, matched(app, orig, tier, res, hdr=True, extra=(EDITION,))),
                                           score(p, matched(app, orig, tier + self.extras(app, orig), res, hdr=True, **self.best_single(minor))))

    def test_edition_never_beats_a_better_language_tier(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.radarr():
            tiers = self.tiers(twin, orig)
            for hi, lo in zip(tiers, tiers[1:]):
                with self.subTest(profile=name, higher=hi, lower=lo):
                    self.assertGreater(score(p, matched(app, orig, hi)), score(p, matched(app, orig, lo + self.extras(app, orig), **self.best_single(minor) | {"extra": (EDITION,)})))

    def test_edition_never_beats_hdr(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.radarr():
            for res in resolutions:
                with self.subTest(profile=name, res=res):
                    self.assertGreater(score(p, matched(app, orig, [orig], res, hdr=True)),
                                       score(p, matched(app, orig, [orig, "English", "German"] + self.extras(app, orig), res, **self.best_single(minor) | {"extra": (EDITION,)})))

    def test_swiss_edition_stays_below_resolution_and_language(self):
        s = profile("radarr", SWISS)["scores"]
        rest = s["German DL"] + s[lf.ORIGINAL_ENGLISH] + max(MINOR["radarr"]["audio"].values()) + max(MINOR["radarr"]["group"].values()) + sum(MINOR["radarr"]["small"].values())
        self.assertLess(rest + s[EDITION], s["Resolution: 2160p"] - s["Resolution: 1080p"])
        self.assertLess(s[EDITION] + max(MINOR["radarr"]["audio"].values()) + max(MINOR["radarr"]["group"].values()), s["German DL"])


class LongerCut(unittest.TestCase):
    """Own `Edition: longer cut` (recyclarr/custom-formats/radarr/edition-longer-cut.json): TRaSH `Special Edition` also hits anniversary, collector's, remastered editions and TV versions (same cut, one more download)."""

    def rx(self):
        d = next(json.loads(f.read_text()) for f in (ROOT / "recyclarr" / "custom-formats" / "radarr").glob("*.json") if json.loads(f.read_text())["name"] == EDITION)
        self.assertRegex(d["trash_id"], r"^[0-9a-f]{32}$")
        self.assertFalse(d["includeCustomFormatWhenRenaming"])
        specs = d["specifications"]
        self.assertTrue(all(x["implementation"] == "ReleaseTitleSpecification" and x["required"] for x in specs))
        return [(re.compile(x["fields"]["value"], re.I), x["negate"]) for x in specs]

    def hit(self, title):
        return all(bool(rx.search(title)) != neg for rx, neg in self.rx())

    def test_longer_cuts_match(self):
        for t in ("A.Movie.2001.Extended.2160p.UHD.BluRay.x265-GRP", "A.Movie.2001.EXTENDED.EDITION.1080p.BluRay.x264-GRP", "A.Movie.1979.Directors.Cut.1080p.BluRay.x264-GRP",
                  "A.Movie.1979.Director's.Cut.1080p", "A.Movie.2004.Uncut.1080p.BluRay.x264-GRP", "A.Movie.2004.UNRATED.1080p.WEB-DL-GRP",
                  "A.Movie.1977.Special.Edition.1080p.BluRay-GRP", "A.Movie.2003.Ultimate.Edition.1080p.BluRay-GRP", "A.Movie.2016.Ultimate.Cut.2160p-GRP",
                  "A.Movie.1982.Final.Cut.2160p.UHD.BluRay-GRP", "A.Movie.1979.Redux.1080p.BluRay-GRP", "A Movie (2001) [tmdbid-1] - {Extended} [Bluray-2160p]-GRP",
                  "A.Movie.2001.Extended.Cut.1080p-GRP", "A.Movie.2005.Uncensored.1080p-GRP"):
            with self.subTest(t=t):
                self.assertTrue(self.hit(t))

    def test_same_cut_versions_do_not_match(self):
        for t in ("A.Movie.1994.25th.Anniversary.Edition.1080p.BluRay-GRP", "A.Movie.1994.Collectors.Edition.1080p-GRP", "A.Movie.1994.Remastered.Edition.2160p-GRP",
                  "A.Movie.1994.Limited.Edition.1080p-GRP", "A.Movie.2012.TV.Version.720p-GRP", "A.Movie.2012.IMAX.Version.2160p-GRP", "A.Movie.2012.IMAX.Edition.2160p-GRP",
                  "A.Movie.2012.Special.Features.1080p-GRP", "A.Movie.2001.Theatrical.Cut.1080p-GRP", "A.Movie.2001.Extended.and.Theatrical.1080p-GRP",
                  "A.Movie.2001.Extended.Clip.1080p-GRP", "A.Movie.2001.Open.Matte.1080p-GRP", "A.Movie.2001.1080p.BluRay.DDP5.1.x264-GRP",
                  "A Movie (2001) [tmdbid-1] - {edition-Remastered} [Bluray-2160p]-GRP", "Extended.2001.1080p.BluRay-GRP"):
            with self.subTest(t=t):
                self.assertFalse(self.hit(t))


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
                want = SEASON_PACK_SCORE if app == "sonarr" and kind in BONUS_KINDS else 0
                self.assertEqual(p["scores"].get(SEASON_PACK, 0), want)
        self.assertEqual(profile("sonarr", SWISS)["scores"].get(SEASON_PACK, 0), 0)

    def test_season_pack_wins_a_tie_but_never_upgrades_alone(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if app == "sonarr" and kind in BONUS_KINDS:
                for res in resolutions:
                    with self.subTest(profile=name, res=res):
                        tier = [orig, "German"] if twin else [orig, "English"]
                        pack, single = score(p, matched(app, orig, tier, res, hdr=True, extra=(SEASON_PACK,))), score(p, matched(app, orig, tier, res, hdr=True))
                        self.assertGreater(pack, single)
                        self.assertLess(pack - single, p["min_upgrade"])

    def test_season_pack_never_beats_a_real_minor_step(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if app == "sonarr" and kind in BONUS_KINDS:
                tier = [orig, "German"] if twin else [orig, "English"]
                dubs = self.extras(app, orig)[:MAX_TITLE_DUBS]
                pack = score(p, matched(app, orig, tier + dubs, 2160, hdr=True, group="WEB Tier 02", extra=(SEASON_PACK,)))
                for what, single in (("repack", {"Repack/Proper"}), ("group tier 01", set()), ("streaming service", {"NF"})):
                    with self.subTest(profile=name, single=what):
                        cfs = matched(app, orig, tier, 2160, hdr=True, group="WEB Tier 01" if what == "group tier 01" else "WEB Tier 02", small=tuple(single))
                        self.assertGreater(score(p, cfs), pack)

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


def qualities(app, name):
    """Quality groups of a profile, best first, as recyclarr.yml lists them (None when the TRaSH template sets them)."""
    inst = CFG[app]["movies" if app == "radarr" else "series"]
    q = next((q for q in inst["quality_profiles"] if q.get("name") == name), {})
    return [set(g.get("qualities") or [g["name"]]) for g in q["qualities"]] if "qualities" in q else None


def rank(app, name, quality):
    return next(i for i, g in enumerate(qualities(app, name)) if quality in g)


def upgrades(p, app, name, have, cand):
    """Radarr/Sonarr upgrade rule below the cutoff: a better quality group, or the same group and a score gain of at least the minimum step."""
    (hq, hcf), (cq, ccf) = have, cand
    hr, cr = rank(app, name, hq), rank(app, name, cq)
    return cr < hr or (cr == hr and score(p, ccf) - score(p, hcf) >= p["min_upgrade"])


class RealQuality(Base):
    """Owner 2026-10-31: swap for any real (visible or audible) improvement, never for size, bitrate or more languages alone."""

    def audio_cases(self):
        return [c for c in self.cases if max(c[5]["audio"].values()) > 0]

    def test_audio_is_scored_in_the_4k_and_any_profiles_of_both_apps(self):
        self.assertEqual({(c[0], c[1]) for c in self.audio_cases()}, {(a, k) for a in PROFILES for k in ("4k", "any")} | {("sonarr", "silent")})
        for app in PROFILES:
            for name in [PROFILES[app]["4k"], PROFILES[app]["4k"] + TWIN, PROFILES[app]["any"], PROFILES[app]["any"] + TWIN, SWISS]:
                with self.subTest(app=app, profile=name):
                    raw = profile(app, name)["scores"]
                    self.assertEqual({c: raw.get(c) for c in AUDIO}, AUDIO)

    def test_truehd_and_dts_hd_ma_equal(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.audio_cases():
            with self.subTest(app=app, profile=name):
                base = matched(app, orig, [orig, "English"], 2160, hdr=True)
                self.assertEqual(score(p, base | {"TrueHD"}), score(p, base | {"DTS-HD MA"}))
                self.assertEqual({p["scores"][c] for c in LOSSLESS}, {p["scores"]["TrueHD"]})

    def test_truehd_atmos_beats_dts_hd_ma_and_equals_dts_x(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.audio_cases():
            with self.subTest(app=app, profile=name):
                s = p["scores"]
                self.assertGreater(s["TrueHD ATMOS"], s["DTS-HD MA"])
                self.assertEqual(s["TrueHD ATMOS"], s["DTS X"])
                self.assertLessEqual(s["TrueHD ATMOS"] - s["TrueHD"], 500)

    def test_lossy_ladder(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.audio_cases():
            with self.subTest(app=app, profile=name):
                s = p["scores"]
                self.assertGreater(s["DD+"], s["AAC"])
                self.assertGreater(s["DD+"], s["DD"])
                self.assertGreater(s["DD"], s["AAC"])
                self.assertEqual(s["DTS"], s["DD"])
                self.assertGreater(s["DD+ ATMOS"], s["DD+"])
                self.assertGreater(s["TrueHD"], s["DD+ ATMOS"])
                self.assertGreater(s["DD+ ATMOS"], s["DTS-HD HRA"])
                self.assertGreater(s["DTS-HD HRA"], s["DD+"])

    def test_bigger_file_of_the_same_quality_is_equal(self):
        for f in (ROOT / "recyclarr" / "custom-formats").glob("*/*.json"):
            with self.subTest(cf=f.name):
                self.assertNotIn("SizeSpecification", {x["implementation"] for x in json.loads(f.read_text())["specifications"]})
        # TRaSH formats are checked live: scripts/scoring-check.py fails when any scored format has a size condition.

    def test_repack_is_higher(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                base = matched(app, orig, [orig, "English"], 2160, hdr=True)
                if kind == "anime":
                    self.assertGreater(score(p, base | {"v2"}), score(p, base | {"v1"}))
                    continue
                self.assertGreater(score(p, base | {"Repack/Proper"}), score(p, base))
                self.assertGreater(score(p, base | {"Repack2"}), score(p, base | {"Repack/Proper"}))

    def test_better_tier_group_is_higher(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if kind == "anime":
                continue
            with self.subTest(app=app, profile=name):
                base = matched(app, orig, [orig, "English"], 2160, hdr=True)
                self.assertGreater(score(p, base | {"WEB Tier 01"}), score(p, base | {"WEB Tier 03"}))
                self.assertGreater(score(p, base | {"WEB Tier 03"}), score(p, base))
                if app == "radarr":
                    self.assertGreater(score(p, base | {"UHD Bluray Tier 01"}), score(p, base | {"UHD Bluray Tier 03"}))

    def test_eac3_beats_aac(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.audio_cases():
            with self.subTest(app=app, profile=name):
                base = matched(app, orig, [orig, "English"], 2160, hdr=True)
                self.assertGreater(score(p, base | {"DD+"}), score(p, base | {"AAC"}))


class Remux(Base):
    """Owner 2026-10-31: a remux is the untouched disc stream (largest); good encodes look the same at a fraction of the size, so they win."""

    def remux_cases(self):
        return [c for c in self.cases if c[0] == "radarr" and c[1] in ("4k", "any")]

    def test_remux_allowed_below_every_2160p_encode_and_above_1080p(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.remux_cases():
            with self.subTest(profile=name):
                self.assertEqual(rank(app, name, "Bluray-2160p"), 0)
                self.assertEqual(rank(app, name, "Remux-2160p"), rank(app, name, "WEBDL-2160p"))
                self.assertLess(rank(app, name, "Remux-2160p"), rank(app, name, "Bluray-1080p"))
                self.assertEqual(p["scores"][REMUX], REMUX_SCORE)
                self.assertEqual(p["min"], REMUX_MIN)
                self.assertEqual(p["cutoff"], profile(app, name)["cutoff"])
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if (app, kind) not in {("radarr", "4k"), ("radarr", "any")}:
                with self.subTest(app=app, profile=name):
                    self.assertEqual(p["scores"].get(REMUX, 0), 0)

    def test_remux_format_is_the_remux_modifier(self):
        d = next(json.loads(f.read_text()) for f in (ROOT / "recyclarr" / "custom-formats" / "radarr").glob("*.json") if json.loads(f.read_text())["name"] == REMUX)
        self.assertRegex(d["trash_id"], r"^[0-9a-f]{32}$")
        self.assertFalse(d["includeCustomFormatWhenRenaming"])
        self.assertEqual([(x["implementation"], x["negate"], x["required"], x["fields"]["value"]) for x in d["specifications"]], [("QualityModifierSpecification", False, True, 5)])

    def test_dv_tier_1_encode_ranks_at_or_above_a_dv_remux(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.remux_cases():
            langs = [orig, "German"] if twin else [orig, "English"]
            remux = matched(app, orig, langs, 2160, hdr=True, audio="TrueHD ATMOS", small=("Repack3", "Remaster", "Criterion Collection"), extra=(REMUX,))
            for q, enc in (("Bluray-2160p", matched(app, orig, langs, 2160, hdr=True, group="UHD Bluray Tier 01")), ("WEBDL-2160p", matched(app, orig, langs, 2160, hdr=True, group="WEB Tier 01"))):
                with self.subTest(profile=name, encode=q):
                    self.assertLessEqual(rank(app, name, q), rank(app, name, "Remux-2160p"))
                    self.assertGreaterEqual(score(p, enc), score(p, remux))

    def test_existing_tier_encode_never_upgrades_to_a_remux(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.remux_cases():
            langs = [orig, "German"] if twin else [orig, "English"]
            remux = ("Remux-2160p", matched(app, orig, langs, 2160, hdr=True, audio="TrueHD ATMOS", small=("Repack3", "Remaster", "4K Remaster", "Criterion Collection", "IMAX", "IMAX Enhanced"), extra=(REMUX,)))
            for have in (("WEBDL-2160p", matched(app, orig, langs, 2160, hdr=True, audio="AAC")),("Bluray-2160p", matched(app, orig, langs, 2160, hdr=True, group="UHD Bluray Tier 03", audio="DD")),
                         ("WEBDL-2160p", matched(app, orig, langs, 2160, hdr=True, group="WEB Tier 03")),
                         ("WEBDL-2160p", matched(app, orig, langs, 2160, hdr=True))):
                with self.subTest(profile=name, have=have[0], cfs=sorted(have[1])):
                    self.assertFalse(upgrades(p, app, name, have, remux))

    def test_remux_still_upgrades_1080p_and_sdr(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.remux_cases():
            langs = [orig, "German"] if twin else [orig, "English"]
            remux = ("Remux-2160p", matched(app, orig, langs, 2160, hdr=True, audio="TrueHD ATMOS", extra=(REMUX,)))
            with self.subTest(profile=name):
                self.assertTrue(upgrades(p, app, name, ("Bluray-1080p", matched(app, orig, langs, 1080, hdr=True, group="HD Bluray Tier 01", audio="DD+")), remux))
                self.assertTrue(upgrades(p, app, name, ("WEBDL-2160p", matched(app, orig, langs, 2160, hdr=False, group="WEB Tier 01", audio="DD+ ATMOS")), remux))
                self.assertGreaterEqual(score(p, remux[1]), p["min"])
                plain_sdr = matched(app, orig, [orig], 2160, hdr=False, extra=(REMUX,))
                self.assertGreaterEqual(score(p, plain_sdr), p["min"], "an SDR original-only remux with no audio named stays grabbable")

    def test_min_score_still_rejects(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.remux_cases():
            with self.subTest(profile=name):
                best = sum(v for v in p["scores"].values() if v > 0)
                self.assertLess(best - 50000000, p["min"])

    def test_resolution_and_hdr_stay_upgrades(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.remux_cases():
            langs = [orig, "German"] if twin else [orig, "English"]
            with self.subTest(profile=name):
                self.assertTrue(upgrades(p, app, name, ("Bluray-1080p", matched(app, orig, langs, 1080, hdr=True, audio="TrueHD ATMOS")), ("WEBDL-2160p", matched(app, orig, langs, 2160, hdr=True))))
                self.assertTrue(upgrades(p, app, name, ("Bluray-2160p", matched(app, orig, langs, 2160, hdr=False, audio="TrueHD ATMOS", group="UHD Bluray Tier 01")), ("Bluray-2160p", matched(app, orig, langs, 2160, hdr=True))))



def own_format(app, name):
    return next(d for d in (json.loads(f.read_text()) for f in (ROOT / "recyclarr" / "custom-formats" / app).glob("*.json")) if d["name"] == name)


def title_hit(d, title):
    return all(bool(re.search(x["fields"]["value"], title, re.I)) != x["negate"] for x in d["specifications"])


class Bonuses(Base):
    """Owner 2026-10-31: extra dubs, extras and a season pack count when choosing between new releases, but never beat a real difference and never trigger an upgrade on their own."""

    def bonus_cases(self):
        return [c for c in self.cases if c[1] in BONUS_KINDS]

    def test_same_quality_plus_an_extra_dub_is_higher_but_no_upgrade(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.bonus_cases():
            dub = "Portuguese" if orig != "Portuguese" else "French"
            for tier in self.tiers(twin, orig):
                with self.subTest(app=app, profile=name, tier=tier):
                    have = matched(app, orig, tier, 2160 if 2160 in resolutions else 1080, hdr=True, audio="DD+", group="WEB Tier 01")
                    cand = matched(app, orig, tier + [dub], 2160 if 2160 in resolutions else 1080, hdr=True, audio="DD+", group="WEB Tier 01")
                    self.assertGreater(score(p, cand), score(p, have))
                    self.assertLess(score(p, cand) - score(p, have), p["min_upgrade"])

    def test_extras_beat_a_dub_and_never_upgrade_alone(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.bonus_cases():
            if app != "radarr":
                continue
            tier = [orig, "German"] if twin else [orig, "English"]
            dub = "Portuguese" if orig != "Portuguese" else "French"
            with self.subTest(profile=name):
                extras, one_dub, plain = (score(p, matched(app, orig, tier, extra=(EXTRAS,))), score(p, matched(app, orig, tier + [dub])), score(p, matched(app, orig, tier)))
                self.assertGreater(extras, one_dub)
                self.assertGreater(one_dub, plain)
                self.assertLess(extras - plain, p["min_upgrade"])

    def test_any_real_quality_step_beats_all_bonuses_combined(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.bonus_cases():
            tiers = self.tiers(twin, orig)
            every = self.extras(app, orig)
            bonus = (EXTRAS, SEASON_PACK)
            res = 2160 if 2160 in resolutions else 1080
            with self.subTest(app=app, profile=name, step="HDR"):
                self.assertGreater(score(p, matched(app, orig, tiers[-1], res, hdr=True)), score(p, matched(app, orig, tiers[-1] + every, res, extra=bonus)))
            for hi, lo in zip(tiers, tiers[1:]):
                with self.subTest(app=app, profile=name, step=f"{hi} over {lo}"):
                    self.assertGreater(score(p, matched(app, orig, hi)), score(p, matched(app, orig, lo + every, extra=bonus)))
            if app == "radarr":
                with self.subTest(profile=name, step="longer cut"):
                    self.assertGreater(score(p, matched(app, orig, tiers[0], extra=(EDITION,))), score(p, matched(app, orig, tiers[0] + every, extra=bonus)))
            if max(minor["audio"].values()) > 0:
                for better, worse in (("TrueHD ATMOS", "TrueHD"), ("DD+ ATMOS", "DTS-HD HRA"), ("DD", "AAC"), ("DD+", "DD")):
                    with self.subTest(app=app, profile=name, step=f"{better} over {worse}"):
                        self.assertGreater(score(p, matched(app, orig, tiers[0], audio=better)), score(p, matched(app, orig, tiers[0] + every, audio=worse, extra=bonus)))
            title = self.extras(app, orig)[:MAX_TITLE_DUBS]
            minors = [("Repack/Proper", None), ("Repack2", "Repack/Proper"), ("WEB Tier 01", "WEB Tier 02")]
            minors += [("Remaster", None)] if app == "radarr" else [("NF", None)]
            for better, worse in minors:
                with self.subTest(app=app, profile=name, step=f"{better} over {worse}", dubs=len(title)):
                    lo = {worse} if worse else set()
                    self.assertGreater(score(p, matched(app, orig, tiers[0], small=(better,))), score(p, matched(app, orig, tiers[0] + title, small=tuple(lo), extra=bonus)))
                    self.assertGreaterEqual(score(p, matched(app, orig, tiers[0], small=(better,))) - score(p, matched(app, orig, tiers[0], small=tuple(lo))), p["min_upgrade"])

    def test_bonus_scores(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            with self.subTest(app=app, profile=name):
                raw = profile(app, name)["scores"]
                bonus = kind in BONUS_KINDS
                if app == "radarr":
                    self.assertEqual(raw.get(EXTRAS, 0), EXTRAS_SCORE if bonus else 0)
                    self.assertEqual(raw.get(EXTRAS_ONLY), -50000000)
                self.assertEqual({c: raw.get(c) for c in ("Repack/Proper", "Repack2", "Repack3")} if bonus else {}, {"Repack/Proper": 25, "Repack2": 50, "Repack3": 75} if bonus else {})
        s = profile("radarr", SWISS)["scores"]
        self.assertEqual((s.get(EXTRAS, 0), s.get(EXTRAS_ONLY)), (0, -2000000))


class Extras(unittest.TestCase):
    """Radarr: `Extras: included` (the film plus bonus material, +5) and `Extras: only` (a bonus disc alone, rejected); TRaSH `Extras` (any extras word after the year, -10000) is left out."""
    INCLUDED = ["A.Movie.2001.1080p.BluRay.x264.+Extras-GRP", "A Movie (2001) 1080p BluRay x265 + Extras", "A.Movie.2001.1080p.BluRay.x264.incl.Extras-GRP",
                "A.Movie.2001.2160p.UHD.BluRay.x265-GRP [Including Bonus Disc]", "A Movie 2001 1080p BluRay with Extras", "A Movie 2001 1080p BluRay w/ Featurettes",
                "A.Movie.2001.1080p.BluRay.x264.Plus.Special.Features-GRP", "A.Movie.2001.1080p.BluRay.and.Behind.the.Scenes-GRP", "A Movie (2001) [1080p] [Extras Included]",
                "A.Movie.2001.1080p.BluRay.&.Bonus.Material-GRP", "A.Movie.2001.1080p.BluRay.x264.INKL.EXTRAS-GRP"]
    ONLY = ["A.Movie.2001.EXTRAS.1080p.BluRay.x264-GRP", "A.Movie.2001.Bonus.Disc.1080p.BluRay-GRP", "A.Movie.2001.Special.Features.1080p-GRP",
            "A.Movie.2001.Featurettes.720p-GRP", "A.Movie.2001.Behind.the.Scenes.1080p-GRP", "A.Movie.2001.Extended.Clip.1080p-GRP", "A.Movie.2001.1080p.BluRay.Bonus-GRP"]
    NEITHER = ["A.Movie.2001.1080p.BluRay.x264-GRP", "Bonus.Track.2023.1080p.WEB-DL-GRP", "Behind.the.Scenes.2019.1080p.WEB-DL-GRP", "The.Extras.1999.1080p.BluRay-GRP",
               "A.Movie.2001.Extended.1080p.BluRay-GRP", "A.Movie.Plus.Extras.1080p.BluRay-GRP", "A.Movie.2001.1080p.BluRay.DDP5.1.x264-WithEXTRAS"]

    def test_definitions(self):
        for name in (EXTRAS, EXTRAS_ONLY):
            with self.subTest(cf=name):
                d = own_format("radarr", name)
                self.assertRegex(d["trash_id"], r"^[0-9a-f]{32}$")
                self.assertFalse(d["includeCustomFormatWhenRenaming"])
                self.assertTrue(all(x["implementation"] == "ReleaseTitleSpecification" and x["required"] for x in d["specifications"]))

    def test_included(self):
        d = own_format("radarr", EXTRAS)
        for t in self.INCLUDED:
            with self.subTest(t=t):
                self.assertTrue(title_hit(d, t))
        for t in self.ONLY + self.NEITHER:
            with self.subTest(t=t):
                self.assertFalse(title_hit(d, t))

    def test_only(self):
        d = own_format("radarr", EXTRAS_ONLY)
        for t in self.ONLY:
            with self.subTest(t=t):
                self.assertTrue(title_hit(d, t))
        for t in self.INCLUDED + self.NEITHER:
            with self.subTest(t=t):
                self.assertFalse(title_hit(d, t))

    def test_trash_extras_left_out_of_radarr(self):
        self.assertNotIn("0a3f082873eb454bde444150b70253cc", [t for b in CFG["radarr"]["movies"]["custom_formats"] for t in b["trash_ids"]])
        groups = CFG["radarr"]["movies"]["custom_format_groups"]["add"]
        unwanted = next(g for g in groups if g["trash_id"] == "a3ac6af01d78e4f21fcb75f601ac96df")
        self.assertIn("0a3f082873eb454bde444150b70253cc", unwanted.get("exclude") or [])


class SilentFilms(Base):
    """Owner 2026-10-31: silent films (before 1930) are exempt from the `Language: Not Original` reject; every other reject stays."""

    def test_radarr_reject_needs_a_sound_film(self):
        d = own_format("radarr", NOT_ORIGINAL["radarr"])
        self.assertRegex(d["trash_id"], r"^[0-9a-f]{32}$")
        self.assertEqual([(x["implementation"], x["negate"], x["required"], x["fields"]) for x in d["specifications"]],
                         [("LanguageSpecification", True, True, {"value": -2, "exceptLanguage": False}), ("YearSpecification", False, True, {"min": SOUND_FROM, "max": 3000})])
        radarr = YML.read_text().split("\nsonarr:")[0]
        self.assertNotIn("d6e9318c875905d6cfb5bee961afcea9", radarr)
        for app, kind, name in every_profile():
            if app == "radarr":
                with self.subTest(profile=name):
                    self.assertEqual(profile(app, name)["scores"].get(NOT_ORIGINAL["radarr"]), -50000000)
        self.assertEqual(profile("radarr", SWISS)["scores"].get(NOT_ORIGINAL["radarr"], 0), 0)

    def test_radarr_silent_film_with_english_intertitles_accepted(self):
        for app, kind, name, twin, orig, minor, resolutions, p in self.cases:
            if app != "radarr" or twin:
                continue
            group = "Anime BD Tier 01" if kind == "anime" else "WEB Tier 01"
            with self.subTest(profile=name):
                self.assertGreaterEqual(score(p, matched(app, "French", ["English"], group=group, year=1915)), p["min"])
                self.assertGreaterEqual(score(p, matched(app, "French", ["English"], group=group, year=1929)), p["min"])
                self.assertLess(score(p, matched(app, "French", ["English"], group=group, year=1930)), p["min"])
                self.assertLess(score(p, matched(app, "French", ["English"], group=group, year=1915) | {"LQ"}), p["min"])

    def test_sonarr_silent_profile_is_any_quality_without_the_language_reject(self):
        sil, base = PROFILES["sonarr"]["silent"], PROFILES["sonarr"]["any"]
        a, b = profile("sonarr", sil), profile("sonarr", base)
        self.assertEqual(a["scores"].pop("Language: Not Original", 0), 0)
        self.assertEqual(b["scores"].pop("Language: Not Original"), -50000000)
        self.assertEqual(a, b)
        self.assertEqual(qualities("sonarr", sil), qualities("sonarr", base))

    def test_sonarr_silent_series_accepted_on_the_silent_profile_only(self):
        cases = {c[2]: c for c in self.cases if c[0] == "sonarr"}
        sil, base = cases[PROFILES["sonarr"]["silent"]][7], cases[PROFILES["sonarr"]["any"]][7]
        english = matched("sonarr", "French", ["English"], group="WEB Tier 01")
        self.assertGreaterEqual(score(sil, english), sil["min"])
        self.assertLess(score(base, english), base["min"])
        self.assertLess(score(sil, english | {"LQ"}), sil["min"])
        self.assertLess(score(sil, matched("sonarr", "French", ["German"])), sil["min"])


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
