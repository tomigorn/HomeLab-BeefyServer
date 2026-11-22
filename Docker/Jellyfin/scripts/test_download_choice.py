import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("download_choice", pathlib.Path(__file__).with_name("download-choice.py"))
dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc)


class MainTest(unittest.TestCase):
    def test_upserts_the_download_choice_script_signed_in_only(self):
        calls = []

        def api(method, path, body=None):
            calls.append((method, path, body))
            if path == "/Plugins":
                return [{"Name": "JavaScript Injector", "Id": "abc"}]
            return {"CustomJavaScripts": [{"Name": "Report a problem", "Script": "r()", "Enabled": True, "RequiresAuthentication": True}]}

        out = io.StringIO()
        self.assertEqual(dc.main([], api=api, out=out), 0)
        posted = calls[-1][2]["CustomJavaScripts"]
        self.assertEqual([s["Name"] for s in posted], ["Report a problem", "Download choice"])
        self.assertTrue(posted[1]["RequiresAuthentication"])
        self.assertIn("hss-popular/download-choice", posted[1]["Script"])
        self.assertEqual(out.getvalue().strip(), "upserted Download choice")

    def test_rejects_arguments(self):
        self.assertEqual(dc.main(["x"], api=lambda *a: None, out=io.StringIO()), 2)


if __name__ == "__main__":
    unittest.main()
