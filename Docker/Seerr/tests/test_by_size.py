#!/usr/bin/env python3
"""download-now/by_size.py: the Requests page's GB sort as one call — every request of the caller's Seerr view sorted by size, then one page in Seerr's own answer format.
Usage: python3 tests/test_by_size.py"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "download-now"))
spec = importlib.util.spec_from_file_location("download_now", ROOT / "download-now" / "download_now.py")
dn = importlib.util.module_from_spec(spec)
sys.modules["download_now"] = dn
spec.loader.exec_module(dn)

COOKIE = {"Cookie": "connect.sid=s%3Aabc"}
# Seerr's order (added, newest first): id → sizes entry (None = not in request-state.json)
SIZES = {
    "70": {"size": 45e9, "runtime": 120},
    "69": {"queued": 5e9},
    "68": {"size": 30e9, "files": 10, "of": 12, "runtime": 50},
    "67": None,
    "66": {"size": 60e9, "runtime": 180},
    "65": {"size": 2e9, "files": 0, "of": 8},
    "64": {"size": 9e9},
}
ORDER = [70, 69, 68, 67, 66, 65, 64]


def request(rid):
    return {"id": rid, "type": "movie", "media": {"tmdbId": 1000 + rid}, "requestedBy": {"id": 7}}


class FakeSeerr:
    def __init__(self, perms=16384, ids=ORDER, fail=False):
        self.perms, self.ids, self.fail, self.calls = perms, list(ids), fail, []

    def __call__(self, path, auth):
        self.calls.append((path, auth))
        if path == "/auth/me":
            if self.perms is None:
                raise dn.ApiError("seerr: 403")
            return {"id": 7, "permissions": self.perms}
        if self.fail:
            raise dn.ApiError("seerr: 500")
        url = urllib.parse.urlsplit(path)
        assert url.path == "/request", path
        q = dict(urllib.parse.parse_qsl(url.query))
        take, skip = int(q["take"]), int(q["skip"])
        page = self.ids[skip:skip + take]
        return {"pageInfo": {"pages": -(-len(self.ids) // take), "pageSize": take, "results": len(self.ids), "page": skip // take + 1}, "results": [request(i) for i in page]}


class BySize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name, "request-state.json")
        self.state.write_text(json.dumps({"sizes": {k: v for k, v in SIZES.items() if v is not None}}))

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, query, seerr=None, headers=COOKIE, state=None, method="GET"):
        self.seerr = seerr or FakeSeerr()
        deps = dn.Deps(seerr=self.seerr, arr=None, qbit=None, sab=None, request_state_file=state or self.state)
        return dn.handle(method, "/_download-now/by-size?" + query, dict(headers), b"", deps)

    def ids(self, out):
        return [r["id"] for r in out["results"]]

    def test_largest_total_first_across_every_page_titles_without_a_size_last(self):
        status, out = self.get("take=3&skip=0&filter=all&mediaType=all&sort=added&sortDirection=desc&hg_size=desc")
        self.assertEqual((status, self.ids(out)), (200, [66, 70, 68]))
        self.assertEqual(out["pageInfo"], {"pages": 3, "pageSize": 3, "results": 7, "page": 1})
        self.assertEqual(self.ids(self.get("take=3&skip=3&filter=all&mediaType=all&hg_size=desc")[1]), [64, 69, 65])
        status, out = self.get("take=3&skip=6&filter=all&mediaType=all&hg_size=desc")
        self.assertEqual((self.ids(out), out["pageInfo"]["page"]), ([67], 3))

    def test_smallest_total_first(self):
        self.assertEqual(self.ids(self.get("take=10&skip=0&filter=all&mediaType=all&hg_size=asc")[1]), [65, 69, 64, 68, 70, 66, 67])

    def test_per_file_and_per_hour_a_film_is_one_file_no_value_last(self):
        self.assertEqual(self.ids(self.get("take=10&skip=0&filter=all&mediaType=all&hg_size=file-desc")[1]), [66, 70, 64, 68, 69, 67, 65])
        self.assertEqual(self.ids(self.get("take=10&skip=0&filter=all&mediaType=all&hg_size=hour-desc")[1]), [70, 66, 68, 69, 67, 65, 64])
        self.assertEqual(self.ids(self.get("take=10&skip=0&filter=all&mediaType=all&hg_size=hour-asc")[1]), [68, 66, 70, 69, 67, 65, 64])

    def test_seerr_is_asked_as_the_caller_with_the_callers_filter_for_every_request(self):
        seerr = FakeSeerr(ids=list(range(1200, 0, -1)))
        status, out = self.get("take=10&skip=0&filter=pending&mediaType=movie&requestedBy=7&sort=modified&sortDirection=asc&hg_size=desc", seerr=seerr)
        self.assertEqual((status, out["pageInfo"]["results"], len(out["results"])), (200, 1200, 10))
        lists = [p for p, a in seerr.calls if p != "/auth/me"]
        self.assertEqual(lists, [f"/request?take=500&skip={s}&filter=pending&mediaType=movie&sort=added&sortDirection=desc&requestedBy=7" for s in (0, 500, 1000)])
        self.assertTrue(all(a == COOKIE for p, a in seerr.calls))

    def test_bad_parameters_are_refused_before_seerr_is_asked(self):
        for q in ("take=0&skip=0&hg_size=desc", "take=101&skip=0&hg_size=desc", "take=x&skip=0&hg_size=desc", "take=10&skip=-1&hg_size=desc",
                  "take=10&skip=0&hg_size=biggest", "take=10&skip=0", "take=10&skip=0&hg_size=desc&filter=all;drop", "take=10&skip=0&hg_size=desc&mediaType=music",
                  "take=10&skip=0&hg_size=desc&requestedBy=7%26take%3D1"):
            status, out = self.get(q)
            self.assertEqual(status, 400, q)
            self.assertEqual([p for p, a in self.seerr.calls if p != "/auth/me"], [], q)

    def test_no_session_no_data_and_get_only(self):
        self.assertEqual(self.get("take=10&skip=0&hg_size=desc", seerr=FakeSeerr(perms=None))[0], 403)
        self.assertEqual(self.get("take=10&skip=0&hg_size=desc", headers={})[0], 403)
        self.assertEqual(self.get("take=10&skip=0&hg_size=desc", method="POST")[0], 404)

    def test_seerr_down_is_a_502(self):
        self.assertEqual(self.get("take=10&skip=0&hg_size=desc", seerr=FakeSeerr(fail=True)), (502, {"error": "seerr not reachable"}))

    def test_no_sizes_keeps_seerrs_order(self):
        status, out = self.get("take=10&skip=0&hg_size=desc", state=pathlib.Path(self.tmp.name, "none.json"))
        self.assertEqual((status, self.ids(out)), (200, ORDER))


if __name__ == "__main__":
    unittest.main()
