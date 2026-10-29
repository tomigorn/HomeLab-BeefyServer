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
import urllib.parse

from PIL import Image

_spec = importlib.util.spec_from_file_location("trending", pathlib.Path(__file__).with_name("trending.py"))
tr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tr)

import trending_model as tm  # noqa: E402

NOW = datetime.datetime(2026, 10, 29, 12, 0, tzinfo=datetime.timezone.utc)
LIB = "lib0"
ADMIN = "admin0"
INJ = "inj0"


def jpeg(color=(10, 20, 30)):
    out = io.BytesIO()
    Image.new("RGB", (40, 60), color).save(out, "JPEG")
    return out.getvalue()


def entry(tid, rank, title, found=True, x=True, d=None):
    return {"k": "movie", "id": tid, "r": rank, "d": d or f"d{tid}", "f": "1080p" if found else None, "x": f"x{tid}" if x and found else None,
            "o": False, "u": False, "s": None, "t": {"en": title, "de": title}, "y": 2026, "p": {"en": f"/p{tid}.jpg"}}


def feeds(updated=None):
    stamp = (updated or NOW).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    lists = {"updated": stamp, "home": {"movies": [entry(1, 1, "Alpha"), entry(2, 2, "Beta", found=False), entry(9, 3, "Owned")], "shows": []},
             "rows": [{"id": "service-8-movie", "kind": "movie", "name": {"en": "Netflix"}, "logo": "/logo8.png", "items": [entry(2, 1, "Beta", found=False), entry(1, 2, "Alpha")]}]}
    coming = {"updated": stamp, "items": [{"d": "c5", "date": "2026-11-04", "kind": "movie", "pop": 1.0, "ref": "movie/5", "tmdb": 5}]}
    details = {"updated": stamp, "entries": {f"d{i}": {"t": {"en": t}, "p": {"en": f"/p{i}.jpg"}, "b": f"/b{i}.jpg", "ref": f"movie/{i}", "y": 2026, "o": {"en": f"Plot {t}"}}
                                            for i, t in ((1, "Alpha"), (2, "Beta"))}}
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

    def scan(self):
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
        u = urllib.parse.urlsplit(path)
        q = dict(urllib.parse.parse_qsl(u.query))
        p = u.path
        if p == "/Library/VirtualFolders":
            return [{"Name": "Movies", "ItemId": "movies"}, {"Name": tm.LIBRARY, "ItemId": LIB}]
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
            self.scan()
            return None
        if re.fullmatch(r"/Items/\w+/Refresh", p):
            return None
        if p == "/Items" and q.get("Recursive") == "true" and q.get("IncludeItemTypes") != "BoxSet":
            assert q.get("CollapseBoxSetItems") == "false", path
        if p == "/Items" and q.get("IncludeItemTypes") == "BoxSet":
            return {"Items": [{k: v for k, v in s.items() if k != "children"} for s in self.sets.values()]}
        if p == "/Items" and q.get("ParentId") == LIB:
            return {"Items": json.loads(json.dumps(self.library))}
        if p == "/Items" and q.get("ParentId") in self.sets:
            return {"Items": [{"Id": i} for i in self.sets[q["ParentId"]]["children"]]}
        if p == "/Items" and q.get("IncludeItemTypes") == "Movie,Series":
            return {"Items": self.owned + json.loads(json.dumps(self.library))}
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
        raise AssertionError(f"unexpected {method} {path}")

    def jf_raw(self, method, path, data, content_type):
        self.calls.append((method, path))
        self.images[path] = data
        return b""

    def seerr_media(self):
        return self.media

    def fetch(self, url):
        return jpeg()

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
                              request_state=root / "request-state.json")
        for d in ("library", "clips", "cache"):
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
        self.assertFalse(any(c[1].startswith("/Items/") and c[1].endswith("/Images/Primary") for c in self.io.calls))

        self.paths.state.unlink()
        self.assertEqual(self.run_sync(), 0, self.out)
        self.assertFalse(any(c[1].startswith("/Collections?") for c in self.io.calls))
        self.assertEqual(len([s for s in self.io.sets.values() if tm.TAG in s["Tags"]]), 4)

    def test_hide_entry_lists_library_sets_and_placeholders(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        entry_ = next(s for s in self.io.injector["CustomJavaScripts"] if s["Name"] == "Trending hidden")
        self.assertTrue(entry_["Script"].startswith("window.HssTrendingHide = "))
        hide = json.loads(entry_["Script"][len("window.HssTrendingHide = "):].rstrip().rstrip(";"))
        self.assertEqual(hide["lib"], LIB)
        self.assertEqual(sorted(hide["sets"]), sorted(s["Id"] for s in self.io.sets.values() if tm.TAG in s["Tags"]))
        self.assertIn(self.item("Alpha")["Id"], hide["items"])
        self.assertNotIn("real9", hide["items"])
        self.assertTrue(any(s["Name"] == "Other" for s in self.io.injector["CustomJavaScripts"]))

    def test_index_maps_primary_and_version_ids_to_mode(self):
        self.assertEqual(self.run_sync(), 0, self.out)
        idx = json.loads(self.paths.index.read_text())
        self.assertEqual(oct(self.paths.index.stat().st_mode & 0o777), "0o644")
        self.assertEqual((idx["library"], idx["runtimeTicks"]), (LIB, 100000000))
        a = self.item("Alpha")
        rec = idx["items"][a["Id"]]
        by_name = {ms["Path"].rsplit("/", 1)[1]: ms["Id"] for ms in a["MediaSources"]}
        self.assertEqual(rec["x"], "x1")
        self.assertEqual(rec["t"], "Alpha")
        self.assertEqual(rec["modes"], {by_name["Alpha (2026) - ⬇ Download movie now.mp4"]: "now", by_name["Alpha (2026) - 🌙 Download movie tonight · lower power cost.mp4"]: "night"})
        for ms in a["MediaSources"]:
            self.assertEqual(idx["items"][ms["Id"]], rec)
        self.assertEqual(idx["items"][self.item("Beta")["Id"]], {"x": None, "t": "Beta", "modes": {}})

    def test_stale_inputs_keep_published_state(self):
        self.write_feeds(*feeds(updated=NOW - datetime.timedelta(days=10)))
        self.assertEqual(self.run_sync(), 1)
        self.assertEqual(list(self.paths.library.iterdir()), [])
        self.assertFalse(self.paths.index.exists())
        self.assertEqual(self.writes(), [])

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.run_sync("--dry-run"), 0, self.out)
        self.assertEqual(list(self.paths.library.iterdir()), [])
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

    def test_usage(self):
        self.assertEqual(self.run_sync("--bogus"), 2)


if __name__ == "__main__":
    unittest.main()
