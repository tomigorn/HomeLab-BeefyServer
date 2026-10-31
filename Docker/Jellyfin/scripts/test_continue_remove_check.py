import importlib.util
import io
import pathlib
import unittest
from urllib.parse import parse_qs, urlparse

_spec = importlib.util.spec_from_file_location("continue_remove_check", pathlib.Path(__file__).with_name("continue-remove-check.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

CANARY = "c" * 32
FILM, EP1, EP2, SERIES = "f1", "e1", "e2", "s1"
BLANK = {"PlaybackPositionTicks": 0, "PlayCount": 0, "IsFavorite": False, "Played": False, "LastPlayedDate": None}


class FakeJellyfin:
    """Jellyfin 12.4 semantics as measured live 2026-10-31: Resume = position > 0; Next Up keyed by the series' last played date, in-progress episodes only with enableResumable."""

    def __init__(self, position_write_also_resets=False):
        self.data = {i: dict(BLANK) for i in (FILM, EP1, EP2)}
        self.resets = position_write_also_resets
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        u = urlparse(path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        if p == "/Users":
            return [{"Id": "x" * 32, "Name": "anna"}, {"Id": CANARY, "Name": "canary-test"}]
        if p == "/Items" and q.get("IncludeItemTypes") == "Movie":
            return {"Items": [{"Id": FILM, "Name": "Nosferatu"}]}
        if p == "/Items" and q.get("IncludeItemTypes") == "Episode" and q.get("IndexNumber") == "2":
            return {"Items": [{"Id": EP2, "SeriesId": SERIES}]}
        if p == f"/Shows/{SERIES}/Episodes":
            return {"Items": [{"Id": EP1, "ParentIndexNumber": 1, "IndexNumber": 1}, {"Id": EP2, "ParentIndexNumber": 1, "IndexNumber": 2}]}
        assert q.get("userId") == CANARY, path
        if p == "/UserItems/Resume":
            return {"Items": [{"Id": i} for i, d in self.data.items() if d["PlaybackPositionTicks"] > 0]}
        if p == "/Shows/NextUp":
            eps = [EP1, EP2]
            if not any(self.data[e]["LastPlayedDate"] for e in eps):
                return {"Items": []}
            nxt = next((e for e in eps if not self.data[e]["Played"]), None)
            if nxt is None or (q.get("enableResumable") == "false" and self.data[nxt]["PlaybackPositionTicks"] > 0):
                return {"Items": []}
            return {"Items": [{"Id": nxt, "SeriesId": SERIES}]}
        if p.startswith("/UserPlayedItems/"):
            i = p.rsplit("/", 1)[1]
            if method == "DELETE":
                self.data[i] = dict(BLANK, IsFavorite=self.data[i]["IsFavorite"])
            return {}
        if p.startswith("/UserItems/") and p.endswith("/UserData"):
            i = p.split("/")[2]
            if method == "POST":
                for k, v in body.items():
                    if v is not None:
                        self.data[i][k] = v
                if self.resets and set(body) == {"PlaybackPositionTicks"} and body["PlaybackPositionTicks"] == 0:
                    self.data[i].update(PlayCount=0, LastPlayedDate=None)
            return dict(self.data[i])
        raise AssertionError(path)


class CheckTest(unittest.TestCase):
    def test_ok_on_jellyfin_semantics_and_canary_left_blank(self):
        fake = FakeJellyfin()
        out = io.StringIO()
        self.assertEqual(cc.main([], api=fake, out=out), 0)
        self.assertEqual(out.getvalue().strip().splitlines()[-1], "ok film and episode: only the resume point changes, undo restores, Next Up keeps the series")
        self.assertEqual(fake.data, {i: BLANK for i in (FILM, EP1, EP2)})

    def test_only_canary_items_are_written(self):
        fake = FakeJellyfin()
        cc.main([], api=fake, out=io.StringIO())
        for method, path, _ in fake.calls:
            if method != "GET":
                self.assertIn(f"userId={CANARY}", path)

    def test_fails_when_removing_also_drops_play_count_or_last_played(self):
        fake = FakeJellyfin(position_write_also_resets=True)
        out = io.StringIO()
        self.assertEqual(cc.main([], api=fake, out=out), 1)
        self.assertIn("FAIL", out.getvalue())
        self.assertEqual(fake.data, {i: BLANK for i in (FILM, EP1, EP2)}, "cleaned up also on failure")

    def test_fails_when_position_write_is_ignored(self):
        fake = FakeJellyfin()
        real = fake.__call__

        def ignoring(method, path, body=None):
            if method == "POST" and body == {"PlaybackPositionTicks": 0}:
                return {}
            return real(method, path, body)
        out = io.StringIO()
        self.assertEqual(cc.main([], api=ignoring, out=out), 1)
        self.assertIn("still in Resume", out.getvalue())

    def test_no_canary_is_an_error(self):
        out = io.StringIO()
        self.assertEqual(cc.main([], api=lambda m, p, b=None: [] if p == "/Users" else {}, out=out), 1)
        self.assertIn("no canary-test user", out.getvalue())

    def test_usage(self):
        self.assertEqual(cc.main(["--x"], api=FakeJellyfin(), out=io.StringIO()), 2)


if __name__ == "__main__":
    unittest.main()
