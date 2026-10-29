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

    def test_tile_only_when_needed(self):
        self.assertTrue(ts.tile_needed({"ImageTags": {}}, False))
        self.assertTrue(ts.tile_needed({}, False))
        self.assertFalse(ts.tile_needed({"ImageTags": {"Primary": "x"}}, False))
        self.assertTrue(ts.tile_needed({"ImageTags": {"Primary": "x"}}, True))


class MainTest(unittest.TestCase):
    def run_main(self, folders_after_create, argv=()):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        calls = []
        seq = iter(folders_after_create)

        def api(method, path, body=None):
            calls.append((method, path))
            if path == "/Library/VirtualFolders" and method == "GET":
                return next(seq)
            if path == "/Users":
                return [{"Id": "u", "Name": "u", "Configuration": {}}]
            if path == "/Library/MediaFolders":
                return {"Items": []}
            return None

        fake = mock.MagicMock(api=api, api_raw=lambda *a: None)
        out = io.StringIO()
        with mock.patch.dict(sys.modules, {"jellyfin_api": fake}), mock.patch.object(ts, "seerr_enabled", return_value=False), redirect_stdout(out):
            rc = ts.main(list(argv))
        return rc, calls, out.getvalue()

    def test_present_unresolved_folder_is_never_recreated_or_hidden(self):
        rc, calls, out = self.run_main([[{"Name": "Trending items"}]])
        self.assertEqual(rc, 1)
        self.assertFalse([c for c in calls if c[0] == "POST"])
        self.assertIn("unresolved", out)

    def test_create_without_resolved_id_exits_before_writing_users(self):
        rc, calls, out = self.run_main([[], [{"Name": "Trending items"}]])
        self.assertEqual(rc, 1)
        self.assertFalse([c for c in calls if c[1].startswith("/Users/")])


class CountTest(unittest.TestCase):
    def test_summary(self):
        users = [{"Configuration": {"MyMediaExcludes": ["t"]}}, {"Configuration": {}}]
        self.assertEqual(ts.check_line(users, "t"), (False, "1 of 2 users see Trending items"))
        self.assertEqual(ts.check_line(users[:1], "t"), (True, "all 1 users hide Trending items"))


if __name__ == "__main__":
    unittest.main()
