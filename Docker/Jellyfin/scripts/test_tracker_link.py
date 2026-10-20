import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("tracker_link", pathlib.Path(__file__).with_name("tracker-link.py"))
tl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tl)

NAME = "Rate in Tracker"
OTHER = {"Name": "Other", "Script": "x()", "Enabled": False, "RequiresAuthentication": False}


class UpsertTest(unittest.TestCase):
    def test_adds_entry(self):
        config = tl.upsert_script({"CustomJavaScripts": [], "PluginJavaScripts": []}, NAME, "a()")
        self.assertEqual(config["CustomJavaScripts"], [{"Name": NAME, "Script": "a()", "Enabled": True, "RequiresAuthentication": True}])
        self.assertEqual(config["PluginJavaScripts"], [])

    def test_replaces_entry(self):
        config = tl.upsert_script({"CustomJavaScripts": [dict(OTHER), {"Name": NAME, "Script": "old()", "Enabled": False, "RequiresAuthentication": False}]}, NAME, "new()")
        self.assertEqual(config["CustomJavaScripts"], [OTHER, {"Name": NAME, "Script": "new()", "Enabled": True, "RequiresAuthentication": True}])

    def test_keeps_others(self):
        config = tl.upsert_script({"CustomJavaScripts": [dict(OTHER)]}, NAME, "a()")
        self.assertIn(OTHER, config["CustomJavaScripts"])
        self.assertEqual(len(config["CustomJavaScripts"]), 2)

    def test_idempotent(self):
        once = tl.upsert_script({"CustomJavaScripts": [dict(OTHER)]}, NAME, "a()")
        twice = tl.upsert_script(once, NAME, "a()")
        self.assertEqual(once, twice)

    def test_requires_auth(self):
        entry = tl.upsert_script({}, NAME, "a()")["CustomJavaScripts"][0]
        self.assertTrue(entry["RequiresAuthentication"])

    def test_input_not_mutated(self):
        config = {"CustomJavaScripts": [{"Name": NAME, "Script": "old()", "Enabled": False, "RequiresAuthentication": False}]}
        tl.upsert_script(config, NAME, "new()")
        self.assertEqual(config["CustomJavaScripts"][0]["Script"], "old()")


class MainTest(unittest.TestCase):
    def fake_api(self, plugins, config):
        calls = []

        def api(method, path, body=None):
            calls.append((method, path, body))
            if path == "/Plugins":
                return plugins
            return config

        return api, calls

    def test_posts_script(self):
        api, calls = self.fake_api([{"Name": "JavaScript Injector", "Id": "p1"}], {"CustomJavaScripts": []})
        out = io.StringIO()
        self.assertEqual(tl.main([], api=api, source="a()", out=out), 0)
        method, path, body = calls[-1]
        self.assertEqual((method, path), ("POST", "/Plugins/p1/Configuration"))
        self.assertEqual(body["CustomJavaScripts"][0]["Script"], "a()")
        self.assertIn("upserted Rate in Tracker", out.getvalue())

    def test_plugin_missing(self):
        api, calls = self.fake_api([{"Name": "Webhooks", "Id": "w"}], {})
        self.assertEqual(tl.main([], api=api, source="a()", out=io.StringIO()), 1)
        self.assertFalse(any(c[0] == "POST" for c in calls))


if __name__ == "__main__":
    unittest.main()
