import importlib.util
import json
import pathlib
import random
import tempfile
import unittest
import urllib.error

_spec = importlib.util.spec_from_file_location("media_bar", pathlib.Path(__file__).with_name("media-bar.py"))
mbar = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mbar)

LIBS = ["mov", "tv"]
FOLDERS = [
    {"ItemId": "mov", "Name": "Movies", "CollectionType": "movies"},
    {"ItemId": "tv", "Name": "Shows", "CollectionType": "tvshows"},
    {"ItemId": "box", "Name": "Collections & Trending", "CollectionType": "boxsets"},
    {"ItemId": "tr", "Name": "Trending items", "CollectionType": "movies"},
    {"ItemId": "ts", "Name": "Trending shows", "CollectionType": "tvshows"},
]


def movie(i, played=False, pos=0, backdrop=True, path=None):
    return {"Id": i, "Type": "Movie", "Path": path or f"/media/movies/{i}", "BackdropImageTags": ["b"] if backdrop else [],
            "UserData": {"Played": played, "PlaybackPositionTicks": pos}}


def series(i, backdrop=True, path=None):
    return {"Id": i, "Type": "Series", "Path": path or f"/media/tv/{i}", "BackdropImageTags": ["b"] if backdrop else [], "UserData": {"Played": False}}


class EligibleTest(unittest.TestCase):
    def test_unwatched_films_and_unstarted_series_with_backdrops(self):
        movies = [movie("m1"), movie("m2", played=True), movie("m3", pos=5), movie("m4", backdrop=False), movie("m5", path="/config/trending/library/x")]
        shows = [series("s1"), series("s2"), series("s3", backdrop=False), series("s4", path="/config/trending/shows/x")]
        self.assertEqual(mbar.eligible(movies, shows, {"s2"}), ["m1", "s1"])


class ChooseTest(unittest.TestCase):
    def test_small_pool_takes_everything(self):
        self.assertEqual(sorted(mbar.choose([], ["a", "b", "c"], 40, random.Random(1), resample=False)), ["a", "b", "c"])

    def test_keeps_still_eligible_members_and_fills_up(self):
        pool = [f"i{n}" for n in range(10)]
        want = mbar.choose(["i1", "i2", "gone"], pool, 4, random.Random(1), resample=False)
        self.assertEqual(len(want), 4)
        self.assertTrue({"i1", "i2"} <= set(want))
        self.assertNotIn("gone", want)
        self.assertTrue(set(want) <= set(pool))

    def test_resample_draws_a_fresh_random_set(self):
        pool = [f"i{n}" for n in range(100)]
        a = mbar.choose([], pool, 40, random.Random(1), resample=True)
        b = mbar.choose(a, pool, 40, random.Random(2), resample=True)
        self.assertEqual((len(a), len(set(a)), len(b)), (40, 40, 40))
        self.assertNotEqual(set(a), set(b))


class BlockedTagsTest(unittest.TestCase):
    def test_blocks_every_other_owner_keeps_foreign_tags(self):
        have = ["kids", "hss-media-bar-gone"]
        self.assertEqual(mbar.blocked_tags(have, "u1", {"u1", "u2", "u3"}), ["kids", "hss-media-bar-u2", "hss-media-bar-u3"])

    def test_own_tag_never_blocked(self):
        self.assertEqual(mbar.blocked_tags(["hss-media-bar-u1"], "u1", {"u1"}), [])


class SettingsChangeTest(unittest.TestCase):
    WANT = {"mediaBarSourceType": "random", "mediaBarLibraryIds": [], "mediaBarCollectionIds": ["set"]}

    def test_old_default_moves_to_the_set(self):
        s = {"global": {"mediaBarSourceType": "recentlyAdded", "mediaBarLibraryIds": LIBS},
             "tv": {"mediaBarSourceType": "recentlyAdded", "mediaBarLibraryIds": ["tv", "mov"], "mediaBarCollectionIds": [], "mediaBarItemCount": 10},
             "mobile": {"navbarPosition": "left"}}
        self.assertEqual(mbar.settings_change(s, "set", {"set"}, LIBS), {"global": self.WANT, "tv": self.WANT})

    def test_own_choice_stays(self):
        s = {"global": {"mediaBarSourceType": "recentlyReleased", "mediaBarLibraryIds": LIBS},
             "tv": {"mediaBarSourceType": "random", "mediaBarLibraryIds": ["mov"], "mediaBarCollectionIds": []},
             "mobile": {"mediaBarSourceType": "random", "mediaBarLibraryIds": [], "mediaBarCollectionIds": ["hidden-gems"]}}
        self.assertEqual(mbar.settings_change(s, "set", {"set"}, LIBS), {})

    def test_already_set_or_older_set_of_ours(self):
        self.assertEqual(mbar.settings_change({"global": dict(self.WANT)}, "set", {"set"}, LIBS), {})
        old = {"global": {**self.WANT, "mediaBarCollectionIds": ["old"]}}
        self.assertEqual(mbar.settings_change(old, "set", {"set", "old"}, LIBS), {"global": self.WANT})

    def test_missing_global_gets_one(self):
        self.assertEqual(mbar.settings_change({"syncEnabled": True}, "set", {"set"}, LIBS), {"global": self.WANT})


class FakeApi:
    def __init__(self, users, settings, movies, shows, started=(), boxes=None):
        self.users = {u: {"Id": u, "Name": u, "Policy": {"BlockedTags": [], "IsAdministrator": False, "AuthenticationProviderId": "p"}} for u in users}
        self.settings = settings
        self.movies, self.shows, self.started = movies, shows, set(started)
        self.boxes = boxes or {}
        self.calls = []
        self.n = 0

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        p, _, q = path.partition("?")
        args = dict(a.split("=", 1) for a in q.split("&") if "=" in a)
        if method == "GET" and p == "/Users":
            return [json.loads(json.dumps(u)) for u in self.users.values()]
        if method == "GET" and p.startswith("/Users/"):
            return json.loads(json.dumps(self.users[p.split("/")[2]]))
        if method == "POST" and p.endswith("/Policy"):
            self.users[p.split("/")[2]]["Policy"] = body
            return None
        if method == "GET" and p == "/Library/VirtualFolders":
            return FOLDERS
        if method == "GET" and p.startswith("/Moonfin/Settings/"):
            uid = p.split("/")[3]
            if uid not in self.settings:
                raise urllib.error.HTTPError(path, 404, "nf", {}, None)
            return json.loads(json.dumps(self.settings[uid]))
        if method == "POST" and p.startswith("/Moonfin/Settings/"):
            uid = p.split("/")[3]
            for prof, vals in body["settings"].items():
                if isinstance(vals, dict):
                    self.settings[uid].setdefault(prof, {}).update(vals)
            return None
        if method == "GET" and p == "/Items" and args.get("IncludeItemTypes") == "BoxSet":
            return {"Items": [{"Id": b, "Name": v["Name"], "Tags": list(v["Tags"])} for b, v in self.boxes.items()]}
        if method == "GET" and p == "/Items" and "ParentId" in args and args["ParentId"] in self.boxes:
            return {"Items": [{"Id": i} for i in self.boxes[args["ParentId"]]["members"]]}
        if method == "GET" and p == "/Items" and args.get("IncludeItemTypes") == "Movie":
            return {"Items": self.movies if args["ParentId"] == "mov" else []}
        if method == "GET" and p == "/Items" and args.get("IncludeItemTypes") == "Series":
            return {"Items": self.shows if args["ParentId"] == "tv" else []}
        if method == "GET" and p == "/Items" and args.get("IncludeItemTypes") == "Episode":
            return {"Items": [{"Id": f"e{s}", "SeriesId": s} for s in self.started] if args["ParentId"] == "tv" else []}
        if method == "POST" and p == "/Collections":
            self.n += 1
            bid = f"box{self.n}"
            self.boxes[bid] = {"Name": args["name"], "Tags": [], "members": args["ids"].split(",")}
            return {"Id": bid}
        if method == "GET" and p.startswith("/Items/"):
            b = self.boxes[p.split("/")[2]]
            return {"Id": p.split("/")[2], "Name": b["Name"], "Tags": list(b["Tags"]), "LockData": b.get("LockData", False), "ForcedSortName": b.get("ForcedSortName")}
        if method == "POST" and p.startswith("/Items/"):
            b = self.boxes[p.split("/")[2]]
            b.update(Name=body["Name"], Tags=list(body["Tags"]), LockData=body["LockData"], ForcedSortName=body["ForcedSortName"])
            return None
        if p.startswith("/Collections/") and p.endswith("/Items"):
            b = self.boxes[p.split("/")[2]]
            ids = args["ids"].split(",")
            b["members"] = b["members"] + [i for i in ids if i not in b["members"]] if method == "POST" else [i for i in b["members"] if i not in ids]
            return None
        if method == "DELETE" and p.startswith("/Items/"):
            self.boxes.pop(p.split("/")[2])
            return None
        raise AssertionError(f"unexpected {method} {path}")


def old_default():
    return {"global": {"mediaBarSourceType": "recentlyAdded", "mediaBarLibraryIds": LIBS}, "syncEnabled": True}


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, api, *argv, day="2026-10-30", seed=1):
        return mbar.run(list(argv), api=api, state_path=self.state, today=day, rng=random.Random(seed), log=lambda *a: None)

    def test_creates_a_hidden_set_per_moonfin_user_and_points_the_bar_at_it(self):
        api = FakeApi(["u1", "u2"], {"u1": old_default()}, [movie("m1"), movie("m2", played=True)], [series("s1"), series("s2")], started={"s2"})
        self.assertEqual(self.run_once(api), 0)
        (bid, box), = api.boxes.items()
        self.assertEqual((box["Name"], sorted(box["Tags"]), sorted(box["members"])), ("For you", ["hss-media-bar", "hss-media-bar-u1"], ["m1", "s1"]))
        self.assertEqual(api.users["u2"]["Policy"]["BlockedTags"], ["hss-media-bar-u1"])
        self.assertEqual(api.users["u2"]["Policy"]["AuthenticationProviderId"], "p")
        self.assertEqual(api.users["u1"]["Policy"]["BlockedTags"], [])
        self.assertEqual(api.settings["u1"]["global"], {"mediaBarSourceType": "random", "mediaBarLibraryIds": [], "mediaBarCollectionIds": [bid]})
        self.assertNotIn("u2", api.settings)

    def test_other_users_blocked_before_the_set_exists(self):
        api = FakeApi(["u1", "u2"], {"u1": old_default()}, [movie("m1")], [])
        self.run_once(api)
        kinds = [(m, p.split("?")[0]) for m, p, _ in api.writes()]
        self.assertLess(kinds.index(("POST", "/Users/u2/Policy")), kinds.index(("POST", "/Collections")))

    def test_second_run_writes_nothing(self):
        api = FakeApi(["u1", "u2"], {"u1": old_default()}, [movie("m1")], [series("s1")])
        self.run_once(api)
        api.calls.clear()
        self.assertEqual(self.run_once(api), 0)
        self.assertEqual(api.writes(), [])

    def test_watched_film_and_started_series_leave_the_set(self):
        api = FakeApi(["u1"], {"u1": old_default()}, [movie("m1"), movie("m2")], [series("s1"), series("s2")])
        self.run_once(api)
        api.movies = [movie("m1", played=True), movie("m2")]
        api.started = {"s1"}
        self.run_once(api)
        (box,) = api.boxes.values()
        self.assertEqual(sorted(box["members"]), ["m2", "s2"])

    def test_nothing_eligible_leaves_the_set_unchanged(self):
        api = FakeApi(["u1"], {"u1": old_default()}, [movie("m1")], [])
        self.run_once(api)
        api.movies = [movie("m1", played=True)]
        self.run_once(api)
        (box,) = api.boxes.values()
        self.assertEqual(box["members"], ["m1"])

    def test_gone_user_set_removed_and_unblocked(self):
        api = FakeApi(["u1", "u2"], {"u1": old_default(), "u2": old_default()}, [movie("m1")], [])
        self.run_once(api)
        self.assertEqual(len(api.boxes), 2)
        del api.users["u2"]
        self.run_once(api)
        self.assertEqual([sorted(b["Tags"]) for b in api.boxes.values()], [["hss-media-bar", "hss-media-bar-u1"]])
        self.assertEqual(api.users["u1"]["Policy"]["BlockedTags"], [])

    def test_untagged_set_of_an_earlier_run_is_tagged_not_duplicated(self):
        api = FakeApi(["u1"], {"u1": old_default()}, [movie("m1")], [])
        self.state.write_text(json.dumps({"created": {"u1": "box9"}}))
        api.boxes["box9"] = {"Name": "For you u1", "Tags": [], "members": ["m1"]}
        self.run_once(api)
        self.assertEqual(list(api.boxes), ["box9"])
        self.assertEqual(sorted(api.boxes["box9"]["Tags"]), ["hss-media-bar", "hss-media-bar-u1"])

    def test_dry_run_and_check_write_nothing(self):
        api = FakeApi(["u1", "u2"], {"u1": old_default()}, [movie("m1")], [])
        self.assertEqual(self.run_once(api, "--dry-run"), 0)
        self.assertEqual(self.run_once(api, "--check"), 1)
        self.assertEqual(api.writes(), [])
        self.run_once(api)
        self.assertEqual(self.run_once(api, "--check"), 0)

    def test_check_flags_a_dropped_block(self):
        api = FakeApi(["u1", "u2"], {"u1": old_default()}, [movie("m1")], [])
        self.run_once(api)
        api.users["u2"]["Policy"]["BlockedTags"] = []
        self.assertEqual(self.run_once(api, "--check"), 1)


if __name__ == "__main__":
    unittest.main()
