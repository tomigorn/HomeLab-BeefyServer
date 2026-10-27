import importlib.util
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("library_options", pathlib.Path(__file__).with_name("library-options.py"))
lo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lo)

FOLDERS = [
    {"Name": "Movies", "CollectionType": "movies", "ItemId": "m1", "LibraryOptions": {"AutomaticallyAddToCollection": False, "Other": 1}},
    {"Name": "Shows", "CollectionType": "tvshows", "ItemId": "t1", "LibraryOptions": {"AutomaticallyAddToCollection": False}},
]


class LibraryOptionsTest(unittest.TestCase):
    def test_movies_library_gets_auto_collections_and_nothing_else_changes(self):
        changes = lo.wanted(FOLDERS)
        self.assertEqual(changes, [("m1", {"AutomaticallyAddToCollection": True, "Other": 1})])

    def test_already_set_is_unchanged(self):
        folders = [{**FOLDERS[0], "LibraryOptions": {"AutomaticallyAddToCollection": True}}, FOLDERS[1]]
        self.assertEqual(lo.wanted(folders), [])

    def test_input_is_not_mutated(self):
        lo.wanted(FOLDERS)
        self.assertFalse(FOLDERS[0]["LibraryOptions"]["AutomaticallyAddToCollection"])


if __name__ == "__main__":
    unittest.main()
