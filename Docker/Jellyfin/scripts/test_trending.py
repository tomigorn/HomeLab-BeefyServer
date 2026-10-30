import datetime
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import tempfile
import unittest
import urllib.error
import urllib.parse

from PIL import Image

_spec = importlib.util.spec_from_file_location("trending", pathlib.Path(__file__).with_name("trending.py"))
tr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tr)

import trending_model as tm  # noqa: E402

NOW = datetime.datetime(2026, 10, 29, 12, 0, tzinfo=datetime.timezone.utc)
LIB = "lib0"
SHOWS = "shows0"
ADMIN = "admin0"
INJ = "inj0"


def jpeg(color=(10, 20, 30)):
    out = io.BytesIO()
    Image.new("RGB", (40, 60), color).save(out, "JPEG")
    return out.getvalue()


def entry(tid, rank, title, found=True, x=True, d=None):
    return {"k": "movie", "id": tid, "r": rank, "d": d or f"d{tid}", "f": "1080p" if found else None, "x": f"x{tid}" if x and found else None,
            "o": False, "u": False, "s": None, "t": {"en": title, "de": title}, "y": 2026, "p": {"en": f"/p{tid}.jpg"}}


def show(tid, rank, title, found=True):
    return {**entry(tid, rank, title, found), "k": "tv", "s": 1}


def feeds(updated=None):
    stamp = (updated or NOW).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    lists = {"updated": stamp, "home": {"movies": [entry(1, 1, "Alpha"), entry(2, 2, "Beta", found=False), entry(9, 3, "Owned")], "shows": [show(7, 1, "Delta"), show(8, 2, "Epsilon", found=False)]},
             "rows": [{"id": "service-8-movie", "kind": "movie", "name": {"en": "Netflix"}, "logo": "/logo8.png", "items": [entry(2, 1, "Beta", found=False), entry(1, 2, "Alpha")]}]}
    coming = {"updated": stamp, "items": [{"d": "c5", "date": "2026-11-04", "kind": "movie", "pop": 1.0, "ref": "movie/5", "tmdb": 5}]}
    details = {"updated": stamp, "entries": {f"d{i}": {"t": {"en": t}, "p": {"en": f"/p{i}.jpg"}, "b": f"/b{i}.jpg", "ref": f"movie/{i}", "y": 2026, "o": {"en": f"Plot {t}"}}
                                            for i, t in ((1, "Alpha"), (2, "Beta"), (7, "Delta"), (8, "Epsilon"))}}
    comingd = {"updated": stamp, "entries": {"c5": {"t": {"en": "Gamma"}, "p": {"en": "/p5.jpg"}, "ref": "movie/5", "y": 2026}}}
    return lists, coming, details, comingd


def sid(*parts):
    return hashlib.md5("|".join(parts).encode()).hexdigest()


class FakeIo:
    def __init__(self, paths):
        self.paths = paths
        self.calls = []
        self.media = []
        self.library = []
        self.owned = [{"Id": "real9", "Type": "Movie", "ProviderIds": {"Tmdb": "9"}, "Path": "/media/movies/Owned (2026)/Owned.mkv", "ParentId": "movies"}]
        self.sets = {"hp": {"Id": "hp", "Name": "Harry Potter Collection", "Tags": [], "DisplayOrder": "PremiereDate", "LockData": False, "children": ["real9"]}}
        self.injector = {"CustomJavaScripts": [{"Name": "Other", "Script": "x", "Enabled": True, "RequiresAuthentication": True}]}
        self.images = {}
        self.n = 0
        self.fail = set()
        self.on_seerr = None
        self.seerr_calls = 0
        self.shows_id = SHOWS
        self.tv = []

    def scan(self):
        self.scan_movies()
        self.scan_shows()

    def scan_shows(self):
        items = []
        for d in sorted(self.paths.shows.iterdir()):
            if d.is_symlink() or not d.is_dir():
                continue
            nfo = (d / "tvshow.nfo").read_text() if (d / "tvshow.nfo").exists() else ""
            m = re.search(r"<sorttitle>(.*?)</sorttitle>", nfo)
            words = [w for w in (m.group(1) if m else d.name).casefold().replace("-", "").split() if w not in ("the", "a", "an")]
            path = f"{tr.JF_SHOWS}/{d.name}"
            series = {"Id": sid(path), "Type": "Series", "Name": d.name, "Path": path, "ParentId": SHOWS, "ProviderIds": {},
                      "SortName": re.sub(r"\d+", lambda n: n.group().zfill(10), " ".join(words))}
            items.append(series)
            for f in sorted(d.rglob("*.mp4")):
                ep = f"{path}/{f.relative_to(d).as_posix()}"
                items.append({"Id": sid(ep), "Type": "Episode", "Name": f.stem, "Path": ep, "SeriesId": series["Id"], "ParentId": sid(path, "season"), "RunTimeTicks": 120000000, "ProviderIds": {}})
        self.tv = items

    def scan_movies(self):
        items = []
        for d in sorted(self.paths.library.iterdir()):
            if d.is_symlink() or not d.is_dir():
                continue
            files = sorted(f.name for f in d.iterdir() if f.suffix == ".mp4")
            if not files:
                continue
            nfo = (d / "movie.nfo").read_text() if (d / "movie.nfo").exists() else ""
            m = re.search(r"<sorttitle>(.*?)</sorttitle>", nfo)
            words = [w for w in (m.group(1) if m else d.name).casefold().replace("-", "").split() if w not in ("the", "a", "an")]
            sort = re.sub(r"\d+", lambda n: n.group().zfill(10), " ".join(words))
            base = f"{tr.JF_PATH}/{d.name}/"
            items.append({"Id": sid(base + files[0]), "Type": "Movie", "Name": d.name, "Path": base + files[0], "ParentId": LIB, "SortName": sort,
                          "RunTimeTicks": 100000000, "ProviderIds": {}, "MediaSources": [{"Id": sid(base + f), "Path": base + f, "Name": f} for f in files]})
        self.library = items

    def jf(self, method, path, body=None):
        self.calls.append((method, path))
        for pattern in self.fail:
            if re.fullmatch(pattern, f"{method} {path}"):
                raise urllib.error.HTTPError(path, 500, "boom", {}, None)
        u = urllib.parse.urlsplit(path)
        q = dict(urllib.parse.parse_qsl(u.query))
        p = u.path
        if p == "/Library/VirtualFolders":
            return [{"Name": "Movies", "ItemId": "movies"}, {"Name": tm.LIBRARY, "ItemId": LIB}, {"Name": tm.SHOWS_LIBRARY, **({"ItemId": self.shows_id} if self.shows_id else {})}]
        if p == "/Users":
            return [{"Id": "u1", "Policy": {"IsAdministrator": False}}, {"Id": ADMIN, "Policy": {"IsAdministrator": True}}]
        if p == "/Plugins":
            return [{"Name": "JavaScript Injector", "Id": INJ}]
        if p == f"/Plugins/{INJ}/Configuration":
            if method == "GET":
                return json.loads(json.dumps(self.injector))
            self.injector = body
            return None
        if p == f"/Items/{LIB}/Refresh":
            self.scan_movies()
            return None
        if p == f"/Items/{SHOWS}/Refresh":
            self.scan_shows()
            return None
        if re.fullmatch(r"/Items/\w+/Refresh", p):
            return None
        if p == "/Items" and q.get("Recursive") == "true" and q.get("IncludeItemTypes") != "BoxSet":
            assert q.get("CollapseBoxSetItems") == "false", path
        if p == "/Items" and q.get("IncludeItemTypes") == "BoxSet":
            return {"Items": [{k: v for k, v in s.items() if k != "children"} for s in self.sets.values()]}
        if p == "/Items" and q.get("ParentId") == LIB:
            return {"Items": json.loads(json.dumps(self.library))}
        if p == "/Items" and q.get("ParentId") == SHOWS:
            assert q.get("IncludeItemTypes") == "Series,Episode", path
            return {"Items": json.loads(json.dumps(self.tv))}
        if p == "/Items" and q.get("ParentId") in self.sets:
            return {"Items": [{"Id": i} for i in self.sets[q["ParentId"]]["children"]]}
        if p == "/Items" and q.get("IncludeItemTypes") == "Movie,Series":
            return {"Items": self.owned + json.loads(json.dumps(self.library + [i for i in self.tv if i["Type"] == "Series"]))}
        if p == "/Collections" and method == "POST":
            self.n += 1
            new = f"set{self.n}"
            self.sets[new] = {"Id": new, "Name": q["name"], "Tags": [], "DisplayOrder": "PremiereDate", "LockData": False, "children": [i for i in q.get("ids", "").split(",") if i]}
            return {"Id": new}
        m = re.fullmatch(r"/Collections/(\w+)/Items", p)
        if m:
            ids = q["ids"].split(",")
            ch = self.sets[m.group(1)]["children"]
            if method == "POST":
                ch.extend(i for i in ids if i not in ch)
            else:
                ch[:] = [i for i in ch if i not in ids]
            return None
        m = re.fullmatch(r"/Items/(\w+)", p)
        if m and m.group(1) in self.sets:
            s = self.sets[m.group(1)]
            if method == "GET":
                return {k: v for k, v in s.items() if k != "children"} | {"Overview": None}
            s.update({k: v for k, v in body.items() if k in ("Name", "Tags", "ForcedSortName", "DisplayOrder", "LockData")})
            return None
        if m and method == "GET":
            raise urllib.error.HTTPError(path, 404, "not found", {}, None)
        raise AssertionError(f"unexpected {method} {path}")

    def jf_raw(self, method, path, data, content_type):
        self.calls.append((method, path))
        self.images[path] = data
        return b""

    def seerr_media(self):
        self.seerr_calls += 1
        if self.on_seerr:
            self.on_seerr(self.seerr_calls)
        return self.media

    def fetch(self, url):
        h = hashlib.md5(url.encode()).digest()
        return jpeg((h[0], h[1], h[2]))

    def injector_lock(self):
        import contextlib
        return contextlib.nullcontext()

    def sleep(self, s):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.paths = tr.Paths(feeds=root / "feeds", root=root / "trending", state=root / "state" / "trending.json", lock=root / "state" / "trending.lock",
                              request_state=root / "request-state.json", signal=root / "signal" / "requested")
        for d in ("library", "shows", "clips", "cache"):
            (self.paths.root / d).mkdir(parents=True)
        self.paths.feeds.mkdir()
        for c in tm.CLIPS:
            (self.paths.clips / f"{c}.mp4").write_bytes(c.encode() * 10)
        self.write_feeds(*feeds())
        self.paths.request_state.write_text(json.dumps({"updated": NOW.isoformat(), "canWait": []}))
        self.io = FakeIo(self.paths)

    def tearDown(self):
        self.tmp.cleanup()

    def write_feeds(self, lists, coming, details, comingd):
        for name, data in (("lists.json", lists), ("coming-list.json", coming), ("details.json", details), ("coming.json", comingd)):
            (self.paths.feeds / name).write_text(json.dumps(data))

    def run_sync(self, *argv):
        self.io.calls = []
        out = io.StringIO()
        rc = tr.main(list(argv), io=self.io, paths=self.paths, now=NOW, out=out)
        self.out = out.getvalue()
        return rc

    def folder(self, title):
        return self.paths.library / f"{title} (2026)"

    def writes(self):
        return [c for c in self.io.calls if c[0] != "GET"]

    def item(self, title):
        return next(i for i in self.io.library if i["Name"] == f"{title} (2026)")

    def show_dir(self, title):
        return self.paths.shows / f"{title} (2026)"

    def series(self, title):
        return next(i for i in self.io.tv if i["Type"] == "Series" and i["Name"] == f"{title} (2026)")

    def episodes(self, title):
        s = self.series(title)
        return {i["Path"].rsplit("/", 1)[1]: i["Id"] for i in self.io.tv if i.get("SeriesId") == s["Id"]}


class Desired(unittest.TestCase):
    class NoImages:
        def get(self, size, path):
            return None

    def test_poster_carries_the_rank_and_unranked_posters_are_unchanged(self):
        lists = {"home": {"movies": [entry(1, 4, "Ranked")], "shows": []},
                 "rows": [{"id": "service-8-movie", "kind": "movie", "type": "service", "name": {"en": "Netflix", "de": "Netflix"}, "logo": None, "items": [entry(2, 1, "Plain")]}]}
        p = tm.plan(lists, {"items": []}, {}, {}, {})
        by = {ph.title: ph for ph in p.placeholders.values()}
        ranked, plain = by["Ranked"], by["Plain"]
        self.assertEqual(tr.desired(ranked, self.NoImages())["poster.jpg"], tr.art.poster(None, ranked.badge, "Ranked", 4))
        self.assertNotEqual(tr.desired(ranked, self.NoImages())["poster.jpg"], tr.art.poster(None, ranked.badge, "Ranked"))
        self.assertEqual(tr.desired(plain, self.NoImages())["poster.jpg"], tr.art.poster(None, plain.badge, "Plain"))


class SyncTest(Base):
    def test_writes_folder_files_and_hardlinks(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        a = self.folder("Alpha")
        for f in ("movie.nfo", "poster.jpg", "fanart.jpg"):
            self.assertTrue((a / f).is_file(), f)
        self.assertIn("<sorttitle>01-01 Alpha</sorttitle>", (a / "movie.nfo").read_text())
        now, night = a / "Alpha (2026) - ⬇ Download movie now.mp4", a / "Alpha (2026) - 🌙 Download movie tonight · lower power cost.mp4"
        self.assertEqual(os.stat(now).st_ino, os.stat(self.paths.clips / "now.mp4").st_ino)
        self.assertEqual(os.stat(night).st_ino, os.stat(self.paths.clips / "night.mp4").st_ino)
        b = self.folder("Beta")
        self.assertEqual([p.name for p in b.glob("*.mp4")], ["Beta (2026).mp4"])
        self.assertEqual(os.stat(b / "Beta (2026).mp4").st_ino, os.stat(self.paths.clips / "unavailable.mp4").st_ino)
        self.assertEqual(os.stat(self.folder("Gamma") / "Gamma (2026).mp4").st_ino, os.stat(self.paths.clips / "coming.mp4").st_ino)
        self.assertFalse((self.paths.library / "Owned (2026)").exists())
        self.assertEqual(oct(os.stat(a / "movie.nfo").st_mode & 0o777), "0o644")
        self.assertNotIn("sort title not applied", self.out)

    def test_second_run_changes_nothing(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        before = {p: p.stat().st_mtime_ns for p in self.paths.library.rglob("*")}
        index = self.paths.index.stat().st_mtime_ns
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual({p: p.stat().st_mtime_ns for p in self.paths.library.rglob("*")}, before)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.paths.index.stat().st_mtime_ns, index)
        self.assertIn("nothing changed", self.out)

    def test_state_change_relinks_and_refreshes_library(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        self.io.media = [{"mediaType": "movie", "tmdbId": 1, "status": 3, "requests": []}]
        self.assertEqual(self.run_sync(), 0, self.out)
        a = self.folder("Alpha")
        self.assertEqual([p.name for p in a.glob("*.mp4")], ["Alpha (2026).mp4"])
        self.assertEqual(os.stat(a / "Alpha (2026).mp4").st_ino, os.stat(self.paths.clips / "requested.mp4").st_ino)
        self.assertIn(tm.PREFIX["now"], (a / "movie.nfo").read_text())
        refresh = [c for c in self.io.calls if c[0] == "POST" and c[1].startswith(f"/Items/{LIB}/Refresh?Recursive=true")]
        self.assertEqual(len(refresh), 1)

    def test_requested_tonight_and_downloading(self):
        self.paths.request_state.write_text(json.dumps({"updated": NOW.isoformat(), "canWait": ["movie/1"], "queue": {"movie/2": {"state": "downloading"}}}))
        self.io.media = [{"mediaType": "movie", "tmdbId": 1, "status": 3, "requests": []}, {"mediaType": "movie", "tmdbId": 2, "status": 3, "requests": []}]
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertIn(tm.PREFIX["night"], (self.folder("Alpha") / "movie.nfo").read_text())
        self.assertIn(tm.PREFIX["now"], (self.folder("Beta") / "movie.nfo").read_text())

    def test_nfo_only_change_refreshes_item_not_library(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        lists, coming, details, comingd = feeds()
        details["entries"]["d1"]["o"]["en"] = "A new plot"
        self.write_feeds(lists, coming, details, comingd)
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertIn("A new plot", (self.folder("Alpha") / "movie.nfo").read_text())
        posts = [c[1] for c in self.io.calls if c[0] == "POST" and "/Refresh" in c[1]]
        self.assertEqual(posts, [f"/Items/{self.item('Alpha')['Id']}/Refresh?MetadataRefreshMode=FullRefresh&ImageRefreshMode=FullRefresh&ReplaceAllMetadata=true&ReplaceAllImages=true"])

    def test_new_clip_inode_relinks(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        clip = self.paths.clips / "unavailable.mp4"
        new = self.paths.clips / ".new"
        new.write_bytes(b"rebuilt")
        os.replace(new, clip)
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual(os.stat(self.folder("Beta") / "Beta (2026).mp4").st_ino, os.stat(clip).st_ino)
        self.assertFalse(any(c[1].startswith(f"/Items/{LIB}/Refresh") for c in self.io.calls))
        self.assertTrue(any(c[1].startswith(f"/Items/{self.item('Beta')['Id']}/Refresh") for c in self.io.calls))

    def test_removes_only_own_folders(self):
        outside = pathlib.Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        (self.paths.library / "link").symlink_to(outside, target_is_directory=True)
        old = self.paths.library / "Old (2000)"
        old.mkdir()
        (old / "movie.nfo").write_text("<movie/>")
        (old / "Old (2000).mp4").hardlink_to(self.paths.clips / "coming.mp4")
        clips = {p.name: (p.stat().st_ino, p.read_bytes()) for p in self.paths.clips.iterdir()}
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertFalse(old.exists())
        self.assertTrue((self.paths.library / "link").is_symlink())
        self.assertEqual((outside / "keep.txt").read_text(), "keep")
        self.assertEqual({p.name: (p.stat().st_ino, p.read_bytes()) for p in self.paths.clips.iterdir()}, clips)

    def test_collections_created_tagged_sorted_and_membership_diffed(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        creates = [c[1] for c in self.io.calls if c[0] == "POST" and c[1].startswith("/Collections?")]
        self.assertEqual(len(creates), 4)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(creates[0]).query))
        self.assertEqual(q["name"], "Top 10 Switzerland · Movies")
        self.assertEqual(q["isLocked"], "true")
        self.assertEqual(q["ids"].split(","), [self.item("Alpha")["Id"], self.item("Beta")["Id"], "real9"])
        specs = tm.collection_specs(json.loads((self.paths.feeds / "lists.json").read_text()))
        tagged = {s["Name"]: s for s in self.io.sets.values() if tm.TAG in s["Tags"]}
        self.assertEqual(sorted(tagged), sorted(s["name"] for s in specs))
        for spec in specs:
            s = tagged[spec["name"]]
            self.assertEqual((s["ForcedSortName"], s["DisplayOrder"], s["LockData"]), (spec["sort"], "SortName", True))
            self.assertIn(f"/Items/{s['Id']}/Images/Primary", self.io.images)
            self.assertIn(f"/Items/{s['Id']}/Images/Backdrop/0", self.io.images)
        hp = self.io.sets["hp"]
        self.assertEqual((hp["Tags"], hp["children"], hp["DisplayOrder"]), ([], ["real9"], "PremiereDate"))
        self.assertFalse(any("/hp" in c[1] or "=hp" in c[1] for c in self.io.calls))

        lists, coming, details, comingd = feeds()
        lists["home"]["movies"] = [entry(1, 1, "Alpha"), entry(9, 2, "Owned"), entry(5, 3, "Gamma")]
        self.write_feeds(lists, coming, details, comingd)
        self.assertEqual(self.run_sync(), 0, self.out)
        home = tagged["Top 10 Switzerland · Movies"]["Id"]
        self.assertFalse(any(c[1].startswith("/Collections?") for c in self.io.calls))
        changes = [c for c in self.io.calls if c[1].startswith(f"/Collections/{home}/Items")]
        self.assertEqual({c[0] for c in changes}, {"POST", "DELETE"})
        self.assertIn(("POST", f"/Collections/{home}/Items?ids={self.item('Gamma')['Id']}"), changes)
        self.assertEqual(sorted(self.io.sets[home]["children"]), sorted([self.item("Alpha")["Id"], "real9", self.item("Gamma")["Id"]]))
        uploads = sorted(c[1] for c in self.io.calls if c[1].startswith("/Items/") and "/Images/" in c[1])
        self.assertEqual(uploads, [f"/Items/{home}/Images/Backdrop/0", f"/Items/{home}/Images/Primary"])

        self.paths.state.unlink()
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertFalse(any(c[1].startswith("/Collections?") for c in self.io.calls))
        self.assertEqual(len([s for s in self.io.sets.values() if tm.TAG in s["Tags"]]), 4)

    def test_hide_entry_lists_library_sets_and_placeholders(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        entry_ = next(s for s in self.io.injector["CustomJavaScripts"] if s["Name"] == "Trending hidden")
        self.assertTrue(entry_["Script"].startswith("window.HssTrendingHide = "))
        hide = json.loads(entry_["Script"][len("window.HssTrendingHide = "):].rstrip().rstrip(";"))
        self.assertEqual(hide["lib"], [LIB, SHOWS])
        self.assertEqual(sorted(hide["sets"]), sorted(s["Id"] for s in self.io.sets.values() if tm.TAG in s["Tags"]))
        self.assertIn(self.item("Alpha")["Id"], hide["items"])
        self.assertNotIn("real9", hide["items"])
        self.assertTrue(any(s["Name"] == "Other" for s in self.io.injector["CustomJavaScripts"]))

    def test_index_maps_primary_and_version_ids_to_mode(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        idx = json.loads(self.paths.index.read_text())
        self.assertEqual(oct(self.paths.index.stat().st_mode & 0o777), "0o644")
        self.assertNotIn("library", idx)
        self.assertNotIn("runtimeTicks", idx)
        a = self.item("Alpha")
        rec = idx["items"][a["Id"]]
        by_name = {ms["Path"].rsplit("/", 1)[1]: ms["Id"] for ms in a["MediaSources"]}
        self.assertEqual(rec["x"], "x1")
        self.assertEqual(rec["t"], "Alpha")
        self.assertEqual(rec["rt"], 100000000)
        self.assertEqual(rec["modes"], {by_name["Alpha (2026) - ⬇ Download movie now.mp4"]: "now", by_name["Alpha (2026) - 🌙 Download movie tonight · lower power cost.mp4"]: "night"})
        for ms in a["MediaSources"]:
            self.assertEqual(idx["items"][ms["Id"]], rec)
        self.assertEqual(idx["items"][self.item("Beta")["Id"]], {"x": None, "t": "Beta", "modes": {}, "rt": 100000000})

    def test_stale_inputs_keep_published_state(self):
        self.write_feeds(*feeds(updated=NOW - datetime.timedelta(days=10)))
        self.assertEqual(self.run_sync(), 1)
        self.assertEqual(list(self.paths.library.iterdir()), [])
        self.assertFalse(self.paths.index.exists())
        self.assertEqual(self.writes(), [])

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.run_sync("--dry-run"), 0, self.out)
        self.assertEqual(list(self.paths.library.iterdir()), [])
        self.assertEqual(list(self.paths.shows.iterdir()), [])
        self.assertEqual(list(self.paths.cache.iterdir()), [])
        self.assertFalse(self.paths.index.exists())
        self.assertFalse(self.paths.state.exists())
        self.assertEqual(self.writes(), [])
        self.assertIn("would", self.out)

    def test_alternate_version_items_do_not_shadow_the_primary(self):
        base = f"{tr.JF_PATH}/A (2026)/"
        primary = {"Id": "p", "Path": base + "A (2026) - x.mp4", "MediaSources": [{"Id": "p", "Path": base + "A (2026) - x.mp4"}, {"Id": "v", "Path": base + "A (2026) - y.mp4"}]}
        alt = {"Id": "v", "Path": base + "A (2026) - y.mp4", "MediaSources": [{"Id": "v", "Path": base + "A (2026) - y.mp4"}]}
        self.assertEqual(tr.by_folder([primary, alt])["A (2026)"]["Id"], "p")
        self.assertEqual(tr.by_folder([alt, primary])["A (2026)"]["Id"], "p")

    def test_sort_key_matches_jellyfin_sort_names(self):
        self.assertEqual(tr.sort_key("05-04 The Crown"), tr.sort_key("0000000504 crown"))
        self.assertEqual(tr.sort_key("01-01 Animals"), tr.sort_key("0000000101 animals"))
        self.assertEqual(tr.sort_key("12-03 Opération Commando"), tr.sort_key("0000001203 operation commando"))
        self.assertNotEqual(tr.sort_key("01-02 Animals"), tr.sort_key("0000000101 animals"))

    def test_failed_tag_update_keeps_the_new_collection(self):
        self.io.fail = {r"POST /Items/set1"}
        self.assertEqual(self.run_sync(), 1, self.out)
        self.assertEqual(json.loads(self.paths.state.read_text())["sets"], {"home-movies": "set1"})
        self.io.fail = set()
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual(len([c for c in self.io.calls if c[1].startswith("/Collections?")]), 3)
        self.assertEqual(len([s for s in self.io.sets.values() if tm.TAG in s["Tags"]]), 4)
        self.assertEqual(len(self.io.sets), 5)
        hide = next(s for s in self.io.injector["CustomJavaScripts"] if s["Name"] == "Trending hidden")["Script"]
        self.assertIn('"set1"', hide)

    def test_pending_item_refresh_survives_a_failed_run(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        lists, coming, details, comingd = feeds()
        details["entries"]["d1"]["o"]["en"] = "A new plot"
        self.write_feeds(lists, coming, details, comingd)
        self.io.fail = {r"POST /Items/\w+/Refresh\?Metadata.*"}
        self.assertEqual(self.run_sync(), 1, self.out)
        self.io.fail = set()
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertIn(("POST", f"/Items/{self.item('Alpha')['Id']}/Refresh?{tr.ITEM_REFRESH}"), self.io.calls)
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual(self.writes(), [])

    def test_pending_library_refresh_survives_a_failed_run(self):
        self.io.fail = {rf"POST /Items/{LIB}/Refresh\?.*"}
        self.assertEqual(self.run_sync(), 1, self.out)
        self.assertEqual(json.loads(self.paths.state.read_text())["pending"]["libs"], ["movie", "tv"])
        self.io.fail = set()
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual(len([c for c in self.io.calls if c[1].startswith(f"/Items/{LIB}/Refresh")]), 1)
        self.assertNotIn("pending", json.loads(self.paths.state.read_text()))

    def test_pending_from_the_old_state_format_refreshes_the_film_library(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        state = json.loads(self.paths.state.read_text())
        state["pending"] = {"library": True, "items": []}
        self.paths.state.write_text(json.dumps(state))
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual([c[1] for c in self.io.calls if "/Refresh" in c[1]], [f"/Items/{LIB}/Refresh?{tr.LIB_REFRESH}"])
        self.assertNotIn("pending", json.loads(self.paths.state.read_text()))

    def test_no_placeholders_refuses_and_keeps_folders(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        lists, coming, details, comingd = feeds()
        lists["home"] = {"movies": [], "shows": []}
        lists["rows"][0]["items"] = []
        coming["items"] = []
        self.write_feeds(lists, coming, details, comingd)
        self.assertEqual(self.run_sync(), 1, self.out)
        self.assertTrue(self.folder("Alpha").is_dir())
        self.assertEqual(self.writes(), [])

    def test_signal_during_run_runs_once_more(self):
        self.paths.signal.parent.mkdir()
        self.paths.signal.write_text("")

        def touch(n):
            t = __import__("time").time() + 5
            os.utime(self.paths.signal, (t, t))

        self.io.on_seerr = touch
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual(self.io.seerr_calls, 2)

    def test_old_signal_runs_once(self):
        self.paths.signal.parent.mkdir()
        self.paths.signal.write_text("")
        os.utime(self.paths.signal, (1, 1))
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual(self.io.seerr_calls, 1)

    def test_series_placeholder_is_a_show_with_specials(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        d = self.show_dir("Delta")
        self.assertFalse((self.paths.library / "Delta (2026)").exists())
        for f in ("tvshow.nfo", "poster.jpg", "fanart.jpg"):
            self.assertTrue((d / f).is_file(), f)
        self.assertFalse((d / "movie.nfo").exists())
        nfo = (d / "tvshow.nfo").read_text()
        self.assertIn("<sorttitle>02-01 Delta</sorttitle>", nfo)
        self.assertIn(f"<dateadded>{tm.DATE_ADDED}</dateadded>", nfo)
        season = d / "Season 00"
        self.assertEqual(sorted(p.name for p in season.iterdir()), ["S00E01 - ⬇ Download series now.mp4", "S00E01 - ⬇ Download series now.nfo",
                                                                    "S00E02 - 🌙 Download series tonight · lower power cost.mp4", "S00E02 - 🌙 Download series tonight · lower power cost.nfo"])
        self.assertEqual(os.stat(season / "S00E01 - ⬇ Download series now.mp4").st_ino, os.stat(self.paths.clips / "now.mp4").st_ino)
        self.assertEqual(os.stat(season / "S00E02 - 🌙 Download series tonight · lower power cost.mp4").st_ino, os.stat(self.paths.clips / "night.mp4").st_ino)
        self.assertIn("<title>⬇ Download series now</title>", (season / "S00E01 - ⬇ Download series now.nfo").read_text())
        e = self.show_dir("Epsilon") / "Season 00"
        self.assertEqual(sorted(p.name for p in e.iterdir()), ["S00E01.mp4", "S00E01.nfo"])
        self.assertEqual(os.stat(e / "S00E01.mp4").st_ino, os.stat(self.paths.clips / "unavailable.mp4").st_ino)
        self.assertEqual(len([c for c in self.io.calls if c[1].startswith(f"/Items/{SHOWS}/Refresh?Recursive=true")]), 1)

    def test_series_index_maps_episodes_to_modes(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        idx = json.loads(self.paths.index.read_text())
        eps = self.episodes("Delta")
        now, night = eps["S00E01 - ⬇ Download series now.mp4"], eps["S00E02 - 🌙 Download series tonight · lower power cost.mp4"]
        rec = {"x": "x7", "t": "Delta", "modes": {now: "now", night: "night"}, "rt": 120000000}
        self.assertEqual((idx["items"][now], idx["items"][night]), (rec, rec))
        self.assertNotIn(self.series("Delta")["Id"], idx["items"])
        (single,) = self.episodes("Epsilon").values()
        self.assertEqual(idx["items"][single], {"x": None, "t": "Epsilon", "modes": {}, "rt": 120000000})

    def test_series_collections_hide_and_owned(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        home = next(s for s in self.io.sets.values() if s["Name"] == "Top 10 Switzerland · Shows")
        self.assertEqual(home["children"], [self.series("Delta")["Id"], self.series("Epsilon")["Id"]])
        hide = next(s for s in self.io.injector["CustomJavaScripts"] if s["Name"] == "Trending hidden")["Script"]
        for i in [self.series("Delta")["Id"], *self.episodes("Delta").values()]:
            self.assertIn(f'"{i}"', hide)
        self.io.tv[0]["ProviderIds"] = {"Tmdb": "7"}
        self.assertNotIn("tv/7", tr.owned(self.io, LIB))

    def test_second_run_with_series_changes_nothing(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        before = {p: p.stat().st_mtime_ns for p in self.paths.shows.rglob("*")}
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual({p: p.stat().st_mtime_ns for p in self.paths.shows.rglob("*")}, before)
        self.assertEqual(self.writes(), [])

    def test_series_requested_restructures_and_refreshes_the_series(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        self.io.media = [{"mediaType": "tv", "tmdbId": 7, "status": 3, "seasons": [{"seasonNumber": 1, "status": 3}], "requests": []}]
        self.assertEqual(self.run_sync(), 0, self.out)
        season = self.show_dir("Delta") / "Season 00"
        self.assertEqual(sorted(p.name for p in season.iterdir()), ["S00E01.mp4", "S00E01.nfo"])
        self.assertEqual(os.stat(season / "S00E01.mp4").st_ino, os.stat(self.paths.clips / "requested.mp4").st_ino)
        self.assertIn(tm.PREFIX["now"], (self.show_dir("Delta") / "tvshow.nfo").read_text())
        self.assertIn(("POST", f"/Items/{SHOWS}/Refresh?{tr.LIB_REFRESH}"), self.io.calls)
        self.assertIn(("POST", f"/Items/{self.series('Delta')['Id']}/Refresh?{tr.ITEM_REFRESH}"), self.io.calls)
        self.assertFalse(any(c[1].startswith(f"/Items/{LIB}/Refresh") for c in self.io.calls))
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual(self.writes(), [])

    def test_migrates_movie_type_series_placeholders(self):
        old = self.paths.library / "Delta (2026)"
        old.mkdir()
        (old / "movie.nfo").write_text("<movie><sorttitle>02-01 Delta</sorttitle></movie>")
        (old / "Delta (2026) - ⬇ Download series now.mp4").hardlink_to(self.paths.clips / "now.mp4")
        self.io.scan()
        old_id = self.item("Delta")["Id"]
        self.io.sets["oldset"] = {"Id": "oldset", "Name": "Top 10 Switzerland · Shows", "Tags": [tm.TAG], "ForcedSortName": "02 Top 10 Switzerland · Shows",
                                  "DisplayOrder": "SortName", "LockData": True, "children": [old_id]}
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertFalse(old.exists())
        self.assertEqual(self.io.sets["oldset"]["children"], [self.series("Delta")["Id"], self.series("Epsilon")["Id"]])
        self.assertNotIn(old_id, [i["Id"] for i in self.io.library])
        calls = self.io.calls
        add = calls.index(("POST", f"/Collections/oldset/Items?ids={self.series('Delta')['Id']},{self.series('Epsilon')['Id']}"))
        lib_refreshes = [i for i, c in enumerate(calls) if c[1].startswith(f"/Items/{LIB}/Refresh")]
        shows_refresh = next(i for i, c in enumerate(calls) if c[1].startswith(f"/Items/{SHOWS}/Refresh"))
        self.assertLess(shows_refresh, add)
        self.assertGreater(lib_refreshes[-1], add)
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual(self.writes(), [])

    def with_unmatched(self, entry=None):
        lists, coming, details, comingd = feeds()
        lists["home"]["shows"].append(entry or {"k": "tv", "id": None, "r": 3, "u": True, "s": None, "f": None, "o": False, "n": True, "y": None,
                                                 "t": {"en": "Instadocs: AI Gone Wild", "de": "Instadocs: AI Gone Wild"}, "p": {"en": None, "de": None}})
        self.write_feeds(lists, coming, details, comingd)

    def unmatched_item(self):
        return next(i for i in self.io.library if i["Name"] == "Instadocs AI Gone Wild")

    def home_shows(self):
        return next(s for s in self.io.sets.values() if s["Name"] == "Top 10 Switzerland · Shows")["children"]

    def test_unmatched_entry_is_a_title_only_film_placeholder_at_its_rank(self):
        self.with_unmatched()
        self.assertEqual(self.run_sync(), 0, self.out)
        d = self.paths.library / "Instadocs AI Gone Wild"
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["Instadocs AI Gone Wild.mp4", "movie.nfo", "poster.jpg"])
        self.assertEqual(os.stat(d / "Instadocs AI Gone Wild.mp4").st_ino, os.stat(self.paths.clips / "unavailable.mp4").st_ino)
        self.assertEqual((d / "poster.jpg").read_bytes(), tr.art.poster(None, "none", "Instadocs: AI Gone Wild", 3))
        nfo = (d / "movie.nfo").read_text()
        self.assertIn("<title>Instadocs: AI Gone Wild</title>", nfo)
        self.assertIn(f"<plot>{tm.PREFIX['none']}</plot>", nfo)
        self.assertNotIn("<uniqueid", nfo)
        u = self.unmatched_item()
        self.assertEqual(self.home_shows(), [self.series("Delta")["Id"], self.series("Epsilon")["Id"], u["Id"]])
        idx = json.loads(self.paths.index.read_text())
        self.assertEqual(idx["items"][u["Id"]], {"x": None, "t": "Instadocs: AI Gone Wild", "modes": {}, "rt": 100000000})
        hide = next(s for s in self.io.injector["CustomJavaScripts"] if s["Name"] == "Trending hidden")["Script"]
        self.assertIn(f'"{u["Id"]}"', hide)
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual(self.writes(), [])

    def test_unmatched_entry_matched_later_replaces_its_placeholder(self):
        self.with_unmatched()
        self.assertEqual(self.run_sync(), 0, self.out)
        old = self.unmatched_item()["Id"]
        self.with_unmatched({**show(70, 3, "Instadocs: AI Gone Wild", found=False), "d": "none"})
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertFalse((self.paths.library / "Instadocs AI Gone Wild").exists())
        self.assertTrue(self.show_dir("Instadocs AI Gone Wild").is_dir())
        self.assertNotIn(old, [i["Id"] for i in self.io.library])
        self.assertEqual(self.home_shows(), [self.series("Delta")["Id"], self.series("Epsilon")["Id"], self.series("Instadocs AI Gone Wild")["Id"]])
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertEqual(self.writes(), [])

    def test_unmatched_entry_gone_removes_its_placeholder(self):
        self.with_unmatched()
        self.assertEqual(self.run_sync(), 0, self.out)
        self.write_feeds(*feeds())
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertFalse((self.paths.library / "Instadocs AI Gone Wild").exists())
        self.assertEqual(self.home_shows(), [self.series("Delta")["Id"], self.series("Epsilon")["Id"]])

    def test_unresolved_shows_library_raises_before_writing_any_folder(self):
        self.io.shows_id = None
        old = self.paths.library / "Delta (2026)"
        old.mkdir()
        (old / "Delta (2026).mp4").hardlink_to(self.paths.clips / "unavailable.mp4")
        self.assertEqual(self.run_sync(), 1, self.out)
        self.assertIn("Trending shows", self.out)
        self.assertFalse(self.folder("Alpha").exists())
        self.assertFalse(self.show_dir("Delta").exists())
        self.assertTrue(old.is_dir())
        self.assertFalse(self.paths.state.exists())
        self.assertEqual(self.writes(), [])

    def test_usage(self):
        self.assertEqual(self.run_sync("--bogus"), 2)


if __name__ == "__main__":
    unittest.main()
