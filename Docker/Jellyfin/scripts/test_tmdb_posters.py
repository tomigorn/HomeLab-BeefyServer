import base64
import contextlib
import datetime
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.parse

from PIL import Image

_spec = importlib.util.spec_from_file_location("tmdb_posters", pathlib.Path(__file__).with_name("tmdb-posters.py"))
tp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tp)

NOW = datetime.datetime(2026, 11, 19, 15, 0, tzinfo=datetime.timezone.utc)
FOLDERS = [{"Name": "Movies", "ItemId": "lm"}, {"Name": "Shows", "ItemId": "ls"}, {"Name": "Trending items", "ItemId": "lt"}]


def jpeg(colour, size=(200, 300), stripe=None):
    im = Image.new("RGB", size, colour)
    if stripe:
        for x in range(size[0] // 2):
            for y in range(size[1]):
                im.putpixel((x, y), stripe)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


DARK = jpeg((10, 10, 12))
LIGHT = jpeg((240, 230, 220))
SPLIT = jpeg((240, 230, 220), stripe=(10, 10, 12))
DARK_SMALL = jpeg((10, 10, 12), (100, 150))


def retitled():
    im = Image.open(io.BytesIO(LIGHT)).convert("RGB")
    for x in range(40, 160):
        for y in range(250, 258):
            im.putpixel((x, y), (10, 10, 12))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


def movie(iid, tmdb, lib="lm", name="Film", year=2011, tag="t1"):
    return {"Id": iid, "Name": name, "Type": "Movie", "ProductionYear": year, "ProviderIds": {"Tmdb": str(tmdb)} if tmdb else {}, "ImageTags": {"Primary": tag} if tag else {}, "_lib": lib}


class FakeIo:
    def __init__(self, items, seerr, images, tmdb, remote=None):
        self.items = {i["Id"]: i for i in items}
        self.seerr_data, self.images, self.tmdb, self.remote = seerr, images, tmdb, remote or {}
        self.calls, self.seerr_calls, self.fetched = [], [], []
        self.n = 0

    def jf(self, method, path, body=None):
        self.calls.append((method, path))
        u = urllib.parse.urlsplit(path)
        if method == "DELETE":
            return None
        q = dict(urllib.parse.parse_qsl(u.query))
        if path == "/Library/VirtualFolders":
            return FOLDERS
        if u.path == "/Items" and "ParentId" in q:
            types = q["IncludeItemTypes"].split(",")
            collapsed = q.get("CollapseBoxSetItems") != "false"
            return {"Items": [{k: v for k, v in i.items() if not k.startswith("_")} for i in self.items.values() if i["_lib"] == q["ParentId"] and i["Type"] in types and not (collapsed and i.get("_boxed"))]}
        if u.path == "/Items" and "Ids" in q:
            return {"Items": [self.items[x] for x in q["Ids"].split(",") if x in self.items]}
        if u.path.endswith("/RemoteImages/Download"):
            iid = u.path.split("/")[2]
            assert method == "POST" and q["Type"] == "Primary"
            self.images[iid] = self.tmdb[urllib.parse.urlsplit(q["ImageUrl"]).path.rsplit("/", 1)[1]]
            self.n += 1
            self.items[iid]["ImageTags"]["Primary"] = f"new{self.n}"
            return None
        if u.path.endswith("/RemoteImages"):
            assert q.get("IncludeAllLanguages") == "true"
            entries = [e if isinstance(e, tuple) else (e, "en") for e in self.remote.get(u.path.split("/")[2], [])]
            return {"Images": [{"Url": "https://image.tmdb.org/t/p/original/" + f, "ProviderName": "TheMovieDb", "Language": lang} for f, lang in entries]}
        raise AssertionError(path)

    def jf_raw(self, method, path, data, content_type):
        self.calls.append((method, path))
        iid = path.split("/")[2]
        if method == "GET":
            if iid not in self.images:
                raise urllib.error.HTTPError(path, 404, "nf", None, None)
            return self.images[iid]
        assert path.split("?")[0].endswith("/Images/Primary")
        self.images[iid] = base64.b64decode(data)
        self.n += 1
        self.items[iid].setdefault("ImageTags", {})["Primary"] = f"up{self.n}"
        return b""

    def seerr(self, path):
        self.seerr_calls.append(path)
        u = urllib.parse.urlsplit(path)
        kind, tmdb = u.path.split("/")[3:5]
        assert dict(urllib.parse.parse_qsl(u.query))["language"] == "en"
        return self.seerr_data[f"{kind}/{tmdb}"]

    def fetch(self, url):
        self.fetched.append(url)
        return self.tmdb[url.rsplit("/", 1)[1]]

    def sleep(self, s):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        (root / "originals").mkdir()
        self.paths = tp.Paths(state=root / "state.json", store=root / "store", originals=root / "originals", trending_state=root / "trending.json", lock=root / "lock")

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, io_, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tp.main(list(argv), io=io_, paths=self.paths, now=NOW)
        return code, out.getvalue()

    def drive(self, current=LIGHT, **kw):
        return FakeIo([movie("m1", 64690, name="Drive", **kw)], {"movie/64690": {"posterPath": "/primary.jpg"}}, {"m1": current} if current else {},
                      {"primary.jpg": DARK, "rated.jpg": LIGHT}, {"m1": ["rated.jpg", "primary.jpg"]})

    def posts(self, io_):
        return [p for m, p in io_.calls if m == "POST"]


class TestSame(unittest.TestCase):
    def test_same_poster_at_another_size_is_same(self):
        self.assertTrue(tp.same(DARK, DARK_SMALL))

    def test_other_poster_is_not_same(self):
        self.assertFalse(tp.same(DARK, LIGHT))
        self.assertFalse(tp.same(LIGHT, SPLIT))

    def test_same_art_with_other_title_text_is_not_same(self):
        self.assertFalse(tp.same(LIGHT, retitled()))

    def test_undecodable_is_not_same(self):
        self.assertFalse(tp.same(b"nope", DARK))


class TestDryRun(Base):
    def test_lists_change_with_current_file_and_changes_nothing(self):
        io_ = self.drive()
        code, out = self.run_main(io_, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Drive (2011): rated.jpg → primary.jpg", out)
        self.assertIn("1 checked, 1 would change, 0 skipped", out)
        self.assertEqual(self.posts(io_), [])
        self.assertFalse((self.paths.store / "backup").exists())


class TestApply(Base):
    def test_downloads_tmdb_primary_original_through_jellyfin(self):
        io_ = self.drive()
        code, out = self.run_main(io_)
        self.assertEqual(code, 0)
        self.assertEqual(self.posts(io_), ["/Items/m1/RemoteImages/Download?Type=Primary&ImageUrl=" + urllib.parse.quote("https://image.tmdb.org/t/p/original/primary.jpg", safe="")])
        self.assertIn("1 checked, 1 changed, 0 skipped", out)

    def test_old_poster_is_backed_up_once(self):
        io_ = self.drive()
        self.run_main(io_)
        self.assertEqual((self.paths.store / "backup" / "m1.jpg").read_bytes(), LIGHT)
        io_.images["m1"] = SPLIT
        io_.items["m1"]["ImageTags"]["Primary"] = "picked-again"
        self.run_main(io_)
        self.assertEqual((self.paths.store / "backup" / "m1.jpg").read_bytes(), LIGHT)

    def test_poster_already_tmdb_primary_is_skipped(self):
        io_ = self.drive(current=DARK_SMALL)
        code, out = self.run_main(io_)
        self.assertEqual(self.posts(io_), [])
        self.assertIn("1 checked, 0 changed, 1 skipped", out)

    def test_second_run_skips_by_image_tag_without_downloads(self):
        io_ = self.drive()
        self.run_main(io_)
        io_.calls, io_.fetched, io_.seerr_calls = [], [], []
        code, out = self.run_main(io_)
        self.assertEqual(self.posts(io_), [])
        self.assertFalse([p for m, p in io_.calls if "/Images/Primary" in p])
        self.assertEqual(io_.seerr_calls, [])
        self.assertIn("0 changed, 1 skipped", out)

    def test_tmdb_answer_is_cached_seven_days(self):
        io_ = self.drive()
        self.run_main(io_)
        io_.items["m1"]["ImageTags"]["Primary"] = "other"
        io_.seerr_calls = []
        self.run_main(io_)
        self.assertEqual(io_.seerr_calls, [])
        tp.main(["--dry-run"], io=io_, paths=self.paths, now=NOW + datetime.timedelta(days=8), out=io.StringIO())
        self.assertEqual(len(io_.seerr_calls), 1)

    def test_only_primary_images_are_written(self):
        io_ = FakeIo([movie("m1", 1), {"Id": "s1", "Name": "Show", "Type": "Series", "ProviderIds": {"Tmdb": "2"}, "ImageTags": {"Primary": "a"}, "_lib": "ls"}],
                     {"movie/1": {"posterPath": "/a.jpg"}, "tv/2": {"posterPath": "/b.jpg", "seasons": []}}, {"m1": LIGHT, "s1": LIGHT}, {"a.jpg": DARK, "b.jpg": DARK})
        self.run_main(io_)
        posts = self.posts(io_)
        self.assertEqual(len(posts), 2)
        self.assertTrue(all("Type=Primary" in p for p in posts))

    def test_items_without_tmdb_id_or_poster_or_outside_the_libraries_are_left(self):
        io_ = FakeIo([movie("m1", None), movie("m2", 5), movie("m3", 6, lib="lt"), dict(movie("m4", 7), Type="BoxSet")],
                     {"movie/5": {"posterPath": None}, "movie/6": {"posterPath": "/x.jpg"}}, {"m1": LIGHT, "m2": LIGHT, "m3": LIGHT}, {"x.jpg": DARK})
        code, out = self.run_main(io_)
        self.assertEqual(self.posts(io_), [])
        self.assertIn("2 checked, 0 changed, 2 skipped", out)

    def test_titles_inside_collections_are_included(self):
        io_ = self.drive()
        io_.items["m1"]["_boxed"] = True
        self.run_main(io_)
        self.assertEqual(len(self.posts(io_)), 1)

    def test_tmdb_primary_with_text_in_another_language_is_left_and_listed(self):
        io_ = self.drive()
        io_.remote["m1"] = ["rated.jpg", ("primary.jpg", "ja")]
        code, out = self.run_main(io_)
        self.assertEqual(self.posts(io_), [])
        self.assertIn("Drive (2011): primary.jpg has ja text, kept", out)
        self.assertIn("0 changed, 1 skipped", out)

    def test_textless_tmdb_primary_is_applied(self):
        io_ = self.drive()
        io_.remote["m1"] = [("primary.jpg", None)]
        self.run_main(io_)
        self.assertEqual(len(self.posts(io_)), 1)

    def test_odd_image_path_is_skipped(self):
        io_ = FakeIo([movie("m1", 1)], {"movie/1": {"posterPath": "/../x.jpg"}}, {"m1": LIGHT}, {})
        self.run_main(io_)
        self.assertEqual(self.posts(io_), [])

    def test_item_without_primary_gets_one(self):
        io_ = self.drive(current=None, tag=None)
        io_.items["m1"]["ImageTags"] = {}
        self.run_main(io_)
        self.assertEqual(len(self.posts(io_)), 1)


class TestSeasons(Base):
    def items(self):
        series = {"Id": "s1", "Name": "Show", "Type": "Series", "ProviderIds": {"Tmdb": "2"}, "ImageTags": {"Primary": "a"}, "_lib": "ls", "_boxed": True}
        s1 = {"Id": "se1", "Name": "Season 1", "Type": "Season", "SeriesId": "s1", "IndexNumber": 1, "ImageTags": {"Primary": "b"}, "_lib": "ls"}
        s2 = {"Id": "se2", "Name": "Season 2", "Type": "Season", "SeriesId": "s1", "IndexNumber": 2, "ImageTags": {"Primary": "c"}, "_lib": "ls"}
        return [series, s1, s2]

    def test_season_with_tmdb_season_poster_changes_others_are_left(self):
        io_ = FakeIo(self.items(), {"tv/2": {"posterPath": "/show.jpg", "seasons": [{"seasonNumber": 1, "posterPath": "/s1.jpg"}, {"seasonNumber": 2, "posterPath": None}]}},
                     {"s1": DARK, "se1": LIGHT, "se2": LIGHT}, {"show.jpg": DARK, "s1.jpg": DARK})
        code, out = self.run_main(io_)
        self.assertEqual([p.split("?")[0] for p in self.posts(io_)], ["/Items/se1/RemoteImages/Download"])
        self.assertIn("3 checked, 1 changed, 2 skipped", out)
        self.assertEqual(len(io_.seerr_calls), 1)


class TestListMarkedPosters(Base):
    def test_marked_item_gets_new_kept_original_not_a_jellyfin_upload(self):
        (self.paths.originals / "m1.jpg").write_bytes(LIGHT)
        (self.paths.originals / "m1.badged").write_text("x")
        io_ = self.drive(current=SPLIT)
        code, out = self.run_main(io_)
        self.assertEqual(self.posts(io_), [])
        self.assertEqual((self.paths.originals / "m1.jpg").read_bytes(), DARK)
        self.assertEqual((self.paths.store / "backup" / "m1.original.jpg").read_bytes(), LIGHT)
        self.assertIn("https://image.tmdb.org/t/p/original/primary.jpg", io_.fetched)
        self.assertIn("1 changed", out)

    def test_marked_item_already_on_tmdb_primary_is_skipped(self):
        (self.paths.originals / "m1.jpg").write_bytes(DARK)
        self.paths.trending_state.write_text(json.dumps({"real": {"m1": {}}}))
        io_ = self.drive(current=SPLIT)
        code, out = self.run_main(io_)
        self.assertEqual((self.paths.originals / "m1.jpg").read_bytes(), DARK)
        self.assertIn("0 changed, 1 skipped", out)


class TestRollback(Base):
    def test_rollback_restores_backups_and_stops_later_runs(self):
        (self.paths.originals / "m2.jpg").write_bytes(LIGHT)
        (self.paths.originals / "m2.badged").write_text("x")
        io_ = FakeIo([movie("m1", 1), movie("m2", 2)], {"movie/1": {"posterPath": "/a.jpg"}, "movie/2": {"posterPath": "/a.jpg"}}, {"m1": LIGHT, "m2": SPLIT}, {"a.jpg": DARK})
        self.run_main(io_)
        code, out = self.run_main(io_, "--rollback")
        self.assertEqual(code, 0)
        self.assertEqual(io_.images["m1"], LIGHT)
        self.assertEqual((self.paths.originals / "m2.jpg").read_bytes(), LIGHT)
        self.assertIn("tmdb-posters.py --enable", out)
        io_.calls = []
        code, out = self.run_main(io_)
        self.assertEqual(code, 0)
        self.assertEqual(self.posts(io_), [])
        self.assertIn("--enable", out)
        code, out = self.run_main(io_, "--enable")
        self.assertEqual(code, 0)
        self.run_main(io_)
        self.assertEqual(io_.images["m1"], DARK)


    def test_rollback_removes_a_primary_the_item_did_not_have(self):
        io_ = self.drive(current=None, tag=None)
        io_.items["m1"]["ImageTags"] = {}
        self.run_main(io_)
        self.run_main(io_, "--rollback")
        self.assertIn(("DELETE", "/Items/m1/Images/Primary"), io_.calls)


class TestCheck(Base):
    def test_check_passes_when_primary_is_tmdb_primary(self):
        io_ = self.drive(current=DARK)
        code, out = self.run_main(io_, "--check")
        self.assertEqual(code, 0)
        self.assertTrue(out.strip().endswith("primary ok movie/64690 /primary.jpg"))
        self.assertEqual(self.posts(io_), [])

    def test_check_fails_on_another_poster(self):
        code, out = self.run_main(self.drive(current=LIGHT), "--check")
        self.assertEqual(code, 1)
        self.assertIn("primary differs movie/64690", out)

    def test_check_reads_the_kept_original_of_a_marked_item(self):
        (self.paths.originals / "m1.jpg").write_bytes(DARK)
        (self.paths.originals / "m1.badged").write_text("x")
        code, out = self.run_main(self.drive(current=SPLIT), "--check")
        self.assertEqual(code, 0)


class TestUsage(Base):
    def test_help(self):
        code, out = self.run_main(None, "--help")
        self.assertEqual(code, 0)
        self.assertIn("--dry-run", out)

    def test_unknown_flag_is_usage_error_with_hint(self):
        code, out = self.run_main(None, "--bogus")
        self.assertEqual(code, 2)
        self.assertIn("tmdb-posters.py --help", out)


if __name__ == "__main__":
    unittest.main()
