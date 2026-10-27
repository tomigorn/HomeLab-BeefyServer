import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("home_placeholders", pathlib.Path(__file__).with_name("home-placeholders.py"))
hp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hp)


class MainTest(unittest.TestCase):
    def test_upserts_the_script_next_to_others(self):
        calls = []
        other = {"Name": "Rate in Tracker", "Script": "x()", "Enabled": True, "RequiresAuthentication": True}

        def api(method, path, body=None):
            calls.append((method, path, body))
            return [{"Name": "JavaScript Injector", "Id": "p1"}] if path == "/Plugins" else {"CustomJavaScripts": [other]}

        out = io.StringIO()
        self.assertEqual(hp.main([], api=api, source="a()", out=out), 0)
        method, path, body = calls[-1]
        self.assertEqual((method, path), ("POST", "/Plugins/p1/Configuration"))
        self.assertEqual(body["CustomJavaScripts"], [other, {"Name": "Empty home sections", "Script": "a()", "Enabled": True, "RequiresAuthentication": True}])
        self.assertIn("upserted Empty home sections", out.getvalue())

    def test_source_is_the_repo_script(self):
        self.assertTrue(hp.SOURCE.is_file())
        self.assertEqual(hp.SOURCE.name, "empty-sections.js")


if __name__ == "__main__":
    unittest.main()
