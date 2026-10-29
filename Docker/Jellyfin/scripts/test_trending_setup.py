import importlib.util
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


class CountTest(unittest.TestCase):
    def test_summary(self):
        users = [{"Configuration": {"MyMediaExcludes": ["t"]}}, {"Configuration": {}}]
        self.assertEqual(ts.check_line(users, "t"), (False, "1 of 2 users see Trending items"))
        self.assertEqual(ts.check_line(users[:1], "t"), (True, "all 1 users hide Trending items"))


if __name__ == "__main__":
    unittest.main()
