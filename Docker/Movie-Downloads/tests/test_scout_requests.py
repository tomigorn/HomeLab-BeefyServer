#!/usr/bin/env python3
"""scripts/scout-requests.py: one release read on demand for Seerr's Manual interaction, through the NFO scout and description scout code and their limits, against fake indexers and FlareSolverr.
Usage: python3 tests/test_scout_requests.py"""
import importlib.util
import json
import pathlib
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("scout_requests", ROOT / "scripts" / "scout-requests.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)
NOW = 1_800_000_000.0
GID = "b" * 32
NFO = ('<rss><channel><item><title>The.Ring.Thing.2004.MULTi.720p</title><guid isPermaLink="true">https://api.example.net/details/' + GID + '</guid>'
       '<description>  AUDIO ....: German, English\n  SUBS .....: English</description></item></channel></rss>')
APIBAY = ('<html><body><pre>{"id":3872789,"name":"The Ring Thing (2004) [XVID]","seeders":0,'
          '"descr":"The Ring Thing (2004)\\r\\n\\r\\nAudio: Vorbis\\r\\nLanguage: Schwyzerd&amp;Atilde;&amp;frac14;tsch &amp;amp; Deutsch\\r\\n",'
          '"imdb":"tt0432028","info_hash":"180F491CAA6F63C4469294D239A5A98E02FBB9A5"}</pre></body></html>')
MAGNET = "magnet:?xt=urn:btih:180F491CAA6F63C4469294D239A5A98E02FBB9A5&dn=The%20Ring%20Thing"


def usage(used=10, limit=100):
    return {"ok": True, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 60)),
            "indexers": [{"name": "Treasure Maps", "window": "rolling", "searches": {"used": used, "limit": limit}}]}


def usenet(indexer="NzbPlanet (Prowlarr)", gid=GID):
    return {"id": "0123456789abcdef", "indexer": indexer, "protocol": "usenet", "guid": "https://api.example.net/details/" + gid, "title": "The.Ring.Thing.2004.MULTi.720p"}


def torrent(info="https://thepiratebay.org/description.php?id=3872789"):
    return {"id": "fedcba9876543210", "indexer": "The Pirate Bay (Prowlarr)", "protocol": "torrent", "guid": MAGNET, "infoUrl": info, "title": "The Ring Thing (2004) [XVID]"}


class Requests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.d = pathlib.Path(self.dir.name)
        self.nfo_state, self.ds_state = self.d / "nfo-scout.json", self.d / "description-scout.json"
        self.nfo_calls, self.desc_calls, self.log = [], [], []

    def nfo_fetch(self, indexer, function, gid):
        self.nfo_calls.append((indexer, function, gid))
        return NFO

    def desc_fetch(self, url):
        self.desc_calls.append(url)
        return APIBAY

    def process(self, req, **kw):
        kw.setdefault("usage", usage())
        return sr.process(req, nfo_fetch=self.nfo_fetch, desc_fetch=self.desc_fetch, nfo_state=self.nfo_state, ds_state=self.ds_state,
                          now=NOW, log=self.log.append, **kw)

    def test_usenet_nfo_is_read_into_the_nfo_scouts_state(self):
        out = self.process(usenet())
        self.assertEqual((out["state"], out["source"], out["langs"]), ("done", "nfo", ["German", "English"]))
        entry = json.loads(self.nfo_state.read_text())["releases"]["NzbPlanet|" + GID]
        self.assertEqual((entry["langs"], entry["lines"]), (["German", "English"], ["AUDIO ....: German, English"]))
        self.assertEqual(self.process(usenet())["state"], "done")
        self.assertEqual(len(self.nfo_calls), 1)

    def test_treasure_maps_daily_limit_is_respected(self):
        self.nfo_state.write_text(json.dumps({"calls": {"Treasure Maps": [NOW - 60] * 50}}))
        out = self.process(usenet("Treasure Maps (Prowlarr)"))
        self.assertEqual(out, {"state": "refused", "why": "daily limit reached, try tomorrow"})
        self.assertEqual(self.nfo_calls, [])

    def test_own_daily_cap_for_reads_on_demand(self):
        self.nfo_state.write_text(json.dumps({"ondemand": [NOW - 60] * 30}))
        self.assertEqual(self.process(usenet())["state"], "refused")
        self.assertEqual(self.process(usenet(), nfo_day=31)["state"], "done")

    def test_torrent_description_through_flaresolverr(self):
        out = self.process(torrent())
        self.assertEqual((out["state"], out["source"]), ("done", "description"))
        self.assertIn("Swiss German", out["langs"])
        self.assertEqual(self.desc_calls, ["https://apibay.org/t.php?id=3872789"])
        entry = json.loads(self.ds_state.read_text())["read|btih:180F491CAA6F63C4469294D239A5A98E02FBB9A5"]
        self.assertIn("Swiss German", entry["langs"])
        self.assertTrue(any("Language" in l for l in entry["lines"]))
        self.process(torrent())
        self.assertEqual(len(self.desc_calls), 1)

    def test_description_daily_cap(self):
        self.ds_state.write_text(json.dumps({"ondemand": [NOW - 60] * 30}))
        self.assertEqual(self.process(torrent()), {"state": "refused", "why": "daily limit reached, try tomorrow"})
        self.assertEqual(self.desc_calls, [])

    def test_what_cannot_be_read_says_so(self):
        for req in (torrent("https://eztvx.to/ep/1/x/"), usenet("NZBIndex (Prowlarr)"), usenet(gid="nothex"), {"id": "x"}):
            self.assertEqual(self.process(req)["state"], "failed", req)
        self.assertEqual(self.nfo_calls + self.desc_calls, [])


class Runner(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.req = pathlib.Path(self.dir.name, "req")
        self.req.mkdir()
        self.seen = []

    def run_once(self, busy=False):
        return sr.run(self.req, lambda r: (self.seen.append(r["id"]), {"state": "done", "source": "nfo", "langs": ["German"]})[1], busy=lambda: busy, now=NOW, max_wait=0)

    def test_requests_get_a_result_file_and_go(self):
        (self.req / "0123456789abcdef.req").write_text(json.dumps(usenet()))
        self.run_once()
        self.assertEqual(self.seen, ["0123456789abcdef"])
        self.assertFalse((self.req / "0123456789abcdef.req").exists())
        self.assertEqual(json.loads((self.req / "0123456789abcdef.result.json").read_text())["state"], "done")

    def test_nothing_runs_while_a_scout_runs(self):
        (self.req / "0123456789abcdef.req").write_text(json.dumps(usenet()))
        self.run_once(busy=True)
        self.assertEqual(self.seen, [])
        self.assertTrue((self.req / "0123456789abcdef.req").exists())

    def test_odd_files_are_ignored_or_failed(self):
        (self.req / "notes.txt").write_text("x")
        (self.req / "0000000000000000.req").write_text("{broken")
        self.run_once()
        self.assertEqual(self.seen, [])
        self.assertEqual(json.loads((self.req / "0000000000000000.result.json").read_text())["state"], "failed")
        self.assertTrue((self.req / "notes.txt").exists())

    def test_old_results_are_removed(self):
        old = self.req / "1111111111111111.result.json"
        old.write_text("{}")
        import os
        os.utime(old, (NOW - 8 * 86400, NOW - 8 * 86400))
        self.run_once()
        self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
