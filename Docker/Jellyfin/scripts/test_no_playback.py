import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("no_playback", pathlib.Path(__file__).with_name("no-playback.py"))
no_playback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(no_playback)


class Verdict(unittest.TestCase):
    def test_idle_sessions_pass(self):
        self.assertEqual(no_playback.verdict([{"UserName": "a"}, {"NowPlayingItem": None}]), (0, "no playback"))

    def test_playing_sessions_block_and_name_no_users(self):
        rc, msg = no_playback.verdict([{"UserName": "jane", "NowPlayingItem": {"Name": "X"}}, {"NowPlayingItem": {"Name": "Y"}}, {}])
        self.assertEqual((rc, msg), (1, "2 playing in Jellyfin"))

    def test_unreadable_api_blocks(self):
        self.assertEqual(no_playback.verdict(None), (1, "Jellyfin sessions unreadable"))


if __name__ == "__main__":
    unittest.main()
