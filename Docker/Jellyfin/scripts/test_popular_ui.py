import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("popular_ui", pathlib.Path(__file__).with_name("popular-ui.py"))
pu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pu)


class MainTest(unittest.TestCase):
    def test_upserts_the_ui_script_for_signed_in_users(self):
        config = {"CustomJavaScripts": [{"Name": "Rate in Tracker", "Script": "x()", "Enabled": True, "RequiresAuthentication": True}]}

        def api(method, path, body=None):
            if path == "/Plugins":
                return [{"Name": "JavaScript Injector", "Id": "p1"}]
            if method == "POST":
                config.clear()
                config.update(body)
                return None
            return config

        out = io.StringIO()
        self.assertEqual(pu.main([], api=api, source="ui()", out=out), 0)
        self.assertEqual(config["CustomJavaScripts"][-1], {"Name": "Currently popular UI", "Script": "ui()", "Enabled": True, "RequiresAuthentication": True})
        self.assertIn("upserted Currently popular UI", out.getvalue())

    def test_source_is_the_repo_script(self):
        self.assertEqual(pu.SOURCE.name, "popular.js")
        self.assertTrue(pu.SOURCE.is_file())


if __name__ == "__main__":
    unittest.main()
