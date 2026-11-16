import importlib.util
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("collection_ids", pathlib.Path(__file__).with_name("collection-ids.py"))
ci = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci)


def member(name, coll):
    return {"Name": name, "ProviderIds": {"Tmdb": "1", **({"TmdbCollection": coll} if coll else {})}}


class PlanTest(unittest.TestCase):
    def test_set_matched_by_name_takes_its_members_collection(self):
        sets = [{"Id": "x", "Name": "X Collection", "ProviderIds": {"Tmdb": "1083423"}}]
        members = {"x": [member("Pearl", "950289"), member("MaXXXine", "950289")]}
        self.assertEqual(ci.plan(sets, members), [("x", "X Collection", "1083423", "950289")])

    def test_matching_set_is_left_alone(self):
        sets = [{"Id": "h", "Name": "Harry Potter Collection", "ProviderIds": {"Tmdb": "1241"}}]
        self.assertEqual(ci.plan(sets, {"h": [member("A", "1241"), member("B", "1241")]}), [])

    def test_members_that_disagree_change_nothing(self):
        sets = [{"Id": "x", "Name": "X", "ProviderIds": {"Tmdb": "5"}}]
        self.assertEqual(ci.plan(sets, {"x": [member("A", "6"), member("B", "7")]}), [])

    def test_members_without_a_collection_id_are_ignored(self):
        sets = [{"Id": "x", "Name": "X", "ProviderIds": {"Tmdb": "5"}}]
        self.assertEqual(ci.plan(sets, {"x": [member("A", "6"), member("B", None)]}), [("x", "X", "5", "6")])

    def test_sets_without_a_tmdb_id_are_not_franchises(self):
        sets = [{"Id": "l", "Name": "Netflix · Movies", "ProviderIds": {}}]
        self.assertEqual(ci.plan(sets, {"l": [member("A", "6")]}), [])

    def test_empty_set_unchanged(self):
        sets = [{"Id": "x", "Name": "X", "ProviderIds": {"Tmdb": "5"}}]
        self.assertEqual(ci.plan(sets, {"x": []}), [])


class FixTest(unittest.TestCase):
    def test_fix_sets_the_id_clears_the_overview_and_replaces_the_art(self):
        calls = []

        def api(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET":
                return {"Id": "x", "Name": "X Collection", "Overview": "X-Deal is", "ProviderIds": {"Tmdb": "1083423"}, "Tags": []}
        ci.fix(api, "admin", "x", "950289")
        self.assertEqual(calls[0][:2], ("GET", "/Users/admin/Items/x"))
        self.assertEqual(calls[1][:2], ("POST", "/Items/x"))
        self.assertEqual(calls[1][2]["ProviderIds"], {"Tmdb": "950289"})
        self.assertEqual(calls[1][2]["Overview"], "")
        self.assertEqual(calls[2][:2], ("POST", "/Items/x/Refresh?MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh&ReplaceAllMetadata=false&ReplaceAllImages=true"))


if __name__ == "__main__":
    unittest.main()
