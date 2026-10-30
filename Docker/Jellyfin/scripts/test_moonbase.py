import contextlib
import importlib.util
import io
import pathlib
import unittest
from unittest import mock

_spec = importlib.util.spec_from_file_location("moonbase", pathlib.Path(__file__).with_name("moonbase.py"))
mb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mb)

LISTS = [
    {"Id": "t1", "Name": "Top 10 Switzerland · Movies", "Count": 10},
    {"Id": "t2", "Name": "Top 10 Switzerland · Shows", "Count": 10},
    {"Id": "c1", "Name": "Coming soon", "Count": 4},
    {"Id": "n1", "Name": "Netflix · Movies", "Count": 20},
    {"Id": "n2", "Name": "Netflix · Shows", "Count": 0},
    {"Id": "g1", "Name": "Action · Movies", "Count": 5},
]
FOLDERS = [
    {"ItemId": "mov", "Name": "Movies", "CollectionType": "movies"},
    {"ItemId": "tv", "Name": "Shows", "CollectionType": "tvshows"},
    {"ItemId": "box", "Name": "Collections & Trending", "CollectionType": "boxsets"},
    {"ItemId": "tr", "Name": "Trending items", "CollectionType": "movies"},
]
CONFIG = {"SeerrEnabled": False, "RecommendationsProviderEnabled": True, "PushRelayUrl": "https://push.example.com/send", "SeerrWebhookSecret": "s", "DefaultUserSettings": None}


def rows(sections):
    return [s.get("pluginDisplayText") or s["type"] for s in sorted(sections, key=lambda s: s["order"]) if s["enabled"]]


class SectionsTest(unittest.TestCase):
    def test_order_continue_next_up_top10_coming_recent_then_lists(self):
        self.assertEqual(rows(mb.sections(LISTS)), ["resume", "nextup", "Top 10 Switzerland · Movies", "Top 10 Switzerland · Shows", "Coming soon", "latestmedia", "Netflix · Movies", "Action · Movies"])

    def test_empty_lists_left_out(self):
        self.assertNotIn("Netflix · Shows", rows(mb.sections(LISTS)))

    def test_list_rows_are_collection_rows_on_the_public_server(self):
        top = [s for s in mb.sections(LISTS) if s.get("pluginAdditionalData") == "t1"][0]
        self.assertEqual((top["kind"], top["pluginSource"], top["pluginSection"], top["serverId"]), ("pluginDynamic", "collections", "collection", "https://jellyfin.holy-grail.ch"))

    def test_orders_are_consecutive(self):
        self.assertEqual([s["order"] for s in mb.sections(LISTS)], list(range(len(mb.sections(LISTS)))))

    def test_recently_added_follows_lists_when_no_leading_list_exists(self):
        self.assertEqual(rows(mb.sections(LISTS[3:])), ["resume", "nextup", "latestmedia", "Netflix · Movies", "Action · Movies"])


class WantedTest(unittest.TestCase):
    def test_seerr_points_at_the_facade(self):
        new = mb.wanted(CONFIG, LISTS, FOLDERS)
        self.assertEqual((new["SeerrEnabled"], new["SeerrUrl"], new["SeerrDisplayName"], new["PushRelayUrl"], new["PublicServerUrl"]), (True, "http://jellyfin-popular:8099", "Requests", "", None))

    def test_seerr_rows_on_by_default(self):
        d = mb.wanted(CONFIG, LISTS, FOLDERS)["DefaultUserSettings"]
        self.assertEqual((d["displaySeerrRows"], d["seerrEnabled"]), (True, True))

    def test_webhook_secret_untouched_and_never_printed(self):
        posted = []
        cfg = {**CONFIG, "SeerrWebhookSecret": "s3cr3t-value"}

        def fake(method, path, body=None):
            if method == "POST":
                posted.append(body)
            if path.endswith("/Configuration"):
                return cfg
            return []

        for argv in ([], ["--dry-run"]):
            out = io.StringIO()
            with mock.patch.object(mb, "api", fake), mock.patch.object(mb, "lists", lambda: LISTS), contextlib.redirect_stdout(out):
                mb.main(argv)
            self.assertNotIn("s3cr3t-value", out.getvalue())
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["SeerrWebhookSecret"], "s3cr3t-value")

    def test_other_settings_kept(self):
        self.assertEqual(mb.wanted(CONFIG, LISTS, FOLDERS)["SeerrWebhookSecret"], "s")

    def test_defaults_left_navigation_and_real_libraries_in_media_bar(self):
        d = mb.wanted(CONFIG, LISTS, FOLDERS)["DefaultUserSettings"]
        self.assertEqual((d["navbarPosition"], d["mediaBarLibraryIds"], d["displayCollectionsRows"], d["collectionsRowSortBy"]), ("left", ["mov", "tv"], True, "name"))

    def test_wizard_questions_answered_by_defaults(self):
        d = mb.wanted(CONFIG, LISTS, FOLDERS)["DefaultUserSettings"]
        for key in ("navbarPosition", "mediaBarMode", "homeRowsStyle", "detailScreenStyle"):
            self.assertTrue(d.get(key), key)

    def test_continue_watching_and_next_up_are_separate_rows(self):
        self.assertIs(mb.wanted(CONFIG, LISTS, FOLDERS)["DefaultUserSettings"]["mergeContinueWatchingNextUp"], False)

    def test_existing_defaults_kept(self):
        cfg = {**CONFIG, "DefaultUserSettings": {"focusColor": "blue", "navbarPosition": "top"}}
        d = mb.wanted(cfg, LISTS, FOLDERS)["DefaultUserSettings"]
        self.assertEqual((d["focusColor"], d["navbarPosition"]), ("blue", "left"))

    def test_input_not_mutated(self):
        mb.wanted(CONFIG, LISTS, FOLDERS)
        self.assertTrue(CONFIG["RecommendationsProviderEnabled"])

    def test_applied_config_is_unchanged(self):
        new = mb.wanted(CONFIG, LISTS, FOLDERS)
        self.assertEqual(mb.changed(new, mb.wanted(new, LISTS, FOLDERS)), [])

    def test_changed_names_the_keys(self):
        self.assertIn("RecommendationsProviderEnabled", mb.changed(CONFIG, mb.wanted(CONFIG, LISTS, FOLDERS)))


if __name__ == "__main__":
    unittest.main()
