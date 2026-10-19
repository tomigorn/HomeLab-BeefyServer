import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("tracker_webhook", pathlib.Path(__file__).with_name("tracker-webhook.py"))
tw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tw)

URL = "https://tracker.holy-grail.ch/_i/int_x"
URL2 = "https://tracker.holy-grail.ch/_i/int_y"
EVENTS = ["Play", "Pause", "Resume", "Stop", "Progress", "MarkPlayed"]


def without_id(hook):
    return {k: v for k, v in hook.items() if k != "Id"}


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

    def test_url_must_be_tracker_integration(self):
        for bad in ["http://tracker.holy-grail.ch/_i/int_x", "https://evil.example.com/_i/int_x", "https://tracker.holy-grail.ch/media/x", "https://tracker.holy-grail.ch/_i/", ""]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tw.upsert_hook({"Hooks": []}, "anna", "a" * 32, bad)


class CliTest(unittest.TestCase):
    def test_unknown_user_refused(self):
        calls = []

        def api(method, path, body=None):
            calls.append((method, path))
            if path == "/Plugins":
                return [{"Name": "Webhooks", "Id": "p1"}]
            return {"Hooks": []}

        def user_id(name):
            raise KeyError(name)

        code = tw.main(["add", "ghost"], stdin=io.StringIO(URL + "\n"), out=io.StringIO(), api=api, user_id=user_id)
        self.assertNotEqual(code, 0)
        self.assertFalse([c for c in calls if c[0] == "POST"])

    def test_add_posts_whole_config(self):
        posted = []

        def api(method, path, body=None):
            if method == "POST":
                posted.append((path, body))
                return None
            if path == "/Plugins":
                return [{"Name": "Other", "Id": "p0"}, {"Name": "Webhooks", "Id": "p1"}]
            return {"Hooks": [], "Extra": 1}

        out = io.StringIO()
        code = tw.main(["add", "anna"], stdin=io.StringIO(URL + "\n"), out=out, api=api, user_id=lambda n: "a" * 32)
        self.assertEqual(code, 0)
        self.assertEqual(posted[0][0], "/Plugins/p1/Configuration")
        self.assertEqual(posted[0][1]["Extra"], 1)
        self.assertEqual(posted[0][1]["Hooks"][0]["UserId"], "a" * 32)
        self.assertEqual(out.getvalue().strip(), "added Tracker anna")
        self.assertNotIn("int_x", out.getvalue())

    def test_list_masks_urls(self):
        config = tw.upsert_hook({"Hooks": []}, "anna", "a" * 32, URL)

        def api(method, path, body=None):
            return [{"Name": "Webhooks", "Id": "p1"}] if path == "/Plugins" else config

        out = io.StringIO()
        self.assertEqual(tw.main(["list"], stdin=io.StringIO(""), out=out, api=api, user_id=None), 0)
        self.assertIn("Tracker anna", out.getvalue())
        self.assertIn("…/_i/****", out.getvalue())
        self.assertNotIn("int_x", out.getvalue())


if __name__ == "__main__":
    unittest.main()
