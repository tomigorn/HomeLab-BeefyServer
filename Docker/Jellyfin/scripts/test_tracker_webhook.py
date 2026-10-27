import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("tracker_webhook", pathlib.Path(__file__).with_name("tracker-webhook.py"))
tw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tw)

TOKEN = "tok_x"
URL = "http://192.168.1.102:8012/webhook/jellyfin/tok_x"
URL2 = "http://192.168.1.102:8012/webhook/jellyfin/tok_y"
EVENTS = ["Play", "Stop", "MarkPlayed", "MarkUnplayed"]


def without_id(hook):
    return {k: v for k, v in hook.items() if k != "Id"}


class UrlTest(unittest.TestCase):
    def test_url_from_token(self):
        self.assertEqual(tw.webhook_url(TOKEN), URL)

    def test_token_must_be_url_safe(self):
        for bad in ["", "a/b", "a?b", "a b", "a#b"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tw.webhook_url(bad)


class UpsertTest(unittest.TestCase):
    def test_hook_bound_to_user(self):
        hooks = tw.upsert_hook({"Hooks": []}, "anna", "a" * 32, URL)["Hooks"]
        self.assertEqual(len(hooks), 1)
        self.assertTrue(hooks[0]["Id"])
        self.assertEqual(without_id(hooks[0]), {"Name": "Tracker anna", "Url": URL, "UserId": "a" * 32, "Format": "Default", "Events": EVENTS})

    def test_add_is_idempotent_per_user(self):
        first = tw.upsert_hook({"Hooks": []}, "anna", "a" * 32, URL)
        hook_id = first["Hooks"][0]["Id"]
        hooks = tw.upsert_hook(first, "anna", "a" * 32, URL2)["Hooks"]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["Url"], URL2)
        self.assertEqual(hooks[0]["Id"], hook_id)

    def test_old_hook_of_the_user_replaced(self):
        old = {"Id": "9", "Name": "Tracker anna", "Url": "https://tracker.holy-grail.ch/_i/int_x", "UserId": "a" * 32, "Format": "Default", "Events": ["Play", "Pause", "Resume", "Stop", "Progress", "MarkPlayed"]}
        hooks = tw.upsert_hook({"Hooks": [old]}, "anna", "a" * 32, URL)["Hooks"]
        self.assertEqual(hooks, [{"Id": "9", "Name": "Tracker anna", "Url": URL, "UserId": "a" * 32, "Format": "Default", "Events": EVENTS}])

    def test_other_hooks_untouched(self):
        other = {"Id": "1", "Name": "Other", "Url": "http://x/", "UserId": "", "Format": "Get", "Events": ["ItemAdded"]}
        bob = {"Id": "2", "Name": "Tracker bob", "Url": URL2, "UserId": "b" * 32, "Format": "Default", "Events": EVENTS}
        config = {"Hooks": [dict(other), dict(bob)]}
        config = tw.upsert_hook(config, "anna", "a" * 32, URL)
        self.assertIn(other, config["Hooks"])
        self.assertIn(bob, config["Hooks"])
        config = tw.remove_hook(config, "anna")
        self.assertEqual(config["Hooks"], [other, bob])

    def test_remove_hook(self):
        config = tw.upsert_hook({"Hooks": []}, "anna", "a" * 32, URL)
        config = tw.upsert_hook(config, "bob", "b" * 32, URL2)
        config = tw.remove_hook(config, "anna")
        self.assertEqual([h["Name"] for h in config["Hooks"]], ["Tracker bob"])
        self.assertEqual(tw.remove_hook(config, "anna"), config)

    def test_url_must_be_floppy_webhook_on_beefy(self):
        for bad in ["https://tracker.holy-grail.ch/webhook/jellyfin/tok_x", "http://192.168.1.2:8012/webhook/jellyfin/tok_x", "http://192.168.1.102:8096/webhook/jellyfin/tok_x", "http://192.168.1.102:8012/webhook/plex/tok_x", "http://192.168.1.102:8012/webhook/jellyfin/", "http://192.168.1.102:8012/webhook/jellyfin/a/b", ""]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tw.upsert_hook({"Hooks": []}, "anna", "a" * 32, bad)


def fake_api(posted, config=None):
    def api(method, path, body=None):
        if method == "POST":
            posted.append((path, body))
            return None
        if path == "/Plugins":
            return [{"Name": "Other", "Id": "p0"}, {"Name": "Webhooks", "Id": "p1"}]
        return config if config is not None else {"Hooks": [], "Extra": 1}
    return api


class CliTest(unittest.TestCase):
    def test_unknown_jellyfin_user_refused(self):
        posted = []

        def user_id(name):
            raise KeyError(name)

        code = tw.main(["add", "ghost"], out=io.StringIO(), api=fake_api(posted), user_id=user_id, floppy=lambda u: TOKEN)
        self.assertNotEqual(code, 0)
        self.assertFalse(posted)

    def test_unknown_floppy_user_refused(self):
        posted = []

        def floppy(name):
            raise KeyError(name)

        code = tw.main(["add", "anna"], out=io.StringIO(), api=fake_api(posted), user_id=lambda n: "a" * 32, floppy=floppy)
        self.assertNotEqual(code, 0)
        self.assertFalse(posted)

    def test_add_uses_floppy_token_of_named_user(self):
        posted, asked = [], []

        def floppy(name):
            asked.append(name)
            return TOKEN

        out = io.StringIO()
        code = tw.main(["add", "anna", "anna2"], out=out, api=fake_api(posted), user_id=lambda n: "a" * 32, floppy=floppy)
        self.assertEqual(code, 0)
        self.assertEqual(asked, ["anna2"])
        self.assertEqual(posted[0][0], "/Plugins/p1/Configuration")
        self.assertEqual(posted[0][1]["Extra"], 1)
        self.assertEqual(posted[0][1]["Hooks"][0]["Url"], URL)
        self.assertEqual(posted[0][1]["Hooks"][0]["UserId"], "a" * 32)
        self.assertEqual(out.getvalue().strip(), "added Tracker anna -> Floppy anna2")
        self.assertNotIn(TOKEN, out.getvalue())

    def test_floppy_user_defaults_to_jellyfin_user(self):
        asked = []
        tw.main(["add", "anna"], out=io.StringIO(), api=fake_api([]), user_id=lambda n: "a" * 32, floppy=lambda u: asked.append(u) or TOKEN)
        self.assertEqual(asked, ["anna"])

    def test_list_masks_urls(self):
        config = tw.upsert_hook({"Hooks": []}, "anna", "a" * 32, URL)
        out = io.StringIO()
        self.assertEqual(tw.main(["list"], out=out, api=fake_api([], config), user_id=None, floppy=None), 0)
        self.assertIn("Tracker anna", out.getvalue())
        self.assertIn("…/webhook/jellyfin/****", out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue())

    def test_token_parsed_from_marker_line(self):
        self.assertEqual(tw.parse_token("158 objects imported automatically\nFLOPPY_TOKEN=abc_-1\n"), "abc_-1")
        with self.assertRaises(KeyError):
            tw.parse_token("158 objects imported\nFLOPPY_TOKEN=\n")


if __name__ == "__main__":
    unittest.main()
