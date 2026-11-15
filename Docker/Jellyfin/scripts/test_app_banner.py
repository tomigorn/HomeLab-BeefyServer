import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("app_banner", pathlib.Path(__file__).with_name("app-banner.py"))
ab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab)

PLUGIN_ID = "f5a34f7b2e8a4e6aa7223a216a81b374"


class FakeApi:
    def __init__(self):
        self.config = {"CustomJavaScripts": [{"Name": "Rate in Tracker", "Script": "x()", "Enabled": True, "RequiresAuthentication": True}]}

    def __call__(self, method, path, body=None):
        if path == "/Plugins":
            return [{"Name": "JavaScript Injector", "Id": PLUGIN_ID}]
        if method == "GET":
            return self.config
        self.config = body


class AppBannerTest(unittest.TestCase):
    def test_upserts_banner_script(self):
        api = FakeApi()
        self.assertEqual(ab.main([], api=api, out=io.StringIO()), 0)
        names = [s["Name"] for s in api.config["CustomJavaScripts"]]
        self.assertEqual(names, ["Rate in Tracker", "Moonfin app banner", "Hide Moonfin header button"])
        entry = api.config["CustomJavaScripts"][1]
        self.assertEqual(entry["Script"], (pathlib.Path(__file__).resolve().parent.parent / "app-banner" / "app-banner.js").read_text())
        self.assertTrue(entry["Enabled"])

    def test_rerun_keeps_one_entry(self):
        api = FakeApi()
        ab.main([], api=api, out=io.StringIO())
        ab.main([], api=api, out=io.StringIO())
        self.assertEqual(sum(s["Name"] == "Moonfin app banner" for s in api.config["CustomJavaScripts"]), 1)


    def test_hides_moonfin_header_button_everywhere(self):
        api = FakeApi()
        ab.main([], api=api, out=io.StringIO())
        entry = api.config["CustomJavaScripts"][2]
        self.assertIn(".headerMoonfinButton", entry["Script"])
        self.assertIn("display:none", entry["Script"].replace(" ", ""))
        self.assertNotIn("userAgent", entry["Script"], "every browser, desktop too")

    def test_header_button_rule_is_public_so_it_applies_before_first_paint(self):
        api = FakeApi()
        ab.main([], api=api, out=io.StringIO())
        by_name = {s["Name"]: s for s in api.config["CustomJavaScripts"]}
        self.assertFalse(by_name["Hide Moonfin header button"]["RequiresAuthentication"])
        self.assertTrue(by_name["Moonfin app banner"]["RequiresAuthentication"])


if __name__ == "__main__":
    unittest.main()
