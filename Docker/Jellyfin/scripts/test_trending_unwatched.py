import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
import urllib.parse

_spec = importlib.util.spec_from_file_location("trending_unwatched", pathlib.Path(__file__).with_name("trending-unwatched.py"))
tu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tu)

FOLDERS = [{"Name": "Movies", "ItemId": "real"}, {"Name": "Trending items", "ItemId": "libm"}, {"Name": "Trending shows", "ItemId": "libs"}]
CLEAN = {"Played": False, "PlaybackPositionTicks": 0, "PlayCount": 0}
HIDDEN = {"ver"}


class FakeJellyfin:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def api(self, method, path, body=None):
        self.calls.append((method, path, body))
        url = urllib.parse.urlsplit(path)
        q = dict(urllib.parse.parse_qsl(url.query))
        if path == "/Users":
            return [{"Id": u, "Name": "secret-name-" + u} for u in self.data]
        if path == "/Library/VirtualFolders":
            return FOLDERS
        if method == "GET" and url.path == "/Items":
            items = self.data[q["userId"]]
            if "ParentId" in q:
                return {"Items": [dict(Id=i, UserData=ud) for i, (lib, ud) in items.items() if lib == q["ParentId"]]}
            ids = q["Ids"].split(",")
            assert len(ids) <= 60
            return {"Items": [dict(Id=i, UserData=items[i][1]) for i in ids if i in items and i not in HIDDEN]}
        if method == "GET" and url.path.startswith("/Items/"):
            iid = url.path.split("/")[2]
            if iid not in self.data[q["userId"]]:
                raise tu.urllib.error.HTTPError(path, 404, "nf", {}, None)
            return dict(Id=iid, UserData=self.data[q["userId"]][iid][1])
        if method == "DELETE" and url.path.startswith("/UserPlayedItems/"):
            self.data[q["userId"]][url.path.split("/")[2]][1].update(CLEAN)
            return None
        raise AssertionError(path)

    def resets(self):
        return sorted((urllib.parse.urlsplit(p).path.split("/")[2], dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(p).query))["userId"]) for m, p, b in self.calls if m == "DELETE")


def fresh():
    return {
        "u1": {"film": ["libm", {"Played": True, "PlayCount": 1}], "ep1": ["libs", {"Played": False, "PlayCount": 1, "PlaybackPositionTicks": 0}],
               "series": ["libs", {"Played": True}], "ver": ["none", {"Played": True}], "realfilm": ["real", {"Played": True}]},
        "u2": {"film": ["libm", {"Played": False}], "ep1": ["libs", {"Played": False, "PlaybackPositionTicks": 50}], "ver": ["none", {}]},
    }


class Sweep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.index = pathlib.Path(self.tmp.name, "trending-index.json")
        self.index.write_text(json.dumps({"items": {"film": {}, "ver": {}, "ep1": {}}}))
        self.jf = FakeJellyfin(fresh())

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv):
        out = io.StringIO()
        code = tu.main(argv, api=self.jf.api, index_file=self.index, out=out)
        return code, out.getvalue()

    def test_marked(self):
        self.assertFalse(tu.marked({}))
        self.assertFalse(tu.marked({"Played": False, "PlaybackPositionTicks": 0, "PlayCount": 0}))
        for ud in ({"Played": True}, {"PlaybackPositionTicks": 1}, {"PlayCount": 2}):
            self.assertTrue(tu.marked(ud), ud)

    def test_sweep_resets_every_marked_placeholder_of_every_user_only(self):
        code, text = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertEqual(self.jf.resets(), [("ep1", "u1"), ("ep1", "u2"), ("film", "u1"), ("series", "u1"), ("ver", "u1")])
        self.assertEqual(self.jf.data["u1"]["realfilm"][1], {"Played": True})
        self.assertEqual(text.strip(), "2 users: 5 marked placeholders reset")
        self.assertNotIn("secret-name", text)

    def test_sweep_is_idempotent(self):
        self.run_cli([])
        self.jf.calls.clear()
        code, text = self.run_cli([])
        self.assertEqual((code, self.jf.resets(), text.strip()), (0, [], "2 users: 0 marked placeholders reset"))

    def test_dry_run_changes_nothing(self):
        code, text = self.run_cli(["--dry-run"])
        self.assertEqual((code, self.jf.resets(), text.strip()), (0, [], "2 users: 5 marked placeholders would be reset"))

    def test_check_counts_played_only(self):
        code, text = self.run_cli(["--check"])
        self.assertEqual((code, self.jf.resets(), text.strip()), (1, [], "3 placeholders played"))
        self.run_cli([])
        code, text = self.run_cli(["--check"])
        self.assertEqual((code, text.strip()), (0, "0 placeholders played"))

    def test_missing_index_still_sweeps_the_libraries(self):
        self.index.unlink()
        self.run_cli([])
        self.assertEqual(self.jf.resets(), [("ep1", "u1"), ("ep1", "u2"), ("film", "u1"), ("series", "u1")])

    def test_missing_library_fails(self):
        global FOLDERS
        saved, FOLDERS = FOLDERS, FOLDERS[:2]
        try:
            code, _ = self.run_cli([])
        finally:
            FOLDERS = saved
        self.assertEqual((code, self.jf.resets()), (1, []))

    def test_many_index_ids_are_read_in_chunks(self):
        self.index.write_text(json.dumps({"items": {f"x{n}": {} for n in range(150)} | {"ver": {}}}))
        self.run_cli([])
        self.assertIn(("ver", "u1"), self.jf.resets())

    def test_bad_usage(self):
        self.assertEqual(self.run_cli(["--nope"])[0], 2)


if __name__ == "__main__":
    unittest.main()
