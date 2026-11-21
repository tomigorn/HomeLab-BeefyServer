import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("report_link", pathlib.Path(__file__).with_name("report-link.py"))
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)


class MainTest(unittest.TestCase):
    def test_upserts_report_script_signed_in_only(self):
        calls = []

        def api(method, path, body=None):
            calls.append((method, path, body))
            if path == "/Plugins":
                return [{"Name": "JavaScript Injector", "Id": "abc"}]
            return {"CustomJavaScripts": [{"Name": "Rate in Tracker", "Script": "t()", "Enabled": True, "RequiresAuthentication": True}]}

        out = io.StringIO()
        self.assertEqual(rl.main([], api=api, out=out), 0)
        posted = calls[-1][2]["CustomJavaScripts"]
        self.assertEqual([s["Name"] for s in posted], ["Rate in Tracker", "Report a problem"])
        self.assertTrue(posted[1]["RequiresAuthentication"])
        self.assertIn("report.holy-grail.ch", posted[1]["Script"])
        self.assertEqual(out.getvalue().strip(), "upserted Report a problem")

    def test_rejects_arguments(self):
        self.assertEqual(rl.main(["x"], api=lambda *a: None, out=io.StringIO()), 2)


if __name__ == "__main__":
    unittest.main()
