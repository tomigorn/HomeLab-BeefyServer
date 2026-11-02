import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tracker as tr  # noqa: E402

TICKS = 10_000_000
T0 = datetime(2026, 11, 2, 20, 0, 0, tzinfo=timezone.utc)


def session(pos_s, now, paused=False, transcode=None, sid="s1", checkin=None, item=True):
    s = {
        "Id": sid, "UserName": "u", "Client": "Jellyfin Web", "DeviceName": "Firefox",
        "LastPlaybackCheckIn": (checkin or (T0 + timedelta(seconds=now))).isoformat(),
        "PlayState": {"PositionTicks": int(pos_s * TICKS), "IsPaused": paused, "PlayMethod": "Transcode" if transcode else "DirectPlay"},
    }
    if item:
        s["NowPlayingItem"] = {
            "Name": "Pilot", "SeriesName": "Show", "RunTimeTicks": 3600 * TICKS,
            "MediaStreams": [{"Type": "Audio"}, {"Type": "Video", "RealFrameRate": 24.0}],
        }
    if transcode:
        s["TranscodingInfo"] = transcode
    return s


def tcode(fps, done_pct):
    return {"Framerate": fps, "CompletionPercentage": done_pct, "TranscodeReasons": ["VideoCodecNotSupported"],
            "HardwareAccelerationType": "vaapi", "VideoCodec": "h264", "Bitrate": 8000000, "IsVideoDirect": False}


def run(tracker, steps):
    out = []
    for now, sessions in steps:
        out += tracker.update(sessions, now)
    return out


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.t = tr.Tracker(lambda: 0)

    def test_smooth_playback_no_incident(self):
        inc = run(self.t, [(n, [session(n, n)]) for n in range(0, 61, 5)])
        self.assertEqual(inc, [])

    def test_lag_between_checkins_is_stall(self):
        inc = run(self.t, [(0, [session(0, 0)]), (10, [session(4, 10)])])
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["kind"], "stall")
        self.assertAlmostEqual(inc[0]["seconds"], 6)
        for k in ("session_id", "user", "client", "device", "item", "play_method", "transcode"):
            self.assertIn(k, inc[0])
        self.assertEqual(inc[0]["item"], "Show - Pilot")

    def test_seek_forward_and_back_ignored(self):
        inc = run(self.t, [(0, [session(100, 0)]), (5, [session(500, 5)]), (10, [session(20, 10)]), (15, [session(25, 15)])])
        self.assertEqual(inc, [])

    def test_pause_not_stall_and_not_silent(self):
        steps = [(0, [session(10, 0)]), (5, [session(15, 5)])]
        steps += [(n, [session(15, n, paused=True, checkin=T0)]) for n in range(10, 200, 5)]
        steps += [(200, [session(15, 200)]), (205, [session(20, 205)]), (210, [session(25, 210)])]
        self.assertEqual(run(self.t, steps), [])

    def test_silent_after_45s(self):
        frozen = T0
        steps = [(0, [session(10, 0, checkin=frozen)])]
        steps += [(n, [session(10, n, checkin=frozen)]) for n in range(5, 41, 5)]
        self.assertEqual(run(self.t, steps), [])
        inc = self.t.update([session(10, 45, checkin=frozen)], 45)
        self.assertEqual([i["kind"] for i in inc], ["silent"])
        self.assertAlmostEqual(inc[0]["seconds"], 45)
        self.assertEqual(self.t.update([session(10, 50, checkin=frozen)], 50), [])

    def test_slow_transcode_needs_two_polls_and_low_buffer(self):
        slow = lambda n: session(n, n, transcode=tcode(15.0, (n + 10) / 36))
        self.assertEqual(self.t.update([slow(0)], 0), [])
        inc = self.t.update([slow(5)], 5)
        self.assertEqual([i["kind"] for i in inc], ["slow_transcode"])
        self.assertAlmostEqual(inc[0]["transcode"]["buffer_ahead"], 10, delta=0.01)
        self.assertEqual(self.t.update([slow(10)], 10), [])

    def test_slow_transcode_single_poll_not_enough(self):
        self.assertEqual(self.t.update([session(0, 0, transcode=tcode(15.0, 10 / 36))], 0), [])
        self.assertEqual(self.t.update([session(5, 5, transcode=tcode(24.0, 15 / 36))], 5), [])
        self.assertEqual(self.t.update([session(10, 10, transcode=tcode(15.0, 20 / 36))], 10), [])

    def test_throttled_transcode_far_ahead_not_slow(self):
        far = lambda n: session(n, n, transcode=tcode(5.0, (n + 120) / 36))
        inc = run(self.t, [(n, [far(n)]) for n in range(0, 31, 5)])
        self.assertEqual(inc, [])

    def test_episode_ends_2min_after_last_incident(self):
        run(self.t, [(0, [session(0, 0)]), (10, [session(4, 10)])])
        self.assertTrue(self.t.episode_active(10))
        self.assertTrue(self.t.episode_active(129))
        self.assertFalse(self.t.episode_active(131))

    def test_episode_needs_playing_session(self):
        run(self.t, [(0, [session(0, 0)]), (10, [session(4, 10)])])
        self.t.update([], 15)
        self.assertFalse(self.t.episode_active(20))

    def test_session_end_sets_last_playing_end(self):
        self.assertIsNone(self.t.last_playing_end())
        self.t.update([session(0, 0)], 0)
        self.assertEqual(self.t.playing(), 1)
        self.assertEqual(self.t.active_by_method(), {"DirectPlay": 1})
        self.assertIsNone(self.t.last_playing_end())
        self.t.update([session(0, 5, item=False)], 5)
        self.assertEqual(self.t.playing(), 0)
        self.assertEqual(self.t.last_playing_end(), 5)
        self.assertEqual(self.t.active_by_method(), {})


if __name__ == "__main__":
    unittest.main()
