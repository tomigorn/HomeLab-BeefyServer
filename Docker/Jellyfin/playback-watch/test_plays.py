import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plays  # noqa: E402

HOUR, DAY = 3600, 86400
T0 = 1_000_000.0


def sess(sid="s1", item_id="i1", method="DirectPlay", info=None, rng="SDR", series=None, season=1, ep=2,
         name="Pilot", year=2020, user="jane", paused=False):
    item = {"Id": item_id, "Name": name, "ProductionYear": year,
            "MediaStreams": [{"Type": "Video", "Codec": "hevc", "VideoRangeType": rng}]}
    if series:
        item.update(SeriesName=series, ParentIndexNumber=season, IndexNumber=ep)
    s = {"Id": sid, "UserName": user, "Client": "Jellyfin Web", "DeviceName": "Safari iPhone", "NowPlayingItem": item,
         "PlayState": {"PlayMethod": method, "IsPaused": paused}}
    if info is not None:
        s["TranscodingInfo"] = info
    return s


def info(video_direct, audio_direct, reasons=("ContainerNotSupported",), codec="h264", bitrate=4_000_000):
    return {"IsVideoDirect": video_direct, "IsAudioDirect": audio_direct, "TranscodeReasons": list(reasons),
            "VideoCodec": codec, "Bitrate": bitrate}


class ClassifyTest(unittest.TestCase):
    def test_methods(self):
        self.assertEqual(plays.classify(sess(method="DirectPlay")), "direct")
        self.assertEqual(plays.classify(sess(method="DirectStream")), "remux")
        self.assertEqual(plays.classify(sess(method="Transcode", info=info(True, True))), "remux")
        self.assertEqual(plays.classify(sess(method="Transcode", info=info(True, False))), "audio")
        self.assertEqual(plays.classify(sess(method="Transcode", info=info(False, True))), "video")
        self.assertEqual(plays.classify(sess(method="Transcode", info=info(False, False))), "video")

    def test_transcode_without_info_is_at_least_remux(self):
        self.assertEqual(plays.classify(sess(method="Transcode")), "remux")

    def test_unknown_play_method_is_direct(self):
        self.assertEqual(plays.classify(sess(method=None)), "direct")

    def test_hdr_to_sdr(self):
        v = info(False, True, codec="h264")
        self.assertEqual(plays.hdr_to_sdr(sess(method="Transcode", info=v, rng="HDR10")), "yes")
        self.assertEqual(plays.hdr_to_sdr(sess(method="Transcode", info=v, rng="DOVI")), "yes")
        self.assertEqual(plays.hdr_to_sdr(sess(method="Transcode", info=v, rng="SDR")), "no")

    def test_hdr_kept_when_output_is_hevc_without_range_reason(self):
        v = info(False, True, codec="hevc")
        self.assertEqual(plays.hdr_to_sdr(sess(method="Transcode", info=v, rng="HDR10")), "no")

    def test_hdr10plus_to_hevc_with_tonemapping_enabled(self):
        v = info(False, True, ["ContainerNotSupported", "VideoCodecTagNotSupported"], codec="hevc")
        s = sess(method="Transcode", info=v, rng="HDR10Plus")
        self.assertEqual(plays.hdr_to_sdr(s, tonemapping=True), "yes")
        self.assertEqual(plays.hdr_to_sdr(s, tonemapping=False), "no")
        self.assertEqual(plays.hdr_to_sdr(sess(method="Transcode", info=v, rng="SDR"), tonemapping=True), "no")

    def test_tonemapping_enabled_from_encoding_xml(self):
        d = pathlib.Path(tempfile.mkdtemp())
        p = d / "encoding.xml"
        self.assertFalse(plays.tonemapping_enabled(p))
        p.write_text("<EncodingOptions><EnableTonemapping>false</EnableTonemapping>"
                     "<EnableVppTonemapping>true</EnableVppTonemapping></EncodingOptions>")
        self.assertTrue(plays.tonemapping_enabled(p))
        p.write_text("<EncodingOptions><EnableTonemapping>false</EnableTonemapping></EncodingOptions>")
        self.assertFalse(plays.tonemapping_enabled(p))
        p.write_text("not xml")
        self.assertFalse(plays.tonemapping_enabled(p))

    def test_hdr_to_sdr_by_range_reason_with_hevc_output(self):
        v = info(False, True, reasons=["VideoRangeTypeNotSupported"], codec="hevc")
        self.assertEqual(plays.hdr_to_sdr(sess(method="Transcode", info=v, rng="HDR10")), "yes")

    def test_hdr_needs_video_conversion(self):
        self.assertEqual(plays.hdr_to_sdr(sess(method="Transcode", info=info(True, False), rng="HDR10")), "no")
        self.assertEqual(plays.hdr_to_sdr(sess(method="DirectPlay", rng="HDR10")), "no")

    def test_unknown_range_is_no(self):
        v = info(False, True)
        self.assertEqual(plays.hdr_to_sdr(sess(method="Transcode", info=v, rng=None)), "no")
        self.assertEqual(plays.hdr_to_sdr(sess(method="Transcode", info=v, rng="Unknown")), "no")

    def test_title(self):
        self.assertEqual(plays.title(sess(series="The Show", season=1, ep=2)["NowPlayingItem"]), "The Show S01E02")
        self.assertEqual(plays.title(sess(name="Film", year=1999)["NowPlayingItem"]), "Film (1999)")
        self.assertEqual(plays.title(sess(name="Film", year=None)["NowPlayingItem"]), "Film")
        self.assertEqual(plays.title(sess(series="The Show", season=None, ep=None, name="Special")["NowPlayingItem"]),
                         "The Show Special")


class PlayLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.log = plays.PlayLog(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        p = self.dir / "plays.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def test_play_recorded_after_gap(self):
        self.log.update([sess()], T0)
        self.log.update([sess()], T0 + 5)
        self.assertEqual(self.rows(), [])
        self.log.update([], T0 + 5 + plays.GAP)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["start"], r["end"], r["user"], r["client"], r["device"]),
                         (T0, T0 + 5, "jane", "Jellyfin Web", "Safari iPhone"))
        self.assertEqual((r["title"], r["item_id"], r["method"], r["reasons"], r["hdr_to_sdr"]),
                         ("Pilot (2020)", "i1", "direct", [], "no"))
        self.assertEqual((r["source_codec"], r["source_range"]), ("hevc", "SDR"))

    def test_gap_under_5min_is_same_playback(self):
        self.log.update([sess()], T0)
        self.log.update([], T0 + 100)
        self.log.update([sess()], T0 + 200)
        self.log.update([], T0 + 200 + plays.GAP)
        self.assertEqual(len(self.rows()), 1)

    def test_gap_over_5min_starts_new_playback(self):
        self.log.update([sess()], T0)
        self.log.update([], T0 + plays.GAP)
        self.log.update([sess()], T0 + plays.GAP + 10)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(len(self.log.recent(T0 + plays.GAP + 10)), 2)

    def test_different_item_or_session_is_new_playback(self):
        self.log.update([sess(), sess(sid="s2")], T0)
        self.log.update([sess(item_id="i2"), sess(sid="s2")], T0 + 5)
        self.assertEqual(len(self.log.recent(T0 + 5)), 3)

    def test_worst_method_wins_and_reasons_collected(self):
        self.log.update([sess()], T0)
        self.log.update([sess(method="Transcode", info=info(False, True, ["VideoCodecNotSupported"]))], T0 + 5)
        self.log.update([sess(method="DirectStream")], T0 + 10)
        self.log.update([sess(method="Transcode", info=info(False, True, ["ContainerNotSupported"]))], T0 + 15)
        (r,) = self.log.recent(T0 + 15)
        self.assertEqual(r["method"], "video")
        self.assertEqual(r["reasons"], ["VideoCodecNotSupported", "ContainerNotSupported"])
        self.assertEqual((r["output_codec"], r["bitrate"]), ("h264", 4_000_000))

    def test_playlog_uses_encoding_xml_and_rereads_on_change(self):
        enc = self.dir / "encoding.xml"
        enc.write_text("<E><EnableTonemapping>true</EnableTonemapping></E>")
        log = plays.PlayLog(self.dir, encoding_path=enc)
        v = info(False, True, ["ContainerNotSupported"], codec="hevc")
        log.update([sess(method="Transcode", info=v, rng="HDR10Plus")], T0)
        self.assertEqual(log.recent(T0)[0]["hdr_to_sdr"], "yes")
        enc.write_text("<E><EnableTonemapping>false</EnableTonemapping></E>")
        import os
        os.utime(enc, (T0 + 5, T0 + 5))
        log.update([sess(sid="s2", method="Transcode", info=v, rng="HDR10Plus")], T0 + 5)
        self.assertEqual([r["hdr_to_sdr"] for r in log.recent(T0 + 5)], ["yes", "no"])

    def test_hdr_to_sdr_sticky(self):
        s = sess(method="Transcode", info=info(False, True), rng="HDR10")
        self.log.update([s], T0)
        self.log.update([sess(method="DirectStream", rng="HDR10")], T0 + 5)
        (r,) = self.log.recent(T0 + 5)
        self.assertEqual((r["hdr_to_sdr"], r["source_range"]), ("yes", "HDR10"))

    def test_paused_playback_continues(self):
        self.log.update([sess()], T0)
        self.log.update([sess(paused=True)], T0 + 600)
        self.log.update([sess()], T0 + 605)
        self.assertEqual(len(self.log.recent(T0 + 605)), 1)

    def test_sessions_without_item_ignored(self):
        self.log.update([{"Id": "s9", "UserName": "jane"}], T0)
        self.assertEqual(self.log.recent(T0), [])

    def test_no_ip_stored(self):
        s = sess()
        s["RemoteEndPoint"] = "203.0.113.5"
        self.log.update([s], T0)
        self.log.update([], T0 + plays.GAP)
        self.assertNotIn("203.0.113.5", (self.dir / "plays.jsonl").read_text())
        self.assertNotIn("RemoteEndPoint", self.rows()[0])

    def test_file_mode_0600(self):
        self.log.update([sess()], T0)
        self.log.update([], T0 + plays.GAP)
        self.assertEqual((self.dir / "plays.jsonl").stat().st_mode & 0o777, 0o600)

    def test_restart_keeps_open_playback(self):
        self.log.update([sess()], T0)
        self.log.save_open()
        log2 = plays.PlayLog(self.dir)
        log2.update([sess()], T0 + 10)
        self.assertEqual(len(log2.recent(T0 + 10)), 1)
        self.assertEqual(log2.recent(T0 + 10)[0]["start"], T0)

    def test_restart_after_gap_ends_open_playback(self):
        self.log.update([sess()], T0)
        self.log.save_open()
        log2 = plays.PlayLog(self.dir)
        log2.update([], T0 + 10 * plays.GAP)
        self.assertEqual(len(self.rows()), 1)

    def test_retention_90_days(self):
        old = {"start": T0 - 91 * DAY, "end": T0 - 91 * DAY + 5, "method": "direct", "user": "jane"}
        new = {"start": T0 - DAY, "end": T0 - DAY + 5, "method": "direct", "user": "jane"}
        (self.dir / "plays.jsonl").write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n")
        log2 = plays.PlayLog(self.dir, now=T0)
        self.assertEqual(len(log2.recent(T0, window=100 * DAY)), 1)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual((self.dir / "plays.jsonl").stat().st_mode & 0o777, 0o600)

    def test_recent_window(self):
        self.log.update([sess()], T0)
        self.assertEqual(len(self.log.recent(T0 + DAY)), 1)
        self.assertEqual(len(self.log.recent(T0 + DAY + 1)), 0)

    def test_broken_file_lines_ignored(self):
        (self.dir / "plays.jsonl").write_text("nonsense\n")
        plays.PlayLog(self.dir, now=T0)


class ReportTest(unittest.TestCase):
    def test_conversions_first_and_counts(self):
        rows = [
            {"start": T0, "end": T0 + 60, "user": "jane", "client": "Web", "device": "Pc", "title": "A (2020)",
             "method": "direct", "reasons": [], "hdr_to_sdr": "no"},
            {"start": T0 + 100, "end": T0 + 700, "user": "bob", "client": "Jellyfin Web", "device": "Safari iPhone",
             "title": "The Show S03E01", "method": "video", "reasons": ["ContainerNotSupported", "VideoCodecTagNotSupported"],
             "hdr_to_sdr": "yes"},
            {"start": T0 + 200, "end": T0 + 260, "user": "jane", "client": "Web", "device": "Pc", "title": "B (2021)",
             "method": "remux", "reasons": ["ContainerNotSupported"], "hdr_to_sdr": "no"},
        ]
        out = plays.report(rows)
        lines = out.splitlines()
        self.assertIn("The Show S03E01", lines[1])
        self.assertIn("video", lines[1])
        self.assertIn("yes", lines[1])
        self.assertIn("ContainerNotSupported,VideoCodecTagNotSupported", lines[1])
        self.assertIn("B (2021)", lines[2])
        self.assertIn("A (2020)", lines[3])
        self.assertIn("3 playbacks: direct 1, remux 1, audio 0, video 1", out)

    def test_empty(self):
        self.assertIn("0 playbacks", plays.report([]))


if __name__ == "__main__":
    unittest.main()
