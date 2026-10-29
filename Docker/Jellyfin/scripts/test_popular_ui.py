import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("popular_ui", pathlib.Path(__file__).with_name("popular-ui.py"))
pu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pu)


class MainTest(unittest.TestCase):
    def test_upserts_the_ui_scripts_for_signed_in_users(self):
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
        self.assertEqual(pu.main([], api=api, sources={"Currently popular UI": "ui()", "Title details": "details()"}, out=out), 0)
        self.assertEqual(config["CustomJavaScripts"][1:], [{"Name": "Currently popular UI", "Script": "ui()", "Enabled": True, "RequiresAuthentication": True},
                                                          {"Name": "Title details", "Script": "details()", "Enabled": True, "RequiresAuthentication": True}])
        self.assertIn("upserted Currently popular UI", out.getvalue())
        self.assertIn("upserted Title details", out.getvalue())

    def test_sources_are_the_repo_scripts(self):
        self.assertEqual({k: v.name for k, v in pu.SOURCES.items()}, {"Currently popular UI": "popular.js", "Title details": "details.js"})
        self.assertTrue(all(v.is_file() for v in pu.SOURCES.values()))


if __name__ == "__main__":
    unittest.main()
