import contextlib
import fcntl
import importlib.util
import io
import json
import os
import pathlib
import tempfile
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
    {"Id": "hg", "Name": "Hidden Gems", "Count": 20},
    {"Id": "hp", "Name": "Harry Potter Collection", "Count": 8},
    {"Id": "lr", "Name": "The Lord of the Rings Collection", "Count": 3},
]
FOLDERS = [
    {"ItemId": "mov", "Name": "Movies", "CollectionType": "movies"},
    {"ItemId": "tv", "Name": "Shows", "CollectionType": "tvshows"},
    {"ItemId": "box", "Name": "Collections & Trending", "CollectionType": "boxsets"},
    {"ItemId": "tr", "Name": "Trending items", "CollectionType": "movies"},
    {"ItemId": "ts", "Name": "Trending shows", "CollectionType": "tvshows"},
]
CONFIG = {"SeerrEnabled": False, "RecommendationsProviderEnabled": True, "PushRelayUrl": "https://push.example.com/send", "SeerrWebhookSecret": "s", "DefaultUserSettings": None}
STUDIOS = "s1,s2,n1"
WEB_ORDER = ["resume", "nextup", "Coming soon", "favoritemovies", "favoriteseries", "latestmedia", "Top 10 Switzerland · Shows", "studios", "rewatch",
             "Top 10 Switzerland · Movies", "sinceyouwatched1", "Hidden Gems", "genres", "Harry Potter Collection", "The Lord of the Rings Collection"]


def rows(sections):
    return [s.get("pluginDisplayText") or s["type"] for s in sorted(sections, key=lambda s: s["order"]) if s["enabled"]]


class SectionsTest(unittest.TestCase):
    def test_web_order(self):
        self.assertEqual(rows(mb.sections(LISTS)), WEB_ORDER)

    def test_service_and_genre_lists_not_on_home(self):
        names = rows(mb.sections(LISTS))
        self.assertNotIn("Netflix · Movies", names)
        self.assertNotIn("Action · Movies", names)

    def test_empty_or_missing_collections_left_out(self):
        lists = [dict(i, Count=0) if i["Id"] == "hg" else i for i in LISTS if i["Id"] != "lr"]
        self.assertEqual(rows(mb.sections(lists)), [r for r in WEB_ORDER if r not in ("Hidden Gems", "The Lord of the Rings Collection")])

    def test_collection_rows_are_on_the_public_server(self):
        top = [s for s in mb.sections(LISTS) if s.get("pluginAdditionalData") == "hp"][0]
        self.assertEqual((top["kind"], top["pluginSource"], top["pluginSection"], top["serverId"], top["type"]), ("pluginDynamic", "collections", "collection", "https://jellyfin.holy-grail.ch", "none"))

    def test_orders_are_consecutive(self):
        self.assertEqual([s["order"] for s in mb.sections(LISTS)], list(range(len(mb.sections(LISTS)))))


class StudioIdsTest(unittest.TestCase):
    SCRIPT = 'window.HssStudios = [{"name": "Pixar", "ids": ["s1"]}, {"name": "A24", "ids": ["s2", "s1"], "min": 2}];\nwindow.HssNetworks = [{"name": "HBO", "ids": ["n1"]}];\n'

    def test_studios_then_networks_without_duplicates(self):
        self.assertEqual(mb.studio_ids({"CustomJavaScripts": [{"Name": "Other", "Script": "x"}, {"Name": "Home networks", "Script": self.SCRIPT}]}), "s1,s2,n1")

    def test_missing_entry_is_none(self):
        self.assertIsNone(mb.studio_ids({"CustomJavaScripts": []}))


OLD = {"navbarPosition": "left", "displaySeerrRows": True, "homeSections": [{"type": "resume", "enabled": True, "order": 0}, {"kind": "pluginDynamic", "pluginSource": "collections", "type": "none", "enabled": True, "order": 1, "pluginAdditionalData": "n1"}]}
NEW = {"navbarPosition": "left", "displaySeerrRows": True, "displayStudiosRows": True, "genresRowItemFilter": "movies", "mediaBarAutoAdvance": True, "homeRowOrder": None,
       "homeSections": [{"type": "resume", "enabled": True, "order": 0}, {"type": "studios", "enabled": True, "order": 1}]}


class AdoptTest(unittest.TestCase):
    def test_old_default_layout_replaced_even_with_client_added_disabled_rows(self):
        stored = OLD["homeSections"] + [{"type": "genres", "enabled": False, "order": 2}]
        self.assertEqual(mb.adopt({"tv": {"homeSections": stored}}, OLD, NEW), {"tv": {"homeSections": NEW["homeSections"]}})

    def test_own_layout_kept(self):
        own = [{"type": "nextup", "enabled": True, "order": 0}]
        self.assertEqual(mb.adopt({"global": {"homeSections": own}}, OLD, NEW), {})

    def test_own_choice_kept(self):
        self.assertEqual(mb.adopt({"tv": {"displaySeerrRows": False, "navbarPosition": "top"}}, OLD, NEW), {})

    def test_client_defaults_of_new_keys_filled(self):
        stored = {"displayStudiosRows": False, "genresRowItemFilter": "both", "mediaBarAutoAdvance": True}
        self.assertEqual(mb.adopt({"mobile": stored}, OLD, NEW), {"mobile": {"displayStudiosRows": True, "genresRowItemFilter": "movies"}})

    def test_client_default_migrated_only_once(self):
        stored = {"displayStudiosRows": False}
        self.assertEqual(mb.adopt({"mobile": stored}, OLD, NEW, done={"mobile:displayStudiosRows"}), {})

    def test_markers_for_every_client_default_key_of_each_profile(self):
        self.assertEqual(mb.client_markers({"tv": {}, "mobile": None}), {f"tv:{k}" for k in mb.CLIENT_DEFAULTS})

    def test_own_value_of_a_new_key_kept(self):
        self.assertEqual(mb.adopt({"mobile": {"genresRowItemFilter": "series"}}, OLD, NEW), {})

    def test_unset_keys_left_to_the_defaults(self):
        self.assertEqual(mb.adopt({"global": {}, "tv": None}, OLD, NEW), {})

    def test_previous_default_value_follows_the_new_one(self):
        self.assertEqual(mb.adopt({"global": {"navbarPosition": "left"}}, {"navbarPosition": "left"}, {"navbarPosition": "top"}), {"global": {"navbarPosition": "top"}})


class WantedTest(unittest.TestCase):
    def test_seerr_points_at_the_facade(self):
        new = mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)
        self.assertEqual((new["SeerrEnabled"], new["SeerrUrl"], new["SeerrDisplayName"], new["PushRelayUrl"], new["PublicServerUrl"]), (True, "http://jellyfin-popular:8099", "Requests", "", None))

    def test_seerr_rows_on_by_default(self):
        d = mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)["DefaultUserSettings"]
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

        tmp = self.enterContext(tempfile.TemporaryDirectory())
        for argv in ([], ["--dry-run"]):
            out = io.StringIO()
            with mock.patch.object(mb, "api", fake), mock.patch.object(mb, "lists", lambda: LISTS), mock.patch.object(mb, "STATE_FILE", pathlib.Path(tmp) / "m.json"), contextlib.redirect_stdout(out):
                mb.main(argv)
            self.assertNotIn("s3cr3t-value", out.getvalue())
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["SeerrWebhookSecret"], "s3cr3t-value")

    def test_media_bar_random_and_auto_advancing(self):
        d = mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)["DefaultUserSettings"]
        self.assertEqual((d["mediaBarSourceType"], d["mediaBarAutoAdvance"]), ("random", True))

    def test_row_toggles_for_the_web_order(self):
        d = mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)["DefaultUserSettings"]
        self.assertEqual((d["displayFavoritesRows"], d["displayStudiosRows"], d["displayGenresRows"], d["displayRewatchRow"], d["displaySinceYouWatchedRows"], d["genresRowItemFilter"]),
                         (True, True, True, True, True, "movies"))

    def test_studios_row_preset_with_web_studios_and_networks(self):
        self.assertEqual(mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)["DefaultUserSettings"]["studiosRowSelectedIds"], "s1,s2,n1")

    def test_studios_preset_kept_when_the_web_entry_is_missing(self):
        cfg = {**CONFIG, "DefaultUserSettings": {"studiosRowSelectedIds": "old"}}
        self.assertEqual(mb.wanted(cfg, LISTS, FOLDERS, None)["DefaultUserSettings"]["studiosRowSelectedIds"], "old")

    def test_other_settings_kept(self):
        self.assertEqual(mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)["SeerrWebhookSecret"], "s")

    def test_defaults_left_navigation_and_real_libraries_in_media_bar(self):
        d = mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)["DefaultUserSettings"]
        self.assertEqual((d["navbarPosition"], d["mediaBarLibraryIds"], d["displayCollectionsRows"], d["collectionsRowSortBy"]), ("left", ["mov", "tv"], True, "name"))

    def test_wizard_questions_answered_by_defaults(self):
        d = mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)["DefaultUserSettings"]
        for key in ("navbarPosition", "mediaBarMode", "homeRowsStyle", "detailScreenStyle"):
            self.assertTrue(d.get(key), key)

    def test_continue_watching_and_next_up_are_separate_rows(self):
        self.assertIs(mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)["DefaultUserSettings"]["mergeContinueWatchingNextUp"], False)

    def test_existing_defaults_kept(self):
        cfg = {**CONFIG, "DefaultUserSettings": {"focusColor": "blue", "navbarPosition": "top"}}
        d = mb.wanted(cfg, LISTS, FOLDERS, STUDIOS)["DefaultUserSettings"]
        self.assertEqual((d["focusColor"], d["navbarPosition"]), ("blue", "left"))

    def test_input_not_mutated(self):
        mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)
        self.assertTrue(CONFIG["RecommendationsProviderEnabled"])

    def test_applied_config_is_unchanged(self):
        new = mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)
        self.assertEqual(mb.changed(new, mb.wanted(new, LISTS, FOLDERS, STUDIOS)), [])

    def test_changed_names_the_keys(self):
        self.assertIn("RecommendationsProviderEnabled", mb.changed(CONFIG, mb.wanted(CONFIG, LISTS, FOLDERS, STUDIOS)))



class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "moonbase.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, argv, user_settings, old=None, fail=(), probe=None):
        calls = []
        cfg = {**CONFIG, "EnableSettingsSync": True, "DefaultUserSettings": dict(old or OLD)}

        def fake(method, path, body=None):
            calls.append((method, path, body))
            if probe and method == "POST":
                probe()
            if method == "POST" and path.rsplit("/", 1)[1] in fail:
                raise mb.urllib.error.HTTPError(path, 500, "boom", {}, None)
            if path == f"/Plugins/{mb.PLUGIN_ID}/Configuration":
                return cfg
            if path == "/Users":
                return [{"Id": "u1"}, {"Id": "u2"}]
            if path.startswith("/Moonfin/Settings/"):
                uid = path.rsplit("/", 1)[1]
                if method == "GET":
                    if uid not in user_settings:
                        raise mb.urllib.error.HTTPError(path, 404, "no settings", {}, None)
                    return user_settings[uid]
                return {"Success": True}
            return []

        with mock.patch.object(mb, "api", fake), mock.patch.object(mb, "lists", lambda: LISTS), mock.patch.object(mb, "STATE_FILE", self.state), contextlib.redirect_stdout(io.StringIO()) as out:
            code = mb.main(argv)
        return code, [c for c in calls if c[0] == "POST"], out.getvalue()

    def applied(self):
        return mb.wanted({**CONFIG, "DefaultUserSettings": dict(OLD)}, LISTS, FOLDERS, None)["DefaultUserSettings"]

    def user_posts(self, posts):
        return [p[1] for p in posts if p[1].startswith("/Moonfin/Settings/")]

    def test_first_run_marks_that_markers_exist(self):
        self.run_main([], {"u1": {"tv": {"displayGenresRows": False}}})
        self.assertTrue(self.state.exists())
        self.assertTrue(self.state.with_suffix(".written").exists())

    def test_existing_state_gets_its_written_mark_on_an_unchanged_run(self):
        self.state.write_text(json.dumps({"migrated": {"u1": sorted(mb.client_markers({"tv": {}}))}, "pending": {}}))
        before = self.state.read_text()
        self.run_main([], {"u1": {"tv": {"displayGenresRows": False}}}, old=self.applied())
        self.assertEqual(self.state.read_text(), before)
        self.assertTrue(self.state.with_suffix(".written").exists())

    def test_lost_state_file_after_markers_were_written_adopts_no_app_default(self):
        self.run_main([], {"u1": {"tv": {"displayGenresRows": False}}})
        self.state.unlink()
        code, posts, out = self.run_main([], {"u1": {"tv": {"displayGenresRows": False}}}, old=self.applied())
        self.assertEqual(self.user_posts(posts), [])
        self.assertIn("state", out)
        self.assertEqual(code, 0)
        self.assertIn("tv:displayGenresRows", json.loads(self.state.read_text())["migrated"]["u1"])

    def test_corrupt_or_odd_state_adopts_no_app_default(self):
        for text in ["{", "[]", "null", '"x"', '{"migrated": []}', '{"migrated": {"u1": "tv:x"}}', '{"migrated": {}, "pending": []}']:
            self.state.write_text(text)
            code, posts, out = self.run_main([], {"u1": {"tv": {"displayGenresRows": False}}})
            self.assertEqual(self.user_posts(posts), [], text)
            self.assertIn("state", out, text)
            self.assertIn("tv:displayGenresRows", json.loads(self.state.read_text())["migrated"]["u1"], text)

    def test_a_previous_default_still_moves_with_odd_state(self):
        self.state.write_text("{")
        code, posts, _ = self.run_main([], {"u1": {"tv": {"homeSections": OLD["homeSections"]}}})
        self.assertEqual(self.user_posts(posts), ["/Moonfin/Settings/u1"])

    def test_users_written_before_the_config(self):
        _, posts, _ = self.run_main([], {"u1": {"tv": {"homeSections": OLD["homeSections"]}}})
        self.assertEqual([p[1] for p in posts], ["/Moonfin/Settings/u1", f"/Plugins/{mb.PLUGIN_ID}/Configuration"])

    def test_failed_user_post_is_retried_against_the_previous_defaults(self):
        layout = {"tv": {"homeSections": OLD["homeSections"], "displayGenresRows": False}}
        code, posts, out = self.run_main([], {"u1": layout, "u2": layout}, fail={"u1"})
        self.assertEqual(code, 1)
        self.assertIn("u1", out)
        self.assertEqual(self.user_posts(posts), ["/Moonfin/Settings/u1", "/Moonfin/Settings/u2"])
        code, posts, _ = self.run_main([], {"u1": layout, "u2": {"tv": {"displayGenresRows": False}}}, old=self.applied())
        self.assertEqual(code, 0)
        self.assertEqual(self.user_posts(posts), ["/Moonfin/Settings/u1"])
        self.assertEqual(set(posts[0][2]["settings"]["tv"]), {"homeSections", "displayGenresRows"})
        code, posts, _ = self.run_main([], {"u1": {"tv": {"displayGenresRows": False}}}, old=self.applied())
        self.assertEqual(self.user_posts(posts), [])
        self.assertEqual(json.loads(self.state.read_text()).get("pending") or {}, {})

    def test_writes_run_under_the_run_lock(self):
        lock = self.state.with_suffix(".lock")
        seen = []

        def probe():
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen.append(False)
            except BlockingIOError:
                seen.append(True)
            finally:
                os.close(fd)

        self.run_main([], {"u1": {"tv": {"homeSections": OLD["homeSections"]}}}, probe=probe)
        self.assertEqual(seen, [True, True])

    def test_untouched_user_layout_follows_the_defaults(self):
        code, posts, _ = self.run_main([], {"u1": {"syncEnabled": True, "tv": {"homeSections": OLD["homeSections"], "displaySeerrRows": False}}})
        push = [p for p in posts if p[1] == "/Moonfin/Settings/u1"]
        self.assertEqual(len(push), 1)
        body = push[0][2]
        self.assertEqual((body["mergeMode"], body["settings"]["syncEnabled"], list(body["settings"]["tv"])), ("merge", True, ["homeSections"]))
        self.assertEqual(rows(body["settings"]["tv"]["homeSections"]), WEB_ORDER)

    def test_dry_run_and_check_write_nothing(self):
        for argv in (["--dry-run"], ["--check"]):
            code, posts, out = self.run_main(argv, {"u1": {"tv": {"homeSections": OLD["homeSections"]}}})
            self.assertEqual(posts, [])
            self.assertIn("1 user", out)
        self.assertEqual(code, 1)

    def test_own_choice_after_the_first_run_survives_the_next(self):
        self.run_main([], {"u1": {"tv": {"displayGenresRows": False}}})
        applied = mb.wanted({**CONFIG, "DefaultUserSettings": dict(OLD)}, LISTS, FOLDERS, None)["DefaultUserSettings"]
        code, posts, _ = self.run_main([], {"u1": {"tv": {"displayGenresRows": False}}}, old=applied)
        self.assertEqual([p for p in posts if p[1].startswith("/Moonfin/Settings/")], [])

    def test_dry_run_keeps_no_markers(self):
        self.run_main(["--dry-run"], {"u1": {"tv": {"displayGenresRows": False}}})
        self.assertFalse(self.state.exists())

    def test_users_without_settings_or_changes_not_written(self):
        code, posts, _ = self.run_main([], {"u1": {"global": {"navbarPosition": "top"}}})
        self.assertEqual([p[1] for p in posts], [f"/Plugins/{mb.PLUGIN_ID}/Configuration"])


if __name__ == "__main__":
    unittest.main()
