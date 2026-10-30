import contextlib
import importlib.util
import io
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("hidden_gems", pathlib.Path(__file__).with_name("hidden-gems.py"))
hg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hg)


def item(i, rating, added, path=None):
    return {"Id": i, "CommunityRating": rating, "DateCreated": added, "Path": path or f"/media/movies/{i}"}


class PickTest(unittest.TestCase):
    def test_rated_real_titles_newest_added_first(self):
        items = [item("old", 8.0, "2024-01-01T00:00:00Z"), item("low", 7.4, "2026-10-30T00:00:00Z"), item("new", 7.5, "2026-10-29T00:00:00Z"),
                 item("ph", 9.0, "2026-10-30T00:00:00Z", "/config/trending/library/x"), item("none", None, "2026-10-30T00:00:00Z")]
        self.assertEqual(hg.pick(items), ["new", "old"])

    def test_twenty_titles(self):
        items = [item(f"i{n:02d}", 8.0, f"2026-10-{n:02d}T00:00:00Z") for n in range(1, 31)]
        self.assertEqual(hg.pick(items), [f"i{n:02d}" for n in range(30, 10, -1)])


def apply(have, steps):
    """Members after each step, as Jellyfin applies them (POST appends new ids, DELETE removes)."""
    cur, sizes = list(have), []
    for method, ids in steps:
        if method == "POST":
            cur += [i for i in ids if i not in cur]
        else:
            cur = [i for i in cur if i not in ids]
        sizes.append(len(cur))
    return cur, sizes


class StepsTest(unittest.TestCase):
    CASES = [
        (["a", "b"], ["a", "b"]),
        (["a", "b", "c", "d"], ["a", "x", "b", "c"]),
        (["a", "b"], ["n", "a", "b"]),
        (["a", "b", "c"], ["a", "b"]),
        (["x", "b"], ["a", "b"]),
        (["b", "a"], ["a", "b"]),
        (["c", "b", "a"], ["n", "a", "b", "c"]),
        ([], ["a", "b"]),
    ]

    def test_ends_in_order_and_never_empty(self):
        for have, want in self.CASES:
            cur, sizes = apply(have, hg.steps(have, want))
            self.assertEqual(cur, want, (have, want))
            self.assertNotIn(0, sizes, (have, want))

    def test_in_order_nothing_to_do(self):
        self.assertEqual(hg.steps(["a", "b"], ["a", "b"]), [])

    def test_no_titles_leaves_the_members(self):
        self.assertEqual(hg.steps(["a", "b"], []), [])

    def test_new_titles_added_before_anything_is_removed(self):
        st = hg.steps(["x", "b"], ["a", "b"])
        self.assertEqual(st[0], ("POST", ["a"]))


class FakeJellyfin:
    def __init__(self, boxes=None, members=None, items=None):
        self.boxes = boxes if boxes is not None else []
        self.members = members if members is not None else {}
        self.items = items or []
        self.calls = []
        self.meta = {}

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/Users":
            return [{"Id": "adm", "Policy": {"IsAdministrator": True}}, {"Id": "u", "Policy": {"IsAdministrator": False}}]
        if path.startswith("/Items?") and "IncludeItemTypes=BoxSet" in path:
            return {"Items": self.boxes}
        if path.startswith("/Items?") and "ParentId=" in path:
            sid = path.split("ParentId=")[1].split("&")[0]
            return {"Items": [{"Id": i} for i in self.members.get(sid, [])]}
        if path.startswith("/Items?"):
            return {"Items": self.items}
        if method == "POST" and path.startswith("/Collections?"):
            ids = path.split("ids=")[1].split("&")[0].split(",")
            self.boxes.append({"Id": "new", "Name": hg.NAME, "Tags": []})
            self.members["new"] = ids
            return {"Id": "new"}
        if method == "GET" and path.startswith("/Items/"):
            sid = path.split("/")[2].split("?")[0]
            return self.meta.get(sid, {"Id": sid, "Name": hg.NAME, "Tags": []})
        if method == "POST" and path.startswith("/Items/"):
            self.meta[path.split("/")[2]] = body
            return None
        if path.startswith("/Collections/"):
            sid = path.split("/")[2]
            ids = path.split("ids=")[1].split(",")
            have = self.members.setdefault(sid, [])
            if method == "POST":
                have += [i for i in ids if i not in have]
            else:
                self.members[sid] = [i for i in have if i not in ids]
            return None
        raise AssertionError(path)


ITEMS = [item("a", 8.0, "2026-10-30T00:00:00Z"), item("b", 7.9, "2026-10-29T00:00:00Z")]


class Orders:
    def __init__(self):
        self.calls = []

    def __call__(self, users, orders, dry):
        self.calls.append((users, orders, dry))


def run(jf, argv=(), order=None):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = hg.main(list(argv), api=jf, order=order or Orders())
    return code, out.getvalue()


class MainTest(unittest.TestCase):
    def test_no_titles_leaves_an_existing_collection_unchanged(self):
        jf = FakeJellyfin(boxes=[{"Id": "g", "Name": hg.NAME, "Tags": [hg.TAG]}], members={"g": ["a", "b"]}, items=[])
        jf.meta["g"] = {"Id": "g", "Name": hg.NAME, "Tags": [hg.TAG], "DisplayOrder": "Default", "LockData": True}
        code, out = run(jf)
        self.assertEqual(jf.members["g"], ["a", "b"])
        self.assertEqual([c for c in jf.calls if c[0] != "GET"], [])
        self.assertIn("no titles", out)

    def test_creates_the_tagged_locked_collection_in_order(self):
        jf = FakeJellyfin(items=ITEMS)
        run(jf)
        self.assertEqual(jf.members["new"], ["a", "b"])
        meta = jf.meta["new"]
        self.assertEqual((meta["Tags"], meta["DisplayOrder"], meta["LockData"], meta["Name"]), ([hg.TAG], "Default", True, hg.NAME))

    def test_second_run_changes_nothing(self):
        jf = FakeJellyfin(items=ITEMS)
        run(jf)
        jf.boxes[0]["Tags"] = [hg.TAG]
        before = len([c for c in jf.calls if c[0] != "GET"])
        code, out = run(jf)
        self.assertEqual(len([c for c in jf.calls if c[0] != "GET"]), before)
        self.assertIn("unchanged", out)

    def test_follows_new_titles(self):
        jf = FakeJellyfin(boxes=[{"Id": "g", "Name": hg.NAME, "Tags": [hg.TAG]}], members={"g": ["b", "old"]}, items=ITEMS)
        jf.meta["g"] = {"Id": "g", "Name": hg.NAME, "Tags": [hg.TAG], "DisplayOrder": "Default", "LockData": True}
        run(jf)
        self.assertEqual(jf.members["g"], ["a", "b"])

    def test_never_empty_when_the_order_changes(self):
        jf = FakeJellyfin(boxes=[{"Id": "g", "Name": hg.NAME, "Tags": [hg.TAG]}], members={"g": ["old", "b", "a"]}, items=ITEMS)
        jf.meta["g"] = {"Id": "g", "Name": hg.NAME, "Tags": [hg.TAG], "DisplayOrder": "Default", "LockData": True}
        sizes = []
        real = jf.__call__

        def spy(method, path, body=None):
            out = real(method, path, body)
            if path.startswith("/Collections/"):
                sizes.append(len(jf.members["g"]))
            return out

        with contextlib.redirect_stdout(io.StringIO()):
            hg.main([], api=spy, order=Orders())
        self.assertEqual(jf.members["g"], ["a", "b"])
        self.assertNotIn(0, sizes)

    def test_untagged_collection_of_the_same_name_not_touched(self):
        jf = FakeJellyfin(boxes=[{"Id": "own", "Name": hg.NAME, "Tags": []}], members={"own": ["x"]}, items=ITEMS)
        run(jf)
        self.assertEqual(jf.members["own"], ["x"])
        self.assertEqual(jf.members["new"], ["a", "b"])

    def test_dry_run_writes_nothing(self):
        jf = FakeJellyfin(items=ITEMS)
        code, out = run(jf, ["--dry-run"])
        self.assertEqual([c for c in jf.calls if c[0] != "GET"], [])
        self.assertIn("would create", out)

    def test_moonfin_order_newest_first_for_every_user(self):
        jf = FakeJellyfin(boxes=[{"Id": "g", "Name": hg.NAME, "Tags": [hg.TAG]}], members={"g": ["b", "old"]}, items=ITEMS)
        jf.meta["g"] = {"Id": "g", "Name": hg.NAME, "Tags": [hg.TAG], "DisplayOrder": "Default", "LockData": True}
        orders = Orders()
        run(jf, order=orders)
        self.assertEqual(orders.calls, [(["adm", "u"], {"g": ["a", "b"]}, False)])
        orders = Orders()
        run(jf, ["--dry-run"], order=orders)
        self.assertEqual(orders.calls, [(["adm", "u"], {"g": ["a", "b"]}, True)])

    def test_no_titles_writes_no_order(self):
        orders = Orders()
        run(FakeJellyfin(items=[]), order=orders)
        self.assertEqual(orders.calls, [])

    def test_no_titles_no_empty_collection(self):
        jf = FakeJellyfin(items=[])
        run(jf)
        self.assertEqual([c for c in jf.calls if c[0] != "GET"], [])


if __name__ == "__main__":
    unittest.main()
