import unittest

import web_home


class Sections(unittest.TestCase):
    def test_only_the_plan_sections_on_in_order(self):
        cfg = {"SectionSettings": [{"SectionId": "HiddenGems", "Enabled": True, "OrderIndex": 6}, {"SectionId": "NextUp", "Enabled": True, "OrderIndex": 2},
                                   {"SectionId": "ContinueWatching", "Enabled": True, "OrderIndex": 1}, {"SectionId": "MyList", "Enabled": False, "OrderIndex": 9}]}
        out = {s["SectionId"]: s for s in web_home.sections(cfg, ["ContinueWatching", "NextUp", "MyList"])["SectionSettings"]}
        self.assertEqual([out[k]["Enabled"] for k in ("ContinueWatching", "NextUp", "MyList", "HiddenGems")], [True, True, True, False])
        self.assertEqual([out[k]["OrderIndex"] for k in ("ContinueWatching", "NextUp", "MyList")], [1, 2, 3])


class Order(unittest.TestCase):
    def test_hss_keeps_only_continue_watching(self):
        self.assertEqual(web_home.ORDER, ["ContinueWatching"])


class Injector(unittest.TestCase):
    def test_prod_home_entries_off_others_untouched(self):
        cfg = {"CustomJavaScripts": [{"Name": "Home layout", "Enabled": True}, {"Name": "Title details", "Enabled": True}, {"Name": "Home upcoming", "Enabled": True}]}
        out = {s["Name"]: s["Enabled"] for s in web_home.injector(cfg)["CustomJavaScripts"]}
        self.assertEqual(out, {"Home layout": False, "Title details": True, "Home upcoming": False})


if __name__ == "__main__":
    unittest.main()
