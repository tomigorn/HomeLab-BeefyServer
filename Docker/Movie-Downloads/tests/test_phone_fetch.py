#!/usr/bin/env python3
"""scripts/phone-fetch.py: a phone version request from pick to a second Jellyfin version (fastpi reports/specs/2026-11-19-phone-versions-design.md), against fake Radarr/Sonarr, SABnzbd and Jellyfin.
Usage: python3 tests/test_phone_fetch.py"""
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pf", ROOT / "scripts" / "phone-fetch.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)

GB = 1_000_000_000
NOW = 1_800_000_000.0
FOLDER = "A Film (2026) [tmdbid-4242]"


def release(title="A.Film.2026.1080p.WEB-DL.DDP5.1-GRP", name="WEBDL-1080p", res=1080, size=8 * GB, protocol="usenet", cfs=(), score=100):
    return {"title": title, "size": size, "protocol": protocol, "indexer": "Example", "downloadUrl": "http://gluetun:9696/1/download?apikey=K&link=L",
            "quality": {"quality": {"name": name, "resolution": res, "modifier": "none"}}, "customFormats": [{"name": c} for c in cfs],
            "customFormatScore": score, "languages": [{"name": "English"}], "rejections": []}


class FakeArr:
    def __init__(self, app="radarr", releases=(), resolution=2160):
        self.app, self.calls, self.releases, self.resolution = app, [], list(releases), resolution
        self.tracked = f"/data/media/movies/{FOLDER}/{FOLDER} - [WEBDL-2160p].mkv"

    def __call__(self, method, path, query=None, body=None):
        self.calls.append((method, path, query))
        if path == "/movie/12":
            return {"id": 12, "title": "A Film", "year": 2026, "tmdbId": 4242, "runtime": 120, "originalLanguage": {"name": "English"}, "qualityProfileId": 7,
                    "path": f"/data/media/movies/{FOLDER}", "movieFile": {"size": 40 * GB, "path": self.tracked, "quality": {"quality": {"resolution": self.resolution}}}}
        if path == "/movie":
            return [{"id": 12}] if (query or {}).get("tmdbId") == 4242 else []
        if path == "/episode/31":
            return {"id": 31, "seriesId": 5, "seasonNumber": 2, "episodeNumber": 1, "runtime": 50, "episodeFileId": 88}
        if path == "/episode":
            return [{"id": 31, "seasonNumber": 2, "episodeNumber": 1}, {"id": 32, "seasonNumber": 2, "episodeNumber": 2}]
        if path == "/series/5":
            return {"id": 5, "title": "A Show", "originalLanguage": {"name": "Spanish"}, "qualityProfileId": 7, "path": "/data/media/tv/A Show (2017) [tvdbid-99]"}
        if path == "/series":
            return [{"id": 5}] if (query or {}).get("tvdbId") == 99 else []
        if path == "/episodefile/88":
            return {"size": 6 * GB, "quality": {"quality": {"resolution": self.resolution}}, "path": "/data/media/tv/A Show (2017) [tvdbid-99]/Season 02/A Show (2017) - S02E01 - Title [WEBDL-2160p].mkv"}
        if path.startswith("/qualityprofile/"):
            return {"formatItems": [{"name": "LQ", "score": -1}]}
        if path == "/release":
            return self.releases
        raise AssertionError(path)


class FakeSab:
    def __init__(self):
        self.calls, self.queue, self.history = [], [], []

    def __call__(self, params):
        self.calls.append(dict(params))
        m = params["mode"]
        if m == "addurl":
            return {"status": True, "nzo_ids": ["SABnzbd_nzo_1"]}
        if m == "queue":
            return {"queue": {"slots": [s for s in self.queue if s["nzo_id"] in params.get("nzo_ids", "")]}}
        if m == "history":
            if params.get("name") == "delete":
                return {"status": True}
            return {"history": {"slots": [s for s in self.history if s["nzo_id"] in params.get("nzo_ids", "")]}}
        raise AssertionError(m)


class FakeJf:
    def __init__(self):
        self.calls, self.sources, self.sessions = [], [], []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.startswith("/Items/jf-film?") or path.startswith("/Items/jf-ep?"):
            kind = "Movie" if "film" in path else "Episode"
            item = {"Id": path.split("?")[0].rsplit("/", 1)[1], "Type": kind, "ProviderIds": {"Tmdb": "4242"}, "MediaSources": self.sources}
            if kind == "Episode":
                item.update(SeriesId="jf-show", ParentIndexNumber=2, IndexNumber=1)
            return item
        if path.startswith("/Items/jf-show?"):
            return {"Id": "jf-show", "ProviderIds": {"Tvdb": "99"}}
        if path.startswith("/Sessions"):
            return self.sessions
        return None


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.dl, self.hdd, self.box = self.root / "dlpool", self.root / "hdd", self.root / "box"
        for d in (self.dl, self.hdd, self.box / "requests"):
            d.mkdir(parents=True)
        self.results = self.root / "quality-results.jsonl"
        self.radarr = FakeArr(releases=[release()])
        self.sonarr = FakeArr("sonarr", releases=[release("A.Show.S02E01.1080p.NF.WEB-DL.HDR.HEVC-GRP", cfs=["HDR"], size=2 * GB)])
        self.sab, self.jf = FakeSab(), FakeJf()
        self.now = NOW
        self.deps = pf.Deps(arr={"radarr": self.radarr, "sonarr": self.sonarr}, sab=self.sab, jf=self.jf, dl_host=str(self.dl), hdd=str(self.hdd),
                            results=str(self.results), box=str(self.box), clock=lambda: self.now)
        self.state = pf.load_state(self.root / "none.json")

    def tearDown(self):
        self.tmp.cleanup()

    def finish_download(self, verdict="ok", job="phone radarr-12 A.Film.2026.1080p.WEB-DL.DDP5.1-GRP", name="A.Film.2026.1080p.WEB-DL.DDP5.1-GRP.mkv"):
        d = self.dl / "usenet/complete/phone" / job
        d.mkdir(parents=True)
        (d / name).write_bytes(b"x" * 1000)
        (d / "a.nfo").write_text("nfo")
        self.sab.history.append({"nzo_id": "SABnzbd_nzo_1", "status": "Completed", "storage": f"/data/usenet/complete/phone/{job}"})
        with open(self.results, "a") as f:
            f.write(json.dumps({"at": "2027-01-15T08:00:00Z", "path": f"/data/usenet/complete/phone/{job}/{name}", "verdict": verdict,
                                "reasons": [] if verdict == "ok" else ["no audio stream"], "notes": [], "duration": 7200.0}) + "\n")
        return d


class Request(Base):
    def test_movie_request_sends_the_pick_to_sab_category_phone(self):
        r = pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {"user": "u1", "device": "d1"})
        self.assertEqual((r["state"], r["option"], r["nzo"], r["release"]), ("downloading", 2, "SABnzbd_nzo_1", "A.Film.2026.1080p.WEB-DL.DDP5.1-GRP"))
        add = [c for c in self.sab.calls if c["mode"] == "addurl"][0]
        self.assertEqual((add["cat"], add["priority"], add["name"]), ("phone", "1", "http://gluetun:9696/1/download?apikey=K&link=L"))
        self.assertTrue(add["nzbname"].startswith("phone radarr-12 "))
        self.assertEqual(r["requestedBy"], [{"user": "u1", "device": "d1", "at": pf.ts(NOW)}])

    def test_jellyfin_item_is_resolved_to_the_arr_title(self):
        r = pf.request(self.deps, self.state, {"item": "jf-film"}, {})
        self.assertEqual((r["app"], r["arrId"], r["jfItem"]), ("radarr", 12, "jf-film"))
        e = pf.request(self.deps, self.state, {"item": "jf-ep"}, {})
        self.assertEqual((e["app"], e["arrId"], e["option"]), ("sonarr", 31, 1))

    def test_same_title_again_joins_the_running_request(self):
        pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {"user": "u1"})
        r = pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {"user": "u2"})
        self.assertEqual(len([c for c in self.sab.calls if c["mode"] == "addurl"]), 1)
        self.assertEqual([x["user"] for x in r["requestedBy"]], ["u1", "u2"])

    def test_torrents_are_not_fetched(self):
        self.radarr.releases = [release(protocol="torrent")]
        r = pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})
        self.assertEqual(r["state"], "nothing")
        self.assertEqual(self.sab.calls, [])

    def test_server_copy_not_4k_needs_nothing(self):
        self.radarr.resolution = 1080
        r = pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})
        self.assertEqual((r["state"], r["note"]), ("not-needed", "the server copy is not 4K"))
        self.assertNotIn("/release", [c[1] for c in self.radarr.calls])

    def test_nothing_found_is_retried_after_a_day(self):
        self.radarr.releases = []
        self.assertEqual(pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})["state"], "nothing")
        self.radarr.releases = [release()]
        self.assertEqual(pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})["state"], "nothing")
        self.now += pf.RETRY_HOURS * 3600 + 1
        self.assertEqual(pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})["state"], "downloading")

    def test_unknown_item_is_refused(self):
        with self.assertRaises(pf.Refused):
            pf.request(self.deps, self.state, {"item": "jf-nope"}, {})


class Run(Base):
    def start(self):
        return pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {"user": "u1", "device": "d1"})

    def test_progress_while_downloading(self):
        self.start()
        self.sab.queue.append({"nzo_id": "SABnzbd_nzo_1", "percentage": "42", "timeleft": "0:05:00"})
        pf.run(self.deps, self.state)
        r = self.state["requests"]["radarr-12"]
        self.assertEqual((r["state"], r["percent"]), ("downloading", 42))

    def test_complete_and_checked_ok_is_placed_on_the_hdd_as_a_second_version(self):
        self.start()
        job = self.finish_download()
        pf.run(self.deps, self.state)
        r = self.state["requests"]["radarr-12"]
        target = self.hdd / "media/movies" / FOLDER / f"{FOLDER} - Phone 1080p.mkv"
        self.assertEqual((r["state"], r["target"]), ("placing", str(target)))
        self.assertEqual(target.read_bytes(), b"x" * 1000)
        self.assertEqual(oct(target.stat().st_mode & 0o777), "0o664")
        self.assertFalse(job.exists())
        self.assertIn(("POST", "/Library/Media/Updated", {"Updates": [{"Path": f"/media/movies/{FOLDER}/{FOLDER} - Phone 1080p.mkv", "UpdateType": "Created"}]}), self.jf.calls)
        self.assertTrue(any(c.get("name") == "delete" for c in self.sab.calls))

    def test_ready_once_jellyfin_lists_the_version_and_the_requester_gets_a_pop_up(self):
        self.start()["jfItem"] = "jf-film"
        self.finish_download()
        pf.run(self.deps, self.state)
        self.jf.sources = [{"Id": "jf-film", "Path": f"/media/movies/{FOLDER}/server.mkv"}, {"Id": "jf-phone", "Path": f"/media/movies/{FOLDER}/{FOLDER} - Phone 1080p.mkv"}]
        self.jf.sessions = [{"Id": "s1", "DeviceId": "d1", "UserId": "u1"}]
        pf.run(self.deps, self.state)
        r = self.state["requests"]["radarr-12"]
        self.assertEqual((r["state"], r["phoneItem"]), ("ready", "jf-phone"))
        msg = [c for c in self.jf.calls if c[1] == "/Sessions/s1/Message"]
        self.assertEqual(len(msg), 1)
        self.assertIn("ready", msg[0][2]["Header"])

    def test_broken_download_fails_and_is_removed(self):
        self.start()
        job = self.finish_download(verdict="broken")
        pf.run(self.deps, self.state)
        r = self.state["requests"]["radarr-12"]
        self.assertEqual(r["state"], "failed")
        self.assertIn("no audio stream", r["note"])
        self.assertFalse(job.exists())
        self.assertFalse((self.hdd / "media").exists())

    def test_waits_while_the_video_is_not_there_yet(self):
        self.start()
        d = self.dl / "usenet/complete/phone/phone radarr-12 X"
        d.mkdir(parents=True)
        (d / "x.nfo").write_text("nfo")
        self.sab.history.append({"nzo_id": "SABnzbd_nzo_1", "status": "Completed", "storage": "/data/usenet/complete/phone/phone radarr-12 X"})
        pf.run(self.deps, self.state)
        self.assertEqual(self.state["requests"]["radarr-12"]["state"], "checking")
        self.assertTrue(d.exists())
        self.now += pf.CHECK_HOURS * 3600 + 1
        pf.run(self.deps, self.state)
        self.assertEqual((self.state["requests"]["radarr-12"]["state"], self.state["requests"]["radarr-12"]["note"]), ("failed", "no video file in the download"))

    def test_waits_for_the_quality_check(self):
        self.start()
        self.finish_download()
        self.results.write_text("")
        pf.run(self.deps, self.state)
        self.assertEqual(self.state["requests"]["radarr-12"]["state"], "checking")

    def test_sab_failure(self):
        self.start()
        self.sab.history.append({"nzo_id": "SABnzbd_nzo_1", "status": "Failed", "fail_message": "Aborted, cannot be completed"})
        pf.run(self.deps, self.state)
        r = self.state["requests"]["radarr-12"]
        self.assertEqual((r["state"], r["note"]), ("failed", "download failed: Aborted, cannot be completed"))

    def test_storage_outside_the_phone_folder_is_never_touched(self):
        self.start()
        other = self.dl / "usenet/complete/movies/X"
        other.mkdir(parents=True)
        (other / "x.mkv").write_text("x")
        self.sab.history.append({"nzo_id": "SABnzbd_nzo_1", "status": "Completed", "storage": "/data/usenet/complete/movies/X"})
        pf.run(self.deps, self.state)
        self.assertEqual(self.state["requests"]["radarr-12"]["state"], "failed")
        self.assertTrue((other / "x.mkv").exists())

    def test_episode_target_name(self):
        pf.request(self.deps, self.state, {"app": "sonarr", "id": 31}, {})
        self.finish_download(job="phone sonarr-31 A.Show", name="A.Show.S02E01.mkv")
        pf.run(self.deps, self.state)
        t = self.state["requests"]["sonarr-31"]["target"]
        self.assertEqual(t, str(self.hdd / "media/tv/A Show (2017) [tvdbid-99]/Season 02/A Show (2017) - S02E01 - Phone 1080p.mkv"))
        self.assertEqual(oct(pathlib.Path(t).parent.stat().st_mode & 0o7777), "0o2775")

    def test_guard_flags_a_phone_file_the_arr_took_over(self):
        r = self.start()
        r.update(state="ready", target=str(self.hdd / "media/movies" / FOLDER / f"{FOLDER} - Phone 1080p.mkv"), updated=pf.ts(NOW))
        self.radarr.tracked = f"/data/media/movies/{FOLDER}/{FOLDER} - Phone 1080p.mkv"
        self.now += pf.GUARD_HOURS * 3600 + 1
        pf.run(self.deps, self.state)
        self.assertIn("Radarr now tracks the phone version", r["note"])
        self.assertTrue(r["guard"])

    def test_guard_quiet_while_the_server_copy_is_tracked(self):
        r = self.start()
        r.update(state="ready", updated=pf.ts(NOW))
        self.now += pf.GUARD_HOURS * 3600 + 1
        pf.run(self.deps, self.state)
        self.assertEqual(r["note"], "")
        calls = len(self.radarr.calls)
        pf.run(self.deps, self.state)
        self.assertEqual(len(self.radarr.calls), calls)

    def test_inbox_requests_are_taken_and_removed(self):
        (self.box / "requests" / "x.json").write_text(json.dumps({"item": "jf-film", "user": "u1", "device": "d1"}))
        pf.run(self.deps, self.state)
        self.assertEqual(self.state["requests"]["radarr-12"]["state"], "downloading")
        self.assertEqual(list((self.box / "requests").iterdir()), [])

    def test_inbox_request_kept_as_retry_while_an_api_is_down(self):
        (self.box / "requests" / "x.json").write_text(json.dumps({"item": "jf-film"}))
        self.radarr.releases = None
        down = self.radarr.__call__

        def failing(method, path, query=None, body=None):
            if path == "/release":
                raise pf.lb.ApiError("GET /release: down")
            return down(method, path, query, body)
        self.deps.arr["radarr"] = failing
        pf.run(self.deps, self.state)
        self.assertEqual(sorted(p.name for p in (self.box / "requests").iterdir()), ["x.json.retry"])
        self.deps.arr["radarr"] = self.radarr
        self.radarr.releases = [release()]
        pf.run(self.deps, self.state)
        self.assertEqual(list((self.box / "requests").iterdir()), [])
        self.assertEqual(self.state["requests"]["radarr-12"]["state"], "downloading")

    def test_export_by_jellyfin_item(self):
        (self.box / "requests" / "x.json").write_text(json.dumps({"item": "jf-film", "user": "u1", "device": "d1"}))
        pf.run(self.deps, self.state)
        out = json.loads((self.box / "state.json").read_text())
        self.assertEqual(out["items"]["jf-film"]["state"], "downloading")
        self.assertNotIn("downloadUrl", json.dumps(out))


class Store(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as t:
            s = pf.load_state(pathlib.Path(t, "s.json"))
            s["requests"]["k"] = {"state": "ready"}
            pf.save_state(pathlib.Path(t, "s.json"), s)
            self.assertEqual(pf.load_state(pathlib.Path(t, "s.json"))["requests"], {"k": {"state": "ready"}})


if __name__ == "__main__":
    unittest.main()
