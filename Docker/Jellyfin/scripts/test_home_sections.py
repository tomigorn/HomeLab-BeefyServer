import importlib.util
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("home_sections", pathlib.Path(__file__).with_name("home-sections.py"))
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)

TYPES = [
    {"Section": s, "ViewMode": "Portrait" if s == "HiddenGems" else "Landscape"}
    for s in ["MyMedia", "ContinueWatching", "NextUp", "RecentlyAddedMovies", "RecentlyAddedShows", "WatchAgain", "BecauseYouWatched",
              "HiddenGems", "FinishTheseCollections", "UpcomingShows", "UpcomingMovies", "DiscoverMovies", "DiscoverTV", "Discover", "MyJellyseerrRequests", "Genre",
              "MyList", "Studio", "Starring", "DirectedBy", "CollectionsSection", "Decade"]
]
LIBS = {"movies": "m1", "tvshows": "t1"}
ARR = {"Sonarr": ("http://192.168.1.102:8989", "skey"), "Radarr": ("http://192.168.1.102:7878", "rkey")}


def fresh():
    return {"Enabled": False, "AllowUserOverride": True, "Sonarr": {"ApiKey": "", "Url": ""}, "Radarr": {"ApiKey": "", "Url": ""}, "SectionSettings": []}


def by_id(cfg):
    return {s["SectionId"]: s for s in cfg["SectionSettings"]}


class Apply(unittest.TestCase):
    def test_enables_the_plugin_for_everyone(self):
        cfg = hs.apply(fresh(), TYPES, LIBS, ARR)
        self.assertTrue(cfg["Enabled"])
        self.assertTrue(cfg["AllowUserOverride"])

    def test_wanted_sections_are_enabled_in_the_owner_order(self):
        cfg = hs.apply(fresh(), TYPES, LIBS, ARR)
        enabled = sorted((s for s in cfg["SectionSettings"] if s["Enabled"]), key=lambda s: s["OrderIndex"])
        self.assertEqual([s["SectionId"] for s in enabled], hs.ORDER)
        self.assertEqual(len({s["OrderIndex"] for s in enabled}), len(hs.ORDER))

    def test_jellyseerr_and_other_sections_stay_disabled(self):
        sections = by_id(hs.apply(fresh(), TYPES, LIBS, ARR))
        for sid in ["DiscoverMovies", "DiscoverTV", "Discover", "MyJellyseerrRequests", "Genre", "Decade"]:
            self.assertFalse(sections[sid]["Enabled"], sid)
            self.assertGreater(sections[sid]["OrderIndex"], len(hs.ORDER))

    def test_owner_order(self):
        self.assertEqual(hs.ORDER, ["MyMedia", "ContinueWatching", "NextUp", "RecentlyAddedMovies", "RecentlyAddedShows", "WatchAgain", "BecauseYouWatched",
                                    "HiddenGems", "FinishTheseCollections", "MyList", "CollectionsSection", "Studio", "Starring", "DirectedBy",
                                    "UpcomingShows", "UpcomingMovies"])

    def test_settings_are_stored_in_display_order(self):
        cfg = hs.apply(fresh(), TYPES, LIBS, ARR)
        ids = [s["SectionId"] for s in cfg["SectionSettings"]]
        self.assertEqual(ids[:len(hs.ORDER)], hs.ORDER)

    def test_because_you_watched_shows_one_or_two_rows(self):
        s = by_id(hs.apply(fresh(), TYPES, LIBS, ARR))["BecauseYouWatched"]
        self.assertEqual((s["LowerLimit"], s["UpperLimit"]), (1, 3))

    def test_new_section_takes_the_view_mode_of_its_type(self):
        self.assertEqual(by_id(hs.apply(fresh(), TYPES, LIBS, ARR))["HiddenGems"]["ViewMode"], "Portrait")

    def test_existing_view_mode_is_kept(self):
        cfg = fresh()
        cfg["SectionSettings"] = [{"SectionId": "HiddenGems", "ViewMode": "Small", "Enabled": False, "PluginConfigurations": []}]
        self.assertEqual(by_id(hs.apply(cfg, TYPES, LIBS, ARR))["HiddenGems"]["ViewMode"], "Small")

    def test_arr_and_library_settings(self):
        cfg = hs.apply(fresh(), TYPES, LIBS, ARR)
        self.assertEqual((cfg["Sonarr"]["Url"], cfg["Sonarr"]["ApiKey"]), ARR["Sonarr"])
        self.assertEqual((cfg["Radarr"]["Url"], cfg["Radarr"]["ApiKey"]), ARR["Radarr"])
        self.assertEqual((cfg["DefaultMoviesLibraryId"], cfg["DefaultTVShowsLibraryId"]), ("m1", "t1"))

    def test_upcoming_windows(self):
        cfg = hs.apply(fresh(), TYPES, LIBS, ARR)
        self.assertEqual((cfg["Sonarr"]["UpcomingTimeframeValue"], cfg["Sonarr"]["UpcomingTimeframeUnit"]), (1, "Months"))
        self.assertEqual((cfg["Radarr"]["UpcomingTimeframeValue"], cfg["Radarr"]["UpcomingTimeframeUnit"]), (3, "Months"))

    def test_upcoming_dates_are_swiss(self):
        cfg = hs.apply(fresh(), TYPES, LIBS, ARR)
        self.assertEqual((cfg["DateFormat"], cfg["DateDelimiter"]), ("DD/MM/YYYY", "."))

    def test_summary_shows_the_upcoming_windows(self):
        text = hs.summary(hs.apply(fresh(), TYPES, LIBS, ARR))
        self.assertIn("Sonarr: http://192.168.1.102:8989 key set, next 1 Months", text)

    def test_second_run_changes_nothing(self):
        once = hs.apply(fresh(), TYPES, LIBS, ARR)
        self.assertEqual(hs.apply(once, TYPES, LIBS, ARR), once)

    def test_input_is_not_mutated(self):
        cfg = fresh()
        hs.apply(cfg, TYPES, LIBS, ARR)
        self.assertEqual(cfg, fresh())


class Summary(unittest.TestCase):
    def test_summary_never_contains_api_keys(self):
        text = hs.summary(hs.apply(fresh(), TYPES, LIBS, ARR))
        self.assertNotIn("skey", text)
        self.assertNotIn("rkey", text)
        self.assertIn("1. MyMedia", text)


class ArrKey(unittest.TestCase):
    def test_reads_the_api_key_from_config_xml(self):
        self.assertEqual(hs.arr_key("<Config><Port>8989</Port><ApiKey>abc123</ApiKey></Config>"), "abc123")


if __name__ == "__main__":
    unittest.main()
