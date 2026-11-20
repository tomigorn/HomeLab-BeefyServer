#!/usr/bin/env python3
# hook/quality_check.py. Host: a fake ffprobe. In the worker image, real samples built from ffmpeg's own test pattern (no library files):
# sudo docker run --rm --network none --user 65112 -v "$PWD/Docker/Antivirus:/src:ro" --entrypoint python3 clamav-local:latest /src/tests/test_quality.py
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("quality_check", os.path.join(HERE, "..", "hook", "quality_check.py"))
qc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qc)


class Decide(unittest.TestCase):
    def test_clean_timeline(self):
        self.assertEqual(qc.jumps(["0.0", "0.08", "0.04", "0.12", "1.0", "2.5"]), [])

    def test_bframe_reordering_under_a_second_is_no_jump(self):
        self.assertEqual(qc.jumps(["10.0", "9.2", "10.5"]), [])

    def test_backward_jump_over_a_second(self):
        self.assertEqual(qc.jumps(["279.0", "280.9", "118.0", "118.04"]), [(280.9, 118.0)])

    def test_missing_timestamps_are_skipped(self):
        self.assertEqual(qc.jumps(["N/A", "", "1.0", "N/A", "2.0"]), [])

    def test_streams_pick_the_first_real_video(self):
        streams = [{"index": 0, "codec_type": "video", "disposition": {"attached_pic": 1}}, {"index": 1, "codec_type": "video", "disposition": {"attached_pic": 0}},
                   {"index": 2, "codec_type": "audio"}]
        self.assertEqual(qc.video_index(streams), 1)

    def test_cover_art_alone_is_no_video(self):
        self.assertIsNone(qc.video_index([{"index": 0, "codec_type": "video", "disposition": {"attached_pic": 1}}, {"index": 1, "codec_type": "audio"}]))

    def test_verdict_blocks_jump_no_video_no_audio_and_notes_container_errors(self):
        self.assertEqual(qc.verdict(True, True, [], ""), ("ok", [], []))
        self.assertEqual(qc.verdict(True, True, [(280.9, 118.0)], "")[:2], ("broken", ["timestamp jump 280.9 s -> 118.0 s"]))
        self.assertEqual(qc.verdict(False, True, [], "")[:2], ("broken", ["no video stream"]))
        self.assertEqual(qc.verdict(True, False, [], "")[:2], ("broken", ["no audio stream"]))
        self.assertEqual(qc.verdict(True, True, [], "[matroska @ 0x1] Read error at pos. 123\nmore"), ("ok", [], ["container error: [matroska] Read error at pos. 123"]))

    def test_several_jumps_are_counted_once_each(self):
        v = qc.verdict(True, True, [(10.0, 1.0), (20.0, 2.0), (30.0, 3.0)], "")
        self.assertEqual(v[1], ["timestamp jump 10.0 s -> 1.0 s (3 jumps)"])


class FakeProbe(unittest.TestCase):
    """The two ffprobe calls through a fake binary: stream list, then video packets."""

    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t)

    def probe(self, streams, pts, err="", rc=0):
        p = os.path.join(self.t, "ffprobe")
        with open(p, "w") as f:
            f.write("""#!%s
import json, sys
if "packet=pts_time" in sys.argv:
    sys.stdout.write(%r)
    sys.stderr.write(%r)
    sys.exit(%d)
print(json.dumps({"streams": %r, "format": {"duration": "1860.5"}}))
""" % (sys.executable, "\n".join(pts) + "\n", err, rc, streams))
        os.chmod(p, 0o755)
        return p

    def test_clean_file(self):
        p = self.probe([{"index": 0, "codec_type": "video"}, {"index": 1, "codec_type": "audio"}], ["0.0", "1.0"])
        r = qc.check("/x.mkv", ffprobe=p)
        self.assertEqual((r["verdict"], r["reasons"], r["notes"], r["duration"]), ("ok", [], [], 1860.5))

    def test_jump_blocks(self):
        p = self.probe([{"index": 0, "codec_type": "video"}, {"index": 1, "codec_type": "audio"}], ["0.0", "280.9", "118.0"])
        self.assertEqual(qc.check("/x.mkv", ffprobe=p)["verdict"], "broken")

    def test_no_audio_blocks_without_reading_packets(self):
        p = self.probe([{"index": 0, "codec_type": "video"}], ["0.0", "280.9", "118.0"])
        r = qc.check("/x.mkv", ffprobe=p)
        self.assertEqual((r["verdict"], r["reasons"]), ("broken", ["no audio stream"]))

    def test_unreadable_container_is_no_video(self):
        p = os.path.join(self.t, "ffprobe")
        with open(p, "w") as f:
            f.write("#!/bin/sh\necho 'Invalid data found when processing input' >&2\nexit 1\n")
        os.chmod(p, 0o755)
        r = qc.check("/x.mkv", ffprobe=p)
        self.assertEqual((r["verdict"], r["reasons"]), ("broken", ["no video stream"]))

    def test_crashing_ffprobe_is_an_error_not_a_block(self):
        p = os.path.join(self.t, "ffprobe")
        with open(p, "w") as f:
            f.write("#!/bin/sh\necho 'Input/output error' >&2\nexit 1\n")
        os.chmod(p, 0o755)
        self.assertEqual(qc.check("/x.mkv", ffprobe=p)["verdict"], "error")

    def test_missing_ffprobe_is_an_error_not_a_block(self):
        r = qc.check("/x.mkv", ffprobe=os.path.join(self.t, "nope"))
        self.assertEqual(r["verdict"], "error")

    def test_packet_read_error_is_a_note(self):
        p = self.probe([{"index": 0, "codec_type": "video"}, {"index": 1, "codec_type": "audio"}], ["0.0", "1.0"], err="[matroska,webm @ 0x55] File ended prematurely\n")
        r = qc.check("/x.mkv", ffprobe=p)
        self.assertEqual((r["verdict"], r["notes"]), ("ok", ["container error: [matroska,webm] File ended prematurely"]))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg/ffprobe (worker image)")
class RealSamples(unittest.TestCase):
    """Samples from ffmpeg's generated test pattern: two segments concatenated out of order, a truncated tail, no audio."""

    @classmethod
    def setUpClass(cls):
        cls.t = tempfile.mkdtemp()

        def make(name, seconds, audio=True):
            cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=%d" % seconds]
            if audio:
                cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=%d" % seconds, "-c:a", "aac"]
            subprocess.run(cmd + ["-c:v", "libx264", "-preset", "ultrafast", os.path.join(cls.t, name)], check=True)
        make("clean.mkv", 10)
        make("a.ts", 10)
        make("b.ts", 5)
        make("silent.mkv", 5, audio=False)
        with open(os.path.join(cls.t, "jump.ts"), "wb") as out:
            for n in ("a.ts", "b.ts"):
                with open(os.path.join(cls.t, n), "rb") as f:
                    out.write(f.read())
        with open(os.path.join(cls.t, "clean.mkv"), "rb") as f:
            data = f.read()
        with open(os.path.join(cls.t, "cut.mkv"), "wb") as f:
            f.write(data[: len(data) * 2 // 3])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.t)

    def check(self, name):
        return qc.check(os.path.join(self.t, name))

    def test_clean(self):
        r = self.check("clean.mkv")
        self.assertEqual((r["verdict"], r["reasons"], r["notes"]), ("ok", [], []))
        self.assertAlmostEqual(r["duration"], 10, delta=0.5)

    def test_segments_out_of_order(self):
        r = self.check("jump.ts")
        self.assertEqual(r["verdict"], "broken")
        self.assertTrue(r["reasons"][0].startswith("timestamp jump"), r)

    def test_no_audio(self):
        self.assertEqual(self.check("silent.mkv")["reasons"], ["no audio stream"])

    def test_truncated_tail_is_a_note_not_a_block(self):
        r = self.check("cut.mkv")
        self.assertEqual((r["verdict"], r["notes"]), ("ok", ["container error: [matroska,webm] File ended prematurely"]))


if __name__ == "__main__":
    unittest.main()
