import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("home_placeholders", pathlib.Path(__file__).with_name("home-placeholders.py"))
hp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hp)


class MainTest(unittest.TestCase):
    def test_upserts_both_scripts_next_to_others(self):
        other = {"Name": "Rate in Tracker", "Script": "x()", "Enabled": True, "RequiresAuthentication": True}
        config = {"CustomJavaScripts": [other]}
        posts = []

        def api(method, path, body=None):
            if path == "/Plugins":
                return [{"Name": "JavaScript Injector", "Id": "p1"}]
            if method == "POST":
                posts.append(path)
                config.clear()
                config.update(body)
                return None
            return config

        out = io.StringIO()
        self.assertEqual(hp.main([], api=api, sources={"Empty home sections": "a()", "Home layout": "b()"}, out=out), 0)
        self.assertEqual(set(posts), {"/Plugins/p1/Configuration"})
        self.assertEqual(config["CustomJavaScripts"], [other, {"Name": "Empty home sections", "Script": "a()", "Enabled": True, "RequiresAuthentication": True},
                                                       {"Name": "Home layout", "Script": "b()", "Enabled": True, "RequiresAuthentication": True}])
        self.assertIn("upserted Empty home sections", out.getvalue())
        self.assertIn("upserted Home layout", out.getvalue())

    def test_sources_are_the_repo_scripts(self):
        self.assertEqual({n: p.name for n, p in hp.SOURCES.items()}, {"Empty home sections": "empty-sections.js", "Home layout": "home-layout.js"})
        for p in hp.SOURCES.values():
            self.assertTrue(p.is_file(), p)


if __name__ == "__main__":
    unittest.main()
