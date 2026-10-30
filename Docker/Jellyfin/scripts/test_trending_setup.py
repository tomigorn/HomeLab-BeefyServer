import importlib.util
import sys
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("trending_setup", pathlib.Path(__file__).with_name("trending-setup.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)


class LibraryOptionsTest(unittest.TestCase):
    def test_new_library_options(self):
        o = ts.library_options(None)
        movie = [t for t in o["TypeOptions"] if t["Type"] == "Movie"][0]
        self.assertEqual(movie["MetadataFetchers"], [])
        self.assertEqual(movie["ImageFetchers"], [])
        self.assertEqual(o["LocalMetadataReaderOrder"], ["Nfo"])
        self.assertEqual(o["MetadataSavers"], [])
        for key in ("SaveLocalMetadata", "EnableRealtimeMonitor", "EnableTrickplayImageExtraction", "ExtractTrickplayImagesDuringLibraryScan",
                    "EnableChapterImageExtraction", "ExtractChapterImagesDuringLibraryScan", "AutomaticallyAddToCollection"):
            self.assertIs(o[key], False, key)
        self.assertIn("Intro Skipper", o["DisabledMediaSegmentProviders"])

    def test_shows_library_options(self):
        o = ts.library_options(None, "tvshows")
        self.assertEqual(sorted(x["Type"] for x in o["TypeOptions"]), ["Episode", "Season", "Series"])
        for x in o["TypeOptions"]:
            self.assertEqual((x["MetadataFetchers"], x["ImageFetchers"]), ([], []), x["Type"])
        for key in ("EnableRealtimeMonitor", "EnableInternetProviders", "EnableTrickplayImageExtraction", "EnableChapterImageExtraction", "EnableAutomaticSeriesGrouping"):
            self.assertIs(o[key], False, key)
        self.assertEqual(o["LocalMetadataReaderOrder"], ["Nfo"])
        self.assertIn("Intro Skipper", o["DisabledMediaSegmentProviders"])
        self.assertEqual(ts.library_options(o, "tvshows"), o)

    def test_film_library_keeps_its_series_grouping_flag(self):
        self.assertIs(ts.library_options({"EnableAutomaticSeriesGrouping": True})["EnableAutomaticSeriesGrouping"], True)

    def test_libraries(self):
        self.assertEqual(ts.LIBRARIES, [("Trending items", "movies", "/config/trending/library"), ("Trending shows", "tvshows", "/config/trending/shows")])

    def test_existing_options_keep_unrelated_keys(self):
        o = ts.library_options({"PathInfos": [{"Path": "/x"}], "EnableRealtimeMonitor": True, "Other": 1})
        self.assertEqual(o["PathInfos"], [{"Path": "/x"}])
        self.assertEqual(o["Other"], 1)
        self.assertIs(o["EnableRealtimeMonitor"], False)

    def test_options_idempotent(self):
        o = ts.library_options(None)
        self.assertEqual(ts.library_options(o), o)

    def test_library_id(self):
        folders = [{"Name": "Movies", "ItemId": "m"}, {"Name": "Trending items", "ItemId": "t"}]
        self.assertEqual(ts.library_id(folders), "t")
        self.assertIsNone(ts.library_id(folders[:1]))


class HiddenTest(unittest.TestCase):
    def test_adds_exclude_and_keeps_others(self):
        c = {"MyMediaExcludes": ["a"], "X": 1}
        self.assertEqual(ts.hidden(c, "t"), {"MyMediaExcludes": ["a", "t"], "X": 1})
        self.assertEqual(c["MyMediaExcludes"], ["a"])

    def test_idempotent(self):
        c = ts.hidden({}, "t")
        self.assertEqual(ts.hidden(c, "t"), c)

    def test_missing_key(self):
        self.assertEqual(ts.hidden({}, "t")["MyMediaExcludes"], ["t"])


class UnresolvedTest(unittest.TestCase):
    def test_unresolved_folder_is_present_without_id(self):
        folders = [{"Name": "Trending items"}]
        self.assertIsNone(ts.library_id(folders))
        self.assertEqual(ts.find_library(folders), {"Name": "Trending items"})
        self.assertIsNone(ts.find_library([{"Name": "Movies", "ItemId": "m"}]))

    def test_hidden_without_id_changes_nothing(self):
        self.assertEqual(ts.hidden({"MyMediaExcludes": ["a"]}, None), {"MyMediaExcludes": ["a"]})
        self.assertNotIn("MyMediaExcludes", ts.hidden({}, None))

    def test_collections_folder_found_by_type(self):
        items = [{"Name": "Movies", "Id": "m", "CollectionType": "movies"}, {"Name": "Collections", "Id": "c", "CollectionType": "boxsets"}]
        self.assertEqual(ts.collections_folder(items)["Id"], "c")
        self.assertIsNone(ts.collections_folder(items[:1]))

    def test_tile_posters_from_the_cache_in_top_10_order(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "lists.json").write_text(json.dumps({"home": {"movies": [{"p": {"en": "/a.jpg"}}, {"p": {"en": None}}, {"p": {"en": "/missing.jpg"}}, {"p": "/b.jpg"}], "shows": []}}))
            (root / "w780_a.jpg").write_bytes(b"A")
            (root / "w780_b.jpg").write_bytes(b"B")
            self.assertEqual(ts.tile_posters(root / "lists.json", root), [b"A", b"B"])
            self.assertEqual(ts.tile_posters(root / "absent.json", root), [])

    def test_tile_only_when_needed(self):
        self.assertTrue(ts.tile_needed({"ImageTags": {}}, False))
        self.assertTrue(ts.tile_needed({}, False))
        self.assertFalse(ts.tile_needed({"ImageTags": {"Primary": "x"}}, False))
        self.assertTrue(ts.tile_needed({"ImageTags": {"Primary": "x"}}, True))


class MainTest(unittest.TestCase):
    def run_main(self, folders_after_create, argv=(), scan_states=("Idle",)):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        calls = []
        self.bodies = {}
        seq = list(folders_after_create)
        states = list(scan_states)

        def api(method, path, body=None):
            calls.append((method, path))
            self.bodies[path] = body
            if path == "/Library/VirtualFolders" and method == "GET":
                return seq.pop(0) if len(seq) > 1 else seq[0]
            if path == "/Users":
                return [{"Id": "u", "Name": "u", "Configuration": {}}]
            if path == "/Library/MediaFolders":
                return {"Items": []}
            if path == "/ScheduledTasks":
                return [{"Key": "RefreshLibrary", "State": states.pop(0) if len(states) > 1 else states[0]}]
            return None

        fake = mock.MagicMock(api=api, api_raw=lambda *a: None)
        out = io.StringIO()
        with mock.patch.dict(sys.modules, {"jellyfin_api": fake}), mock.patch.object(ts, "seerr_enabled", return_value=False), \
                mock.patch.object(ts, "ensure_dirs", return_value=[]) as dirs, mock.patch.object(ts, "sleep", lambda s: None), redirect_stdout(out):
            rc = ts.main(list(argv))
        self.dirs = dirs
        return rc, calls, out.getvalue()

    def test_present_unresolved_folder_is_never_recreated_or_hidden(self):
        rc, calls, out = self.run_main([[{"Name": "Trending items"}, {"Name": "Trending shows"}]])
        self.assertEqual(rc, 1)
        self.assertFalse([c for c in calls if c[0] == "POST"])
        self.assertIn("unresolved", out)

    def test_create_without_resolved_id_exits_before_writing_users(self):
        rc, calls, out = self.run_main([[], [{"Name": "Trending items"}], [{"Name": "Trending items"}, {"Name": "Trending shows"}]])
        self.assertEqual(rc, 1)
        self.assertFalse([c for c in calls if c[1].startswith("/Users/")])
        self.assertEqual(calls.count(("POST", "/Library/Refresh")), 1)
        self.assertIn("no ItemId", out)

    def test_new_library_is_scanned_and_hidden_in_one_flow(self):
        movies = {"Name": "Trending items", "ItemId": "t", "LibraryOptions": ts.library_options(None)}
        shows = {"Name": "Trending shows", "LibraryOptions": ts.library_options(None, "tvshows")}
        rc, calls, out = self.run_main([[movies], [movies, shows], [movies, shows], [movies, dict(shows, ItemId="s")]], scan_states=("Running", "Running", "Idle"))
        self.assertEqual(rc, 0, out)
        create = next(i for i, c in enumerate(calls) if c[1].startswith("/Library/VirtualFolders?"))
        scan = calls.index(("POST", "/Library/Refresh"))
        hide = calls.index(("POST", "/Users/u/Configuration"))
        self.assertLess(create, scan)
        self.assertLess(scan, hide)
        self.assertEqual(self.bodies["/Users/u/Configuration"]["MyMediaExcludes"], ["t", "s"])
        self.assertLess(hide, calls.index(("GET", "/ScheduledTasks")))
        self.assertEqual(calls.count(("GET", "/ScheduledTasks")), 3)
        self.assertIn("scan finished", out)

    def test_resolved_libraries_need_no_scan(self):
        movies = {"Name": "Trending items", "ItemId": "t", "LibraryOptions": ts.library_options(None)}
        shows = {"Name": "Trending shows", "ItemId": "s", "LibraryOptions": ts.library_options(None, "tvshows")}
        rc, calls, out = self.run_main([[movies, shows]])
        self.assertEqual(rc, 0, out)
        self.assertNotIn(("POST", "/Library/Refresh"), calls)
        self.assertNotIn(("GET", "/ScheduledTasks"), calls)
        self.dirs.assert_called_once()
        self.assertEqual(list(self.dirs.call_args.args[1]), ["library", "shows", "originals"])

    def test_dry_run_neither_scans_nor_creates_dirs(self):
        rc, calls, out = self.run_main([[]], argv=["--dry-run"])
        self.assertFalse([c for c in calls if c[0] == "POST"])
        self.assertIs(self.dirs.call_args.args[-1], True)
        self.assertIn("would create library Trending shows", out)

    def test_creates_the_shows_library_next_to_the_film_library_and_hides_both(self):
        movies = {"Name": "Trending items", "ItemId": "t", "LibraryOptions": ts.library_options(None)}
        shows = {"Name": "Trending shows", "ItemId": "s", "LibraryOptions": ts.library_options(None, "tvshows")}
        rc, calls, out = self.run_main([[movies], [movies, shows]])
        self.assertEqual(rc, 0, out)
        creates = [c[1] for c in calls if c[1].startswith("/Library/VirtualFolders?")]
        self.assertEqual(len(creates), 1)
        self.assertIn("name=Trending+shows", creates[0])
        self.assertIn("collectionType=tvshows", creates[0])
        self.assertIn("paths=%2Fconfig%2Ftrending%2Fshows", creates[0])
        self.assertIn("refreshLibrary=false", creates[0])
        self.assertEqual(self.bodies["/Users/u/Configuration"]["MyMediaExcludes"], ["t", "s"])
        self.assertEqual(calls.count(("POST", "/Library/Refresh")), 1)
        self.assertLess(calls.index(("POST", "/Users/u/Configuration")), calls.index(("POST", "/Library/Refresh")))
        self.assertIn("scan finished", out)


class DirsTest(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "trending"
        (self.root / "library").mkdir(parents=True)
        os.chmod(self.root / "library", 0o2755)
        self.st = (self.root / "library").stat()
        self.runs = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_dirs(self, dry=False):
        return ts.ensure_dirs(self.root, ("library", "shows"), dry, run=lambda cmd, check: self.runs.append((cmd, check)))

    def test_missing_shows_dir_is_created_owned_like_library(self):
        self.assertEqual(self.run_dirs(), [self.root / "shows"])
        self.assertEqual(self.runs, [(["sudo", "-n", "install", "-d", "-o", str(self.st.st_uid), "-g", str(self.st.st_gid), "-m", "2755", str(self.root / "shows")], True)])

    def test_existing_dirs_are_left_alone(self):
        (self.root / "shows").mkdir()
        self.assertEqual(self.run_dirs(), [])
        self.assertEqual(self.runs, [])

    def test_dry_run_only_reports(self):
        self.assertEqual(self.run_dirs(dry=True), [self.root / "shows"])
        self.assertEqual(self.runs, [])

    def test_host_dirs_match_the_library_paths(self):
        self.assertEqual(ts.HOST_ROOT, pathlib.Path("/srv/appdata/jellyfin/trending"))
        self.assertEqual([pathlib.PurePath(p).name for _, _, p in ts.LIBRARIES], ["library", "shows"])


class CountTest(unittest.TestCase):
    def test_summary(self):
        users = [{"Configuration": {"MyMediaExcludes": ["t", "s"]}}, {"Configuration": {"MyMediaExcludes": ["t"]}}]
        libs = {"Trending items": "t", "Trending shows": "s"}
        self.assertEqual(ts.check_line(users, libs), (False, "1 of 2 users see Trending shows"))
        self.assertEqual(ts.check_line(users[:1], libs), (True, "all 1 users hide Trending items and Trending shows"))


if __name__ == "__main__":
    unittest.main()
