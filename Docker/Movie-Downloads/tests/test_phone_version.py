#!/usr/bin/env python3
"""scripts/phone-version.py: the phone version's release order (fastpi reports/specs/2026-11-19-phone-versions-design.md) on Radarr/Sonarr release-search data.
Usage: python3 tests/test_phone_version.py"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pv", ROOT / "scripts" / "phone-version.py")
pv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pv)

GB = 1_000_000_000
BAD = {"LQ", "Upscaled", "BR-DISK", "DV (w/o HDR fallback)", "x265 (no HDR/DV)", "German", "Language: Not Original", "Remux: smaller encode wins", "Bad Dual Groups"}
TITLE = {"runtime": 120, "original": "English", "size": 60 * GB, "resolution": 2160}


def rel(name="WEBDL-1080p", size=8 * GB, cfs=(), langs=("English",), score=0, title="A.Film.2026.1080p.WEB-DL-GRP", rejections=(), **kw):
    res = int(name.rsplit("-", 1)[1].rstrip("p")) if name[-1] == "p" else 0
    r = {"title": title, "size": size, "quality": {"name": name, "resolution": res, "modifier": "remux" if name.startswith("Remux") else "none"},
         "customFormats": list(cfs), "languages": list(langs), "customFormatScore": score, "rejections": list(rejections), "protocol": "usenet", "indexer": "Example"}
    r.update(kw)
    return r


def pick(releases, title=TITLE, bad=BAD):
    return pv.pick(releases, title, bad)


ENC = dict(size=6 * GB, cfs=["10bit", "x265 (HD)", "HD Bluray Tier 02", "HDR10"], title="A.Film.2026.1080p.BluRay.HDR.x265.10bit-GRP")


class Order(unittest.TestCase):
    def test_hdr_web_dl_first(self):
        hdr = rel(cfs=["HDR"], title="A.Film.2026.1080p.WEB-DL.HDR.H265-GRP")
        r = pick([rel(), hdr, rel("Bluray-1080p", **ENC)])
        self.assertEqual((r["option"], r["release"]["title"]), (1, hdr["title"]))

    def test_dolby_vision_with_hdr10_fallback_counts_as_hdr(self):
        self.assertEqual(pick([rel(cfs=["DV HDR10"])])["option"], 1)

    def test_dolby_vision_without_fallback_is_a_bad_tag(self):
        self.assertEqual(pick([rel(cfs=["DV (w/o HDR fallback)"])])["option"], 3)

    def test_strict_hdr_encode_second(self):
        self.assertEqual(pick([rel("Bluray-1080p", **ENC)])["option"], 2)

    def test_sdr_web_dl_is_no_phone_version(self):
        r = pick([rel()])
        self.assertEqual((r["option"], r["release"], r["rejected"][0]["why"]), (3, None, "1080p without HDR"))

    def test_sdr_encode_is_no_phone_version(self):
        r = pick([rel("Bluray-1080p", **dict(ENC, cfs=["10bit", "x265 (HD)", "HD Bluray Tier 02"]))])
        self.assertEqual((r["option"], r["rejected"][0]["why"]), (3, "1080p without HDR"))

    def test_encode_rules_each_required(self):
        base = dict(ENC, name="Bluray-1080p")
        bad_cases = {
            "untrusted group": dict(base, cfs=["10bit", "x265 (HD)", "HDR10"]),
            "8 bit": dict(base, cfs=["x265 (HD)", "HD Bluray Tier 02", "HDR10"], title="A.Film.2026.1080p.BluRay.HDR.x265-GRP"),
            "h264": dict(base, cfs=["10bit", "HD Bluray Tier 02", "HDR10"], title="A.Film.2026.1080p.BluRay.HDR.10bit.x264-GRP"),
            "too small": dict(base, size=1 * GB),
            "webrip": dict(base, name="WEBRip-1080p"),
            "upscaled": dict(base, cfs=base["cfs"] + ["Upscaled"]),
            "foreign only": dict(base, langs=["French"]),
        }
        for why, kw in bad_cases.items():
            name = kw.pop("name")
            self.assertEqual(pick([rel(name, **kw)])["option"], 3, why)

    def test_fifteen_mb_per_minute(self):
        self.assertEqual(pick([rel("Bluray-1080p", **dict(ENC, size=120 * 15_000_000))])["option"], 2)
        self.assertEqual(pick([rel("Bluray-1080p", **dict(ENC, size=120 * 15_000_000 - 1))])["option"], 3)

    def test_4k_releases_never(self):
        r = pick([rel("WEBDL-2160p", size=20 * GB, cfs=["HDR"])])
        self.assertEqual((r["option"], r["rejected"][0]["why"]), (3, "WEBDL-2160p: not 1080p"))

    def test_a_1080p_release_bigger_than_the_server_copy_is_never_a_phone_version(self):
        r = pick([rel(size=12 * GB, cfs=["HDR"])], dict(TITLE, size=10 * GB))
        self.assertEqual((r["option"], r["rejected"][0]["why"]), (3, "not smaller than the server copy"))

    def test_remux_never(self):
        self.assertEqual(pick([rel("Remux-1080p", size=20 * GB, cfs=["HDR"]), rel("Remux-2160p", size=30 * GB)])["option"], 3)

    def test_own_conversion_when_nothing_fits(self):
        r = pick([])
        self.assertEqual((r["option"], r["label"], r["release"]), (3, "own conversion", None))

    def test_releases_tried_before_are_skipped(self):
        a, b = rel(cfs=["HDR"], score=900, title="a"), rel(cfs=["HDR"], score=100, title="b")
        r = pv.pick([a, b], TITLE, BAD, tried=["a"])
        self.assertEqual(r["release"]["title"], "b")
        self.assertEqual(r["rejected"], [{"title": "a", "why": "tried before"}])
        self.assertEqual(pv.pick([a, b], TITLE, BAD, tried=["a", "b"])["option"], 3)

    def test_server_copy_not_4k_needs_no_phone_version(self):
        r = pick([rel(cfs=["HDR"])], dict(TITLE, resolution=1080))
        self.assertEqual((r["option"], r["why"]), (4, "the server copy is not 4K"))


class Rules(unittest.TestCase):
    def test_language_rule_original_counts(self):
        self.assertEqual(pick([rel(langs=["Japanese"], cfs=["HDR"])], dict(TITLE, original="Japanese"))["option"], 1)

    def test_german_counts(self):
        self.assertEqual(pick([rel(langs=["German"], cfs=["German", "HDR"])])["option"], 1)

    def test_unknown_language_fails(self):
        self.assertEqual(pick([rel(langs=["Unknown"], cfs=["HDR"])])["option"], 3)

    def test_wrong_title_never(self):
        self.assertEqual(pick([rel(cfs=["HDR"], rejections=["Unknown Movie. Unable to match to correct movie using release title."])])["option"], 3)

    def test_arr_rejections_about_the_server_copy_do_not_count(self):
        r = pick([rel(cfs=["HDR"], rejections=["Existing file on disk is of equal or higher preference: WEBDL-2160p", "WEBDL-1080p is not wanted in profile"])])
        self.assertEqual(r["option"], 1)

    def test_audio_description_releases_never(self):
        ad = rel(size=7 * GB, cfs=["HDR"], title="A.Film.2026.with.Audio.Description.1080p.WEB-DL.DDP2.0-GRP")
        plain = rel(size=8 * GB, cfs=["HDR"], title="A.Film.2026.1080p.WEB-DL.DDP5.1-GRP")
        r = pick([ad, plain])
        self.assertEqual(r["release"]["title"], plain["title"])
        self.assertEqual(r["rejected"], [{"title": ad["title"], "why": "audio description"}])

    def test_hardcoded_subtitles_never(self):
        for t in ("A.Film.2026.HC.1080p.WEB-DL-GRP", "A.Film.2026.1080p.WEB-DL.KORSUB-GRP", "A.Film.2026.1080p.HardSub.WEB-DL-GRP", "A.Film.2026.1080p.Hardcoded.Subs.WEB-DL-GRP", "A.Film.2026.1080p.Hard.Subbed.WEB-DL-GRP"):
            self.assertEqual(pick([rel(title=t)])["rejected"], [{"title": t, "why": "hardcoded subtitles"}], t)

    def test_hc_inside_a_word_is_no_tag(self):
        self.assertEqual(pick([rel(cfs=["HDR"], title="A.Film.HCMC.2026.1080p.WEB-DL-GRP")])["option"], 1)

    def test_best_score_wins_inside_an_option(self):
        a, b = rel(score=100, title="a", cfs=["HDR"]), rel(score=900, title="b", cfs=["HDR"])
        self.assertEqual(pick([a, b])["release"]["title"], "b")

    def test_equal_score_smaller_wins(self):
        a, b = rel(size=9 * GB, title="a", cfs=["HDR"]), rel(size=5 * GB, title="b", cfs=["HDR"])
        self.assertEqual(pick([a, b])["release"]["title"], "b")

    def test_bad_tags_come_from_the_profile_minus_the_ones_the_order_handles(self):
        profile = {"formatItems": [{"name": "LQ", "score": -50000000}, {"name": "x265 (no HDR/DV)", "score": -50000000}, {"name": "German", "score": -50000000},
                                   {"name": "DV (w/o HDR fallback)", "score": -50000000}, {"name": "HDR", "score": 20000000}, {"name": "SDR", "score": -50000}]}
        self.assertEqual(pv.bad_formats(profile), {"LQ", "DV (w/o HDR fallback)"})

    def test_why_names_each_rule_that_failed(self):
        r = pick([rel("Bluray-1080p", size=1 * GB, cfs=["x265 (HD)", "HDR10"], title="A.1080p.BluRay.HDR.x265-GRP")])
        self.assertIn("encode", r["rejected"][0]["why"])
        self.assertIn("trusted group", r["rejected"][0]["why"])
        self.assertIn("10-bit", r["rejected"][0]["why"])


if __name__ == "__main__":
    unittest.main()
