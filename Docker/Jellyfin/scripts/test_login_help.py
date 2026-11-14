import importlib.util
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("login_help", pathlib.Path(__file__).with_name("login-help.py"))
lh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lh)


class LoginHelpTest(unittest.TestCase):
    def test_text_links_the_public_guide_in_a_new_tab(self):
        self.assertIn('href="https://wiki.holy-grail.ch/getting-started/"', lh.TEXT)
        self.assertIn('target="_blank"', lh.TEXT)
        self.assertIn("noopener", lh.TEXT)

    def test_sets_the_disclaimer_and_keeps_other_branding(self):
        self.assertEqual(lh.updated({"SplashscreenEnabled": False}), {"SplashscreenEnabled": False, "LoginDisclaimer": lh.TEXT})

    def test_nothing_to_do_when_already_set(self):
        self.assertIsNone(lh.updated({"LoginDisclaimer": lh.TEXT}))


if __name__ == "__main__":
    unittest.main()
