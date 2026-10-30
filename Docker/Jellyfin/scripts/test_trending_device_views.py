import importlib.util
import json
import pathlib
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location("trending_device_views", pathlib.Path(__file__).with_name("trending-device-views.py"))
tdv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tdv)

UID = "a" * 32
NAME = "Jane Doe"
VIEW = {"Id": "v1", "Name": "Collections & Trending", "CollectionType": "boxsets"}
MOVIES = {"Id": "v2", "Name": "Movies", "CollectionType": "movies"}
TOP = {"Id": "b1", "Name": "Top 10 Switzerland · Movies", "Type": "BoxSet"}
TOPS = {"Id": "b2", "Name": "Top 10 Switzerland · Shows", "Type": "BoxSet"}
SHOW = {"Id": "s1", "Name": "Some Show", "Type": "Series"}
EPS = [{"Id": "e1", "Name": "⬇ Download series now", "Type": "Episode", "ParentIndexNumber": 0, "IndexNumber": 1}, {"Id": "e2", "Name": "🌙 Download series tonight · lower power cost", "Type": "Episode", "ParentIndexNumber": 0, "IndexNumber": 2}]
FOUND = {"Id": "m1", "Name": "Some Film", "MediaSources": [{"Name": "⬇ Download movie now", "Path": "/media/internal/film.mp4"}, {"Name": "🌙 Download movie tonight · lower power cost"}], "Path": "/media/internal/film.mp4", "UserData": {"Key": UID, "IsFavorite": True, "Played": True, "PlayCount": 3, "PlaybackPositionTicks": 777, "LastPlayedDate": "2026-01-02T03:04:05Z", "PlayedPercentage": 55.5, "Likes": True, "UnplayedItemCount": 4}}


def fake(path):
    page = lambda items: {"Items": items, "TotalRecordCount": len(items), "StartIndex": 0}
    if path.startswith("/UserViews"):
        return page([VIEW, MOVIES, {"Id": "hidden", "Name": "Trending items", "CollectionType": "movies"}])
    if path.startswith("/Users/Me"):
        return {"Name": NAME, "Configuration": {"MyMediaExcludes": ["hidden"]}}
    if path.startswith("/Items/m1/"):
        return page([]) if "Special" not in path else []
    if path.startswith("/Items/m1"):
        return {**FOUND, "UserId": UID, "AccessToken": "tok"}
    if path.startswith("/Shows/s1/Seasons"):
        return page([{"Id": "se0", "Name": "Specials", "Type": "Season", "IndexNumber": 0}])
    if path.startswith("/Shows/s1/Episodes") and "seasonId=se0" in path:
        return page(EPS)
    if path.startswith("/Items/s1"):
        return SHOW
    if path.startswith("/Items/Latest"):
        return []
    if "parentId=v1" in path:
        return page([TOP, TOPS])
    if "parentId=b2" in path and "includeItemTypes=Series" in path:
        return page([SHOW])
    if "parentId=b1" in path and "includeItemTypes=Movie" in path:
        return page([FOUND])
    return page([])


class DeviceViewsTest(unittest.TestCase):
    def capture(self):
        out = {}
        reqs = {}
        for name in tdv.CLIENTS:
            out[name], reqs[name] = tdv.run_client(name, fake, UID)
        return out, reqs

    def test_requests_have_no_secrets(self):
        out, reqs = self.capture()
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            for name, data in out.items():
                tdv.save(d, name, data, UID, NAME)
            tdv.save_meta(d, reqs)
            self.assertEqual(len(list(d.glob("*.json"))), 19)
            for f in d.glob("*.json"):
                text = f.read_text()
                for bad in ("api_key", "Token", UID, NAME, "LastPlayedDate", '"IsFavorite": true', '"Played": true', "PlayCount", "PlayedPercentage", "Likes", "777", '"Path"', "/media/internal"):
                    self.assertNotIn(bad, text, f.name)
        for r in (x for rs in reqs.values() for x in rs):
            self.assertEqual(r["method"], "GET")
            self.assertNotIn("api_key", r["path_with_query"])
            self.assertNotIn("Token", r["path_with_query"])
            self.assertNotIn("isRecursive", r["path_with_query"])

    def test_scrub_removes_user_names(self):
        data = {"UserId": UID, "AccessToken": "tok", "User": {"Name": NAME, "Id": UID}, "Items": [{"Name": f"{NAME}'s list", "Path": f"/Users/{UID}/x"}]}
        text = json.dumps(tdv.scrub(data, UID, NAME))
        for bad in ("UserId", "AccessToken", NAME, UID, "tok"):
            self.assertNotIn(bad, text)

    def test_scrub_whole_words_and_short_names(self):
        out = tdv.scrub({"a": "Madison and Max", "b": "Al's list", "c": "Alps"}, "", "Al")
        self.assertEqual(out, {"a": "Madison and Max", "b": "viewer's list", "c": "Alps"})
        self.assertEqual(tdv.scrub({"a": "Madison"}, "", "Mad"), {"a": "Madison"})

    def test_logout_failure_does_not_skip_other_client(self):
        calls = []
        def lg(tok, dev):
            calls.append(dev)
            raise OSError("x")
        tdv.logout_all([("t1", "d1"), ("t2", "d2")], lg)
        self.assertEqual(calls, ["d1", "d2"])

    def test_hidden_library_not_in_media_and_found_item_is_the_target(self):
        out, reqs = self.capture()
        self.assertNotIn("Trending items", json.dumps(out["swiftfin"]["media"]))
        self.assertEqual(out["androidtv"]["details"]["Id"], "m1")
        self.assertIn("searchTerm=Some+Film", [r["path_with_query"] for r in reqs["swiftfin"] if r["screen"] == "search"][0])


    def test_shows_list_series_page_and_recently_added(self):
        out, reqs = self.capture()
        for name in tdv.CLIENTS:
            self.assertEqual([i["Id"] for i in out[name]["shows"]["Series"]["Items"]], ["s1"], name)
            self.assertEqual([s["Name"] for s in out[name]["series"]["seasons"]["Items"]], ["Specials"], name)
            self.assertEqual([e["Id"] for e in out[name]["series"]["episodes"]["Items"]], ["e1", "e2"], name)
            self.assertEqual(out[name]["series"]["item"]["Id"], "s1", name)
        recent = [r["path_with_query"] for r in reqs["swiftfin"] if r["screen"] == "recent"]
        self.assertEqual(len(recent), 1)
        for part in ("includeItemTypes=Movie,Series", "sortBy=DateCreated", "sortOrder=Descending", "recursive=true"):
            self.assertIn(part, recent[0])
        latest = [r["path_with_query"] for r in reqs["androidtv"] if r["screen"] == "recent"]
        self.assertEqual(sorted(latest), sorted(f"/Items/Latest?parentId={v}&limit=50&fields=" + tdv.ATV_FIELDS + "&imageTypeLimit=1&groupItems=true" for v in ("v2", "hidden")))


if __name__ == "__main__":
    unittest.main()
