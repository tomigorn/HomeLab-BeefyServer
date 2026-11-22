#!/usr/bin/env python3
"""scripts/upscale.py: own 2160p upscale of downloads phones and TVs cannot play directly (README "Own conversions"), against fakes.
Usage: python3 tests/test_upscale.py"""
import datetime
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("up", ROOT / "scripts" / "upscale.py")
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)

GB = 1_000_000_000


def video(codec="h264", field="progressive", w=1920, h=1080, sar="1:1", tag=None, index=0):
    v = {"index": index, "codec_type": "video", "codec_name": codec, "width": w, "height": h, "sample_aspect_ratio": sar, "avg_frame_rate": "25/1"}
    if field is not None:
        v["field_order"] = field
    if tag:
        v["codec_tag_string"] = tag
    return v


def audio(index, codec="ac3", lang="ger", ch=6, title=None, default=0):
    return {"index": index, "codec_type": "audio", "codec_name": codec, "channels": ch, "tags": {"language": lang, **({"title": title} if title else {})}, "disposition": {"default": default}}


def sub(index, codec="dvd_subtitle", lang="ger"):
    return {"index": index, "codec_type": "subtitle", "codec_name": codec, "tags": {"language": lang}}


def probe(*streams, duration=4604.0):
    return {"streams": list(streams), "format": {"duration": str(duration)}}


RING = probe(video("mpeg2video", "tt", 720, 576, "64:45"),
             audio(1, title="Swiss German (Original)", lang="gsw", default=1), audio(2, title="German"), audio(3, ch=2, title="Deutsch 2.0"), sub(4))


class Detect(unittest.TestCase):
    """Which sources get an own version (owner 2026-11-19: MPEG-2, VC-1, XviD/DivX, WMV, interlaced)."""

    def test_matrix(self):
        cases = [
            (video("mpeg2video", "progressive"), "MPEG-2"),
            (video("vc1"), "VC-1"),
            (video("mpeg4", tag="XVID"), "XviD/DivX"),
            (video("mpeg4", tag="DX50"), "XviD/DivX"),
            (video("msmpeg4v3", tag="DIV3"), "XviD/DivX"),
            (video("wmv3"), "WMV"),
            (video("wmv2"), "WMV"),
            (video("h264", "tt"), "interlaced"),
            (video("h264", "bb"), "interlaced"),
            (video("hevc", "tb"), "interlaced"),
            (video("h264", "progressive"), None),
            (video("hevc", "progressive"), None),
            (video("h264", None), None),
            (video("hevc", "unknown"), None),
            (video("av1"), None),
        ]
        for v, want in cases:
            with self.subTest(codec=v["codec_name"], field=v.get("field_order")):
                self.assertEqual(up.verdict(probe(v, audio(1))), want)

    def test_mpeg2_named_before_interlaced(self):
        self.assertEqual(up.verdict(RING), "MPEG-2")

    def test_cover_art_is_not_the_video(self):
        cover = {**video("mjpeg"), "disposition": {"attached_pic": 1}}
        self.assertEqual(up.verdict(probe(cover, {**video("mpeg2video"), "index": 1}, audio(2))), "MPEG-2")

    def test_no_video_is_not_converted(self):
        self.assertIsNone(up.verdict(probe(audio(0))))


class Size(unittest.TestCase):
    def test_anamorphic_pal_16_9_fills_2160p(self):
        self.assertEqual(up.target_size(video("mpeg2video", "tt", 720, 576, "64:45")), (3840, 2160))

    def test_4_3_keeps_its_shape(self):
        self.assertEqual(up.target_size(video("mpeg2video", "tt", 720, 576, "16:15")), (2880, 2160))

    def test_scope_is_bounded_by_the_width(self):
        self.assertEqual(up.target_size(video("vc1", w=1920, h=800)), (3840, 1600))

    def test_unknown_sar_is_square(self):
        self.assertEqual(up.target_size(video("mpeg4", w=640, h=480, sar="0:1")), (2880, 2160))


class Command(unittest.TestCase):
    """The proven The Ring Thing settings (2026-11-19, README "Own conversions") plus the owner's audio and subtitle rules (2026-11-22)."""

    def setUp(self):
        self.args = up.plan(RING)

    def after(self, flag):
        return self.args[self.args.index(flag) + 1]

    def test_libplacebo_ewa_lanczossharp_to_2160p(self):
        self.assertEqual(self.after("-vf"), "hwupload,libplacebo=w=3840:h=2160:upscaler=ewa_lanczossharp:format=yuv420p,hwdownload,format=yuv420p,setsar=1,setfield=prog")

    def test_x264_crf16_as_proven(self):
        s = " ".join(self.args)
        self.assertIn("-c:v libx264 -preset medium -profile:v high -level 5.1 -pix_fmt yuv420p -crf 16", s)

    def test_output_flagged_progressive(self):
        # x264 encodes progressive (interlaced=0); unmarked frames keep the source's field order in the file and Jellyfin calls the upscale interlaced
        self.assertTrue(self.after("-vf").endswith(",setfield=prog"))
        self.assertNotIn("-field_order", self.args)

    def test_every_audio_track_copied_then_aac_stereo_of_the_original(self):
        maps = [self.args[i + 1] for i, a in enumerate(self.args) if a == "-map"]
        self.assertEqual(maps[:5], ["0:0", "0:1", "0:2", "0:3", "0:1"])
        s = " ".join(self.args)
        self.assertIn("-c:a copy -c:a:3 aac -b:a:3 192k -ac:a:3 2", s)

    def test_aac_stereo_is_the_only_default(self):
        s = " ".join(self.args)
        self.assertIn("-disposition:a 0 -disposition:a:3 default", s)
        self.assertIn("-metadata:s:a:3 language=gsw", s)
        self.assertIn("-metadata:s:a:3 title=Swiss German (Original) · AAC stereo", s)

    def test_bitmap_subtitles_copied_never_burnt_in(self):
        self.assertIn("0:4", self.args)
        self.assertEqual(self.after("-c:s"), "copy")
        self.assertNotIn("subtitles", self.after("-vf"))
        self.assertNotIn("overlay", self.after("-vf"))

    def test_original_by_name_beats_the_default_flag(self):
        p = probe(video("vc1"), audio(1, lang="ger", title="German", default=1), audio(2, lang="eng", title="English (Original)"))
        args = up.plan(p)
        self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == "-map"], ["0:0", "0:1", "0:2", "0:2"])
        self.assertIn("-metadata:s:a:2 language=eng", " ".join(args))

    def test_mov_text_and_data_streams_left_out(self):
        p = probe(video("mpeg4", tag="XVID"), audio(1, "mp3", "eng", 2), sub(2, "mov_text", "eng"), {"index": 3, "codec_type": "data"}, sub(4, "subrip", "eng"))
        maps = [a for i, a in enumerate(up.plan(p)) if i and up.plan(p)[i - 1] == "-map"]
        self.assertEqual(maps, ["0:0", "0:1", "0:1", "0:4"])

    def test_no_audio_no_aac(self):
        s = " ".join(up.plan(probe(video("vc1"))))
        self.assertNotIn("aac", s)


class Names(unittest.TestCase):
    def test_movie_version_next_to_the_film(self):
        src = "/srv/video/media/movies/The Ring Thing (2004) [tmdbid-10248]/The Ring Thing (2004) [tmdbid-10248] - [German DL][DVD][AC3 5.1][MPEG2]-MTZ.mkv"
        self.assertEqual(up.version_path(src), "/srv/video/media/movies/The Ring Thing (2004) [tmdbid-10248]/The Ring Thing (2004) [tmdbid-10248] - 2160p upscale.mkv")

    def test_episode_version_named_up_to_sxxeyy(self):
        src = "/srv/video/media/tv/Show (1999) [tvdbid-1]/Season 01/Show (1999) - S01E02 - Pilot [DVD][MPEG2].avi"
        self.assertEqual(up.version_path(src), "/srv/video/media/tv/Show (1999) [tvdbid-1]/Season 01/Show (1999) - S01E02 - 2160p upscale.mkv")

    def test_own_versions_are_never_sources(self):
        self.assertTrue(up.own_version("/x/F (1) - 2160p upscale.mkv"))
        self.assertTrue(up.own_version("/x/F (1) - Phone 1080p.mkv"))
        self.assertFalse(up.own_version("/x/F (1) - [DVD].mkv"))


class Night(unittest.TestCase):
    def at(self, h, m=0):
        return datetime.datetime(2026, 11, 22, h, m)

    def test_a_film_started_at_2210_fits(self):
        self.assertTrue(up.fits_night(self.at(22, 10), 4604))

    def test_not_in_the_day(self):
        self.assertFalse(up.fits_night(self.at(12), 600))
        self.assertFalse(up.fits_night(self.at(21, 59), 600))

    def test_must_end_before_0600(self):
        self.assertTrue(up.fits_night(self.at(4, 46), 1200))
        self.assertFalse(up.fits_night(self.at(4, 46), 4604))
        self.assertFalse(up.fits_night(self.at(23, 30), 4 * 3600))


class DiskFloor(unittest.TestCase):
    """beefy SSD full = 80 %, HDD full = 90 % (owner); a conversion that would cross it is skipped."""

    def test_estimate_from_the_ring_thing(self):
        self.assertAlmostEqual(up.estimate_bytes(4604) / GB, 20.1, places=1)

    def test_ssd_floor(self):
        self.assertTrue(up.disk_ok("/srv/.disks/ssd-hot", 8000 * GB, 5000 * GB, 20 * GB))
        self.assertFalse(up.disk_ok("/srv/.disks/ssd-hot", 8000 * GB, 6390 * GB, 20 * GB))

    def test_hdd_floor(self):
        self.assertTrue(up.disk_ok("/srv/.disks/hdd-cold", 28000 * GB, 25000 * GB, 20 * GB))
        self.assertFalse(up.disk_ok("/srv/.disks/hdd-cold", 28000 * GB, 25190 * GB, 20 * GB))


class Fake:
    def __init__(self, root, probes, now=datetime.datetime(2026, 11, 22, 22, 10), total=8000 * GB, used=1000 * GB):
        self.root, self.probes, self.now, self.total, self.used = root, probes, now, total, used
        self.pool = root
        self.lock = os.path.join(root, ".lock")
        self.encoded, self.updated, self.bazarr = [], [], []

    def probe(self, path):
        return self.probes[os.path.basename(path)]

    def clock(self):
        return self.now

    def branch(self, path):
        return self.root

    def usage(self, branch):
        return self.total, self.used

    def encode(self, src, work, args):
        self.encoded.append((src, args))
        out = os.path.join(work, up.PARTIAL)
        pathlib.Path(out).write_text("x")
        name = os.path.basename(src)
        p = self.probes[name]
        n = sum(1 for s in p["streams"] if s["codec_type"] == "audio")
        self.probes[up.PARTIAL] = probe(video("h264", "progressive", 3840, 2160), *[audio(i + 1) for i in range(n)], audio(n + 1, "aac", ch=2, default=1),
                                         duration=float(p["format"]["duration"]))
        return 0, ""

    def jellyfin(self, path):
        self.updated.append(path)

    def search_subs(self, app, ident):
        self.bazarr.append((app, ident))


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.film = pathlib.Path(self.root, "media/movies/F (1990) [tmdbid-1]")
        self.film.mkdir(parents=True)
        self.src = self.film / "F (1990) [tmdbid-1] - [DVD][MPEG2].mkv"
        self.src.write_text("dvd")
        (self.film / "F (1990) [tmdbid-1] - [DVD][MPEG2].de.srt").write_text("1\n")
        self.ok = self.film.parent / "G (2020) [tmdbid-2]"
        self.ok.mkdir()
        (self.ok / "G.mkv").write_text("web")
        self.log = pathlib.Path(self.root, "radarr.imports")
        self.log.write_text(f"1795304393\t7\t/data/media/movies/F (1990) [tmdbid-1]/{self.src.name}\t1\tx\t\t\t\n"
                            "1795304394\t8\t/data/media/movies/G (2020) [tmdbid-2]/G.mkv\t2\ty\t\t\t\n")
        self.fake = Fake(self.root, {self.src.name: probe(video("mpeg2video", "tt", 720, 576, "64:45"), audio(1, lang="gsw", default=1), sub(2), duration=600),
                                     "G.mkv": probe(video("hevc"), audio(1))})
        self.state = {}

    def tearDown(self):
        self.tmp.cleanup()

    def detect(self):
        up.detect(self.fake, self.state, {"radarr": str(self.log)}, media=os.path.join(self.root, "media"))

    def test_detect_queues_only_the_incompatible_import(self):
        self.detect()
        self.assertEqual([r["src"] for r in self.state["titles"].values()], [str(self.src)])
        r = self.state["titles"][str(self.src)]
        self.assertEqual((r["state"], r["reason"], r["app"], r["id"]), ("queued", "MPEG-2", "radarr", 7))
        self.assertEqual(self.state["pending"], 1)

    def test_detect_reads_each_import_once(self):
        self.detect()
        self.fake.probes = {}
        self.detect()
        self.assertEqual(self.state["pending"], 1)

    def test_an_existing_version_is_registered_not_queued(self):
        pathlib.Path(up.version_path(str(self.src))).write_text("4k")
        self.detect()
        self.assertEqual(self.state["titles"][str(self.src)]["state"], "done")
        self.assertEqual(self.state["pending"], 0)

    def test_run_converts_places_and_hints(self):
        self.detect()
        up.run(self.fake, self.state)
        r = self.state["titles"][str(self.src)]
        self.assertEqual(r["state"], "done", r.get("note"))
        dest = pathlib.Path(up.version_path(str(self.src)))
        self.assertTrue(dest.exists())
        self.assertEqual(oct(dest.stat().st_mode & 0o777), "0o664")
        self.assertEqual(pathlib.Path(str(dest)[:-4] + ".de.srt").read_text(), "1\n")
        self.assertEqual(self.fake.updated, [str(self.film)])
        self.assertEqual(self.fake.bazarr, [("radarr", 7)])
        self.assertEqual(self.state["pending"], 0)
        self.assertEqual(list(pathlib.Path(self.root, up.WORK).iterdir()), [])

    def test_run_skips_over_the_disk_floor_and_keeps_it_queued(self):
        self.detect()
        self.fake.used = 6399 * GB
        up.run(self.fake, self.state)
        r = self.state["titles"][str(self.src)]
        self.assertEqual(r["state"], "queued")
        self.assertIn("disk", r["note"])
        self.assertEqual(self.fake.encoded, [])

    def test_run_does_nothing_by_day(self):
        self.detect()
        self.fake.now = datetime.datetime(2026, 11, 22, 14, 0)
        up.run(self.fake, self.state)
        self.assertEqual(self.fake.encoded, [])
        self.assertEqual(self.state["titles"][str(self.src)]["state"], "queued")

    def test_a_failed_check_keeps_the_library_clean(self):
        self.detect()

        def bad(src, work, args):
            pathlib.Path(work, up.PARTIAL).write_text("x")
            self.fake.probes[up.PARTIAL] = probe(video("h264", w=3840, h=2160), duration=10)
            return 0, ""
        self.fake.encode = bad
        up.run(self.fake, self.state)
        self.assertEqual(self.state["titles"][str(self.src)]["state"], "failed")
        self.assertFalse(pathlib.Path(up.version_path(str(self.src))).exists())

    def test_an_error_while_placing_leaves_no_work_file(self):
        self.detect()
        self.fake.lock = self.root
        up.run(self.fake, self.state)
        self.assertEqual(self.state["titles"][str(self.src)]["state"], "failed")
        self.assertEqual(list(pathlib.Path(self.root, up.WORK).iterdir()), [])
        self.assertFalse(pathlib.Path(up.version_path(str(self.src))).exists())

    def test_a_failed_title_can_be_queued_again(self):
        self.detect()
        self.state["titles"][str(self.src)]["state"] = "failed"
        up.register(self.fake, self.state, str(self.src), "radarr", 7)
        self.assertEqual(self.state["titles"][str(self.src)]["state"], "queued")

    def test_subtitles_mirrored_again_on_later_runs(self):
        self.detect()
        up.run(self.fake, self.state)
        (self.film / f"{self.src.stem}.en.srt").write_text("2\n")
        up.run(self.fake, self.state)
        self.assertTrue(pathlib.Path(up.version_path(str(self.src))[:-4] + ".en.srt").exists())


class StateFile(unittest.TestCase):
    def test_round_trip_and_pending_for_fastpi(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            up.save_state(p, {"titles": {"a": {"state": "queued"}}, "seen": {}})
            got = json.loads(pathlib.Path(p).read_text())
            self.assertEqual(got["pending"], 1)
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o644")


if __name__ == "__main__":
    unittest.main()
