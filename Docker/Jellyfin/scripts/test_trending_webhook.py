import contextlib
import importlib.util
import io
import pathlib
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location("trending_webhook", pathlib.Path(__file__).with_name("trending-webhook.py"))
tw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tw)

SECRET = "hookSecret_x-1"
URL = "http://jellyfin-popular:8098/hook/hookSecret_x-1"
EVENTS = ["Play", "Progress", "Pause", "Resume", "Stop", "MarkPlayed"]
FLOPPY = {"Id": "f1", "Name": "Tracker anna", "Url": "http://192.168.1.102:8012/webhook/jellyfin/floppytok", "UserId": "a" * 32, "Format": "Default", "Events": ["Play", "Stop", "MarkPlayed", "MarkUnplayed"]}


def without_id(hook):
    return {k: v for k, v in hook.items() if k != "Id"}


class UpsertTest(unittest.TestCase):
    def test_hook_for_every_user(self):
        hooks = tw.upsert_hook({"Hooks": []}, URL)["Hooks"]
        self.assertTrue(hooks[0]["Id"])
        self.assertEqual(without_id(hooks[0]), {"Name": "Trending requests", "Url": URL, "Format": "Default", "Events": EVENTS})

    def test_floppy_hooks_untouched(self):
        config = {"Hooks": [dict(FLOPPY)], "Extra": 1}
        config = tw.upsert_hook(config, URL)
        self.assertEqual(config["Hooks"][0], FLOPPY)
        self.assertEqual(config["Extra"], 1)
        self.assertEqual(tw.remove_hook(config), {"Hooks": [FLOPPY], "Extra": 1})

    def test_existing_hook_replaced_by_name(self):
        old = {"Id": "9", "Name": "Trending requests", "Url": "http://jellyfin-popular:8098/hook/old", "UserId": "b" * 32, "Format": "Get", "Events": ["Play"]}
        hooks = tw.upsert_hook({"Hooks": [FLOPPY, old]}, URL)["Hooks"]
        self.assertEqual(hooks, [FLOPPY, {"Id": "9", "Name": "Trending requests", "Url": URL, "Format": "Default", "Events": EVENTS}])

    def test_url_must_be_the_internal_hook(self):
        for bad in ["https://jellyfin-popular:8098/hook/x", "http://jellyfin-popular:8097/hook/x", "http://192.168.1.102:8098/hook/x", "http://jellyfin-popular:8098/hss-popular/hook/x",
                    "http://jellyfin-popular:8098/hook/", "http://jellyfin-popular:8098/hook/a/b", "http://jellyfin-popular:8098/hook/x?y=1", ""]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tw.upsert_hook({"Hooks": []}, bad)


def fake_api(posted, config):
    def api(method, path, body=None):
        if method == "POST":
            posted.append((path, body))
            return None
        if path == "/Plugins":
            return [{"Name": "Other", "Id": "p0"}, {"Name": "Webhooks", "Id": "p1"}]
        return config
    return api


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.secret = pathlib.Path(self.tmp.name, "hook.secret")
        self.secret.write_text(SECRET + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv, config, posted=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            code = tw.main(argv, out=out, api=fake_api(posted if posted is not None else [], config), secret_file=self.secret)
        return code, out.getvalue() + err.getvalue()

    def test_add_posts_the_hook_and_never_prints_the_secret(self):
        posted = []
        code, text = self.run_cli(["add"], {"Hooks": [dict(FLOPPY)]}, posted)
        self.assertEqual(code, 0)
        self.assertEqual(posted[0][0], "/Plugins/p1/Configuration")
        self.assertEqual(posted[0][1]["Hooks"][0], FLOPPY)
        self.assertEqual(posted[0][1]["Hooks"][1]["Url"], URL)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("floppytok", text)

    def test_check_ok_and_missing(self):
        config = tw.upsert_hook({"Hooks": [dict(FLOPPY)]}, URL)
        code, text = self.run_cli(["check"], config)
        self.assertEqual((code, text.strip()), (0, "ok"))
        for broken in ({"Hooks": [FLOPPY]}, tw.upsert_hook({"Hooks": []}, "http://jellyfin-popular:8098/hook/other"),
                       {"Hooks": [dict(config["Hooks"][1], Events=["Play"])]}, {"Hooks": [dict(config["Hooks"][1], Events=EVENTS[:5])]}, {"Hooks": [dict(config["Hooks"][1], UserId="a" * 32)]}):
            code, text = self.run_cli(["check"], broken)
            self.assertEqual(code, 1, broken)
            self.assertNotIn(SECRET, text)
            self.assertNotIn("floppytok", text)

    def test_list_masks_every_url(self):
        config = tw.upsert_hook({"Hooks": [dict(FLOPPY)]}, URL)
        code, text = self.run_cli(["list"], config)
        self.assertEqual(code, 0)
        self.assertIn("Trending requests", text)
        self.assertIn("…/hook/****", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("floppytok", text)

    def test_remove(self):
        posted = []
        config = tw.upsert_hook({"Hooks": [dict(FLOPPY)]}, URL)
        self.assertEqual(self.run_cli(["remove"], config, posted)[0], 0)
        self.assertEqual(posted[0][1]["Hooks"], [FLOPPY])

    def test_missing_secret_refused(self):
        self.secret.unlink()
        posted = []
        self.assertEqual(self.run_cli(["add"], {"Hooks": []}, posted)[0], 1)
        self.assertEqual(posted, [])

    def test_usage(self):
        self.assertEqual(self.run_cli([], {"Hooks": []})[0], 2)
        self.assertEqual(self.run_cli(["add", "x"], {"Hooks": []})[0], 2)


if __name__ == "__main__":
    unittest.main()
