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


HDR_WEB = "A.Film.2026.1080p.WEB-DL.DDP5.1.HDR-GRP"


def release(title=HDR_WEB, name="WEBDL-1080p", res=1080, size=8 * GB, protocol="usenet", cfs=(), score=100):
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
            return {"status": True, "nzo_ids": [f"SABnzbd_nzo_{sum(c['mode'] == 'addurl' for c in self.calls)}"]}
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
        self.radarr = FakeArr(releases=[release(cfs=["HDR"])])
        self.sonarr = FakeArr("sonarr", releases=[release("A.Show.S02E01.1080p.NF.WEB-DL.HDR.HEVC-GRP", cfs=["HDR"], size=2 * GB)])
        self.sab, self.jf = FakeSab(), FakeJf()
        self.now = NOW
        self.deps = pf.Deps(arr={"radarr": self.radarr, "sonarr": self.sonarr}, sab=self.sab, jf=self.jf, dl_host=str(self.dl), hdd=str(self.hdd),
                            results=str(self.results), box=str(self.box), clock=lambda: self.now)
        self.state = pf.load_state(self.root / "none.json")

    def tearDown(self):
        self.tmp.cleanup()

    def finish_download(self, verdict="ok", job="phone radarr-12 " + HDR_WEB, name=HDR_WEB + ".mkv", nzo="SABnzbd_nzo_1"):
        d = self.dl / "usenet/complete/phone" / job
        d.mkdir(parents=True)
        (d / name).write_bytes(b"x" * 1000)
        (d / "a.nfo").write_text("nfo")
        self.sab.history.append({"nzo_id": nzo, "status": "Completed", "storage": f"/data/usenet/complete/phone/{job}"})
        with open(self.results, "a") as f:
            f.write(json.dumps({"at": "2027-01-15T08:00:00Z", "path": f"/data/usenet/complete/phone/{job}/{name}", "verdict": verdict,
                                "reasons": [] if verdict == "ok" else ["no audio stream"], "notes": [], "duration": 7200.0}) + "\n")
        return d


class Request(Base):
    def test_movie_request_sends_the_pick_to_sab_category_phone(self):
        r = pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {"user": "u1", "device": "d1"})
        self.assertEqual((r["state"], r["option"], r["nzo"], r["release"], r["tried"]), ("downloading", 1, "SABnzbd_nzo_1", HDR_WEB, [HDR_WEB]))
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
        self.radarr.releases = [release(protocol="torrent", cfs=["HDR"])]
        r = pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})
        self.assertEqual(r["state"], "queued")
        self.assertEqual(self.sab.calls, [])

    def test_server_copy_not_4k_needs_nothing(self):
        self.radarr.resolution = 1080
        r = pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})
        self.assertEqual((r["state"], r["note"]), ("not-needed", "the server copy is not 4K"))
        self.assertNotIn("/release", [c[1] for c in self.radarr.calls])

    def test_nothing_fits_queues_an_own_conversion_of_the_server_copy(self):
        self.radarr.releases = [release()]
        r = pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})
        self.assertEqual((r["state"], r["option"], r["percent"]), ("queued", 3, 0))
        job = json.loads((self.dl / "usenet/complete/phone/convert/radarr-12/job.json").read_text())
        self.assertEqual((job["key"], job["src"]), ("radarr-12", self.radarr.tracked))
        self.assertEqual(oct((self.dl / "usenet/complete/phone/convert/radarr-12").stat().st_mode & 0o7777), "0o2775")

    def test_a_failed_title_is_asked_again_after_a_day(self):
        r = pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})
        r.update(state="failed", updated=pf.ts(NOW))
        self.assertEqual(pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})["state"], "failed")
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
        self.assertEqual(r["state"], "queued")
        self.assertIn("no audio stream", r["failures"][0])
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
        self.assertEqual((self.state["requests"]["radarr-12"]["state"], self.state["requests"]["radarr-12"]["failures"]), ("queued", ["no video file in the download"]))

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
        self.assertEqual((r["state"], r["failures"]), ("queued", ["download failed: Aborted, cannot be completed"]))

    def test_storage_outside_the_phone_folder_is_never_touched(self):
        self.start()
        other = self.dl / "usenet/complete/movies/X"
        other.mkdir(parents=True)
        (other / "x.mkv").write_text("x")
        self.sab.history.append({"nzo_id": "SABnzbd_nzo_1", "status": "Completed", "storage": "/data/usenet/complete/movies/X"})
        pf.run(self.deps, self.state)
        self.assertEqual(self.state["requests"]["radarr-12"]["failures"], ["finished outside the phone folder: /data/usenet/complete/movies/X"])
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
        self.radarr.releases = [release(cfs=["HDR"])]
        pf.run(self.deps, self.state)
        self.assertEqual(list((self.box / "requests").iterdir()), [])
        self.assertEqual(self.state["requests"]["radarr-12"]["state"], "downloading")

    def test_export_by_jellyfin_item(self):
        (self.box / "requests" / "x.json").write_text(json.dumps({"item": "jf-film", "user": "u1", "device": "d1"}))
        pf.run(self.deps, self.state)
        out = json.loads((self.box / "state.json").read_text())
        self.assertEqual(out["items"]["jf-film"]["state"], "downloading")
        self.assertNotIn("downloadUrl", json.dumps(out))

    def test_export_names_who_asked_and_when(self):
        (self.box / "requests" / "x.json").write_text(json.dumps({"item": "jf-film", "user": "u1", "device": "d1"}))
        pf.run(self.deps, self.state)
        x = json.loads((self.box / "state.json").read_text())["items"]["jf-film"]
        at = self.state["requests"]["radarr-12"]["at"]
        self.assertEqual((x["at"], x["requestedBy"], x["note"]), (at, [{"user": "u1", "at": at}], ""))


class Retry(Base):
    def setUp(self):
        super().setUp()
        self.radarr.releases = [release(f"A.Film.2026.1080p.WEB-DL.HDR-G{i}", cfs=["HDR"], score=900 - i) for i in range(1, 5)]

    def test_a_failed_download_takes_the_next_fitting_release_at_once(self):
        pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})
        self.sab.history.append({"nzo_id": "SABnzbd_nzo_1", "status": "Failed", "fail_message": "missing articles"})
        pf.run(self.deps, self.state)
        r = self.state["requests"]["radarr-12"]
        self.assertEqual((r["state"], r["nzo"], r["release"], r["percent"]), ("downloading", "SABnzbd_nzo_2", "A.Film.2026.1080p.WEB-DL.HDR-G2", 0))
        self.assertEqual(r["tried"], ["A.Film.2026.1080p.WEB-DL.HDR-G1", "A.Film.2026.1080p.WEB-DL.HDR-G2"])

    def test_a_failed_check_takes_the_next_release_too(self):
        pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})
        self.finish_download(verdict="broken", job="phone radarr-12 G1", name="G1.mkv")
        pf.run(self.deps, self.state)
        self.assertEqual(self.state["requests"]["radarr-12"]["release"], "A.Film.2026.1080p.WEB-DL.HDR-G2")

    def test_after_three_releases_the_server_copy_is_converted(self):
        pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})
        for n in (1, 2, 3):
            self.sab.history.append({"nzo_id": f"SABnzbd_nzo_{n}", "status": "Failed", "fail_message": f"f{n}"})
            pf.run(self.deps, self.state)
        r = self.state["requests"]["radarr-12"]
        self.assertEqual((r["state"], r["option"], r["failures"]), ("queued", 3, ["download failed: f1", "download failed: f2", "download failed: f3"]))
        self.assertEqual(len([c for c in self.sab.calls if c["mode"] == "addurl"]), 3)

    def test_a_search_that_breaks_during_a_retry_is_repeated_next_run(self):
        pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {})
        self.sab.history.append({"nzo_id": "SABnzbd_nzo_1", "status": "Failed", "fail_message": "x"})
        ok = self.radarr.__call__

        def failing(method, path, query=None, body=None):
            if path == "/release":
                raise pf.lb.ApiError("GET /release: down")
            return ok(method, path, query, body)
        self.deps.arr["radarr"] = failing
        pf.run(self.deps, self.state)
        self.assertEqual(self.state["requests"]["radarr-12"]["state"], "searching")
        self.deps.arr["radarr"] = self.radarr
        pf.run(self.deps, self.state)
        self.assertEqual(self.state["requests"]["radarr-12"]["release"], "A.Film.2026.1080p.WEB-DL.HDR-G2")


class Converting(Base):
    def setUp(self):
        super().setUp()
        self.radarr.releases = []
        self.r = pf.request(self.deps, self.state, {"app": "radarr", "id": 12}, {"user": "u1", "device": "d1"})
        self.job = self.dl / "usenet/complete/phone/convert/radarr-12"

    def test_queued_until_the_converter_starts(self):
        pf.run(self.deps, self.state)
        self.assertEqual((self.r["state"], self.r["percent"]), ("queued", 0))

    def test_percent_from_the_ffmpeg_progress(self):
        (self.job / "status.json").write_text(json.dumps({"duration": 7200.0}))
        (self.job / "progress").write_text("frame=10\nout_time_us=1000000000\nprogress=continue\nframe=20\nout_time_us=1800000000\nprogress=continue\n")
        pf.run(self.deps, self.state)
        self.assertEqual((self.r["state"], self.r["percent"]), ("converting", 25))
        export = json.loads((self.box / "state.json").read_text())
        self.assertEqual(export["items"].get(self.r.get("jfItem"), {}).get("state", "converting"), "converting")

    def test_percent_counts_frames_when_known(self):
        (self.job / "status.json").write_text(json.dumps({"duration": 7200.0, "frames": 172800}))
        (self.job / "progress").write_text("frame=86400\nout_time_us=1000000000\nprogress=continue\n")
        pf.run(self.deps, self.state)
        self.assertEqual((self.r["state"], self.r["percent"]), ("converting", 50))

    def test_a_finished_conversion_is_placed_like_a_download(self):
        (self.job / "phone.mkv").write_bytes(b"c" * 500)
        (self.job / "result.json").write_text(json.dumps({"ok": True, "file": "phone.mkv"}))
        pf.run(self.deps, self.state)
        target = self.hdd / "media/movies" / FOLDER / f"{FOLDER} - Phone 1080p.mkv"
        self.assertEqual((self.r["state"], target.read_bytes()), ("placing", b"c" * 500))
        self.assertFalse(self.job.exists())
        self.assertIn(("POST", "/Library/Media/Updated", {"Updates": [{"Path": f"/media/movies/{FOLDER}/{FOLDER} - Phone 1080p.mkv", "UpdateType": "Created"}]}), self.jf.calls)

    def test_a_failed_conversion_fails_and_is_removed(self):
        (self.job / "result.json").write_text(json.dumps({"ok": False, "why": "ffmpeg exit 1"}))
        pf.run(self.deps, self.state)
        self.assertEqual((self.r["state"], self.r["note"]), ("failed", "conversion failed: ffmpeg exit 1"))
        self.assertFalse(self.job.exists())

    def test_a_skipped_conversion_means_no_smaller_version(self):
        (self.job / "result.json").write_text(json.dumps({"ok": False, "skip": True, "why": "Dolby Vision without an HDR10 layer"}))
        pf.run(self.deps, self.state)
        self.assertEqual((self.r["state"], self.r["note"]), ("nothing", "Dolby Vision without an HDR10 layer"))

    def test_a_vanished_job_fails(self):
        import shutil
        shutil.rmtree(self.job)
        pf.run(self.deps, self.state)
        self.assertEqual((self.r["state"], self.r["note"]), ("failed", "the conversion job vanished"))


def stream(i, kind, codec, lang=None, ch=None, **kw):
    s = {"index": i, "codec_type": kind, "codec_name": codec, "tags": {"language": lang} if lang else {}, "disposition": {"attached_pic": 0}}
    if ch:
        s["channels"] = ch
    s.update(kw)
    return s


HDR_VIDEO = stream(0, "video", "hevc", color_transfer="smpte2084", color_primaries="bt2020", color_space="bt2020nc", avg_frame_rate="24000/1001")


def probe(*streams, duration="7200.000"):
    return {"format": {"duration": duration}, "streams": list(streams)}


class Plan(unittest.TestCase):
    def args(self, p):
        got = pf.plan(p)
        self.assertNotIn("skip", got)
        return got["input"] + got["args"]

    def test_the_owner_pipeline(self):
        a = " ".join(self.args(probe(HDR_VIDEO, stream(1, "audio", "eac3", "eng", 6))))
        for part in ("-init_hw_device vaapi=va:,vendor_id=0x8086,driver=iHD", "-init_hw_device qsv=qs@va", "-filter_hw_device qs", "-hwaccel vaapi", "-hwaccel_output_format vaapi",
                     "scale_vaapi=w=1920:h=-2:format=p010,hwmap=derive_device=qsv,format=qsv", "-c:v hevc_qsv", "-low_power 1", "-preset slow", "-b:v 5M", "-maxrate 7500k", "-bufsize 15M",
                     "-fps_mode passthrough", "-color_trc smpte2084", "-color_primaries bt2020", "-colorspace bt2020nc", "-map_chapters 0", "-map 0:0"):
            self.assertIn(part, a)

    def test_lossy_tracks_are_copied_lossless_ones_dropped(self):
        a = self.args(probe(HDR_VIDEO, stream(1, "audio", "truehd", "eng", 8), stream(2, "audio", "ac3", "eng", 6), stream(3, "audio", "eac3", "deu", 6), stream(4, "audio", "aac", "eng", 2)))
        maps = [a[i + 1] for i, x in enumerate(a) if x == "-map"]
        self.assertEqual(maps[:4], ["0:0", "0:2", "0:3", "0:4"])
        self.assertNotIn("eac3", [a[i + 1] for i, x in enumerate(a) if x.startswith("-c:a:")])

    def test_a_language_without_a_lossy_track_gets_its_best_track_as_eac3_640k(self):
        a = self.args(probe(HDR_VIDEO, stream(1, "audio", "dts", "eng", 6, profile="DTS-HD MA"), stream(2, "audio", "truehd", "eng", 8), stream(3, "audio", "eac3", "deu", 6)))
        maps = [a[i + 1] for i, x in enumerate(a) if x == "-map"]
        self.assertEqual(maps[:3], ["0:0", "0:2", "0:3"])
        j = " ".join(a)
        self.assertIn("-c:a:0 eac3 -b:a:0 640k -ac:a:0 6", j)
        self.assertIn("-c:a:1 copy", j)

    def test_subtitles_and_fonts_are_copied(self):
        a = self.args(probe(HDR_VIDEO, stream(1, "audio", "ac3", "eng", 6), stream(2, "subtitle", "subrip", "eng"), stream(3, "subtitle", "hdmv_pgs_subtitle", "deu"),
                            stream(4, "subtitle", "mov_text", "fra"), stream(5, "attachment", "ttf")))
        maps = [a[i + 1] for i, x in enumerate(a) if x == "-map"]
        self.assertEqual(maps, ["0:0", "0:1", "0:2", "0:3", "0:5"])
        self.assertIn("-c:s", a)
        self.assertIn("-c:t", a)

    def test_a_cover_image_is_no_video(self):
        cover = stream(0, "video", "mjpeg", disposition={"attached_pic": 1})
        a = self.args(probe(cover, dict(HDR_VIDEO, index=1), stream(2, "audio", "ac3", "eng", 6)))
        self.assertEqual([a[i + 1] for i, x in enumerate(a) if x == "-map"][0], "0:1")

    def test_dolby_vision_without_hdr10_layer_is_skipped(self):
        dv5 = dict(HDR_VIDEO, color_transfer=None, side_data_list=[{"side_data_type": "DOVI configuration record", "dv_profile": 5, "dv_bl_signal_compatibility_id": 0}])
        got = pf.plan(probe(dv5, stream(1, "audio", "eac3", "eng", 6)))
        self.assertEqual((got["skip"], got["why"]), (True, "Dolby Vision without an HDR10 layer"))

    def test_dolby_vision_with_hdr10_layer_converts(self):
        dv8 = dict(HDR_VIDEO, side_data_list=[{"side_data_type": "DOVI configuration record", "dv_profile": 8, "dv_bl_signal_compatibility_id": 1}])
        self.assertIn("-c:v", self.args(probe(dv8, stream(1, "audio", "eac3", "eng", 6))))

    def test_odd_colour_values_from_the_file_are_not_passed_on(self):
        a = self.args(probe(dict(HDR_VIDEO, color_primaries="bt2020 -y /etc/x"), stream(1, "audio", "ac3", "eng", 6)))
        self.assertNotIn("-color_primaries", a)


class Check(unittest.TestCase):
    def test_duration_within_one_percent_and_streams_present(self):
        src = probe(HDR_VIDEO, duration="7200.0")
        good = probe(HDR_VIDEO, stream(1, "audio", "eac3", "eng", 6), duration="7140.0")
        self.assertIsNone(pf.check(src, good))
        self.assertIn("duration", pf.check(src, probe(HDR_VIDEO, stream(1, "audio", "eac3"), duration="7100.0")))
        self.assertIn("audio", pf.check(src, probe(HDR_VIDEO, duration="7200.0")))
        self.assertIn("video", pf.check(src, probe(stream(1, "audio", "eac3"), duration="7200.0")))


class Converter(Base):
    def setUp(self):
        super().setUp()
        self.media = self.root / "media"
        (self.media / "movies" / FOLDER).mkdir(parents=True)
        (self.media / "movies" / FOLDER / "server.mkv").write_bytes(b"s")
        self.runs, self.broken = [], None
        self.deps.media_host = str(self.media)
        self.deps.docker = self.docker
        self.deps.image = lambda: "jellyfin-local:test"

    def docker(self, argv):
        self.runs.append(argv)
        if "ffprobe" in " ".join(argv):
            dur = "7200.0"
            return 0, json.dumps(probe(HDR_VIDEO, stream(1, "audio", "eac3", "eng", 6), duration=dur)), ""
        out = pathlib.Path(argv[argv.index("-v", argv.index("-v") + 1) + 1].split(":")[0])
        if self.broken:
            (out / "phone.partial.mkv").write_bytes(b"half")
            return 1, "", self.broken
        (out / "phone.partial.mkv").write_bytes(b"c")
        return 0, "", ""

    def queue(self, key="radarr-12", src=f"/data/media/movies/{FOLDER}/server.mkv", at="2027-01-01T00:00:00Z"):
        d = self.dl / "usenet/complete/phone/convert" / key
        d.mkdir(parents=True)
        (d / "job.json").write_text(json.dumps({"key": key, "src": src, "at": at}))
        return d

    def test_a_job_is_converted_in_a_throwaway_container(self):
        d = self.queue()
        pf.convert(self.deps)
        self.assertEqual(json.loads((d / "result.json").read_text()), {"ok": True, "file": "phone.mkv"})
        self.assertEqual((d / "phone.mkv").read_bytes(), b"c")
        self.assertFalse((d / "job.json").exists())
        self.assertEqual((d.parent / ".done").read_text(), "radarr-12")
        self.assertEqual(json.loads((d / "status.json").read_text()), {"duration": 7200.0, "frames": 172627})
        run = [a for a in self.runs if "ffmpeg" in " ".join(a) and "ffprobe" not in " ".join(a)][0]
        j = " ".join(run)
        for part in ("run --rm --name phone-convert", "--network none", "--device /dev/dri", f"-v {self.media}:/media:ro", f"-v {d}:/out", "jellyfin-local:test", "-progress /out/progress",
                     f"/media/movies/{FOLDER}/server.mkv", "/out/phone.partial.mkv"):
            self.assertIn(part, j)
        self.assertIn("--user", run)

    def test_a_broken_conversion_leaves_no_partial_file(self):
        d = self.queue()
        self.broken = "Error while opening encoder"
        pf.convert(self.deps)
        res = json.loads((d / "result.json").read_text())
        self.assertFalse(res["ok"])
        self.assertIn("Error while opening encoder", res["why"])
        self.assertFalse((d / "phone.partial.mkv").exists())

    def test_oldest_job_first_and_all_of_them(self):
        b = self.queue("radarr-13", at="2027-01-02T00:00:00Z")
        a = self.queue("radarr-12", at="2027-01-01T00:00:00Z")
        pf.convert(self.deps)
        srcs = [a_ for a_ in self.runs if "ffmpeg" in " ".join(a_) and "ffprobe" not in " ".join(a_)]
        self.assertEqual(len(srcs), 2)
        self.assertIn(str(a), " ".join(srcs[0]))
        self.assertTrue((b / "result.json").exists())

    def test_a_job_outside_the_library_is_refused(self):
        d = self.queue(src="/data/media/../../etc/shadow")
        pf.convert(self.deps)
        self.assertFalse(json.loads((d / "result.json").read_text())["ok"])
        self.assertEqual(self.runs, [])

    def test_a_folder_not_named_like_a_job_is_ignored(self):
        d = self.queue("x:ro,y")
        (d / "job.json").write_text(json.dumps({"key": "radarr-12", "src": f"/data/media/movies/{FOLDER}/server.mkv", "at": "2027-01-01T00:00:00Z"}))
        pf.convert(self.deps)
        self.assertEqual(self.runs, [])

    def test_a_job_interrupted_by_a_crash_fails(self):
        d = self.queue()
        (d / "job.json").rename(d / "running.json")
        pf.convert(self.deps)
        self.assertEqual(json.loads((d / "result.json").read_text()), {"ok": False, "why": "interrupted"})

    def test_a_skipped_title_is_not_converted(self):
        d = self.queue()
        dv5 = dict(HDR_VIDEO, side_data_list=[{"side_data_type": "DOVI configuration record", "dv_profile": 5, "dv_bl_signal_compatibility_id": 0}])
        self.deps.docker = lambda argv: (self.runs.append(argv), (0, json.dumps(probe(dv5, stream(1, "audio", "eac3", "eng", 6))), ""))[1]
        pf.convert(self.deps)
        self.assertEqual(json.loads((d / "result.json").read_text())["skip"], True)
        self.assertEqual(len(self.runs), 1)


class Store(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as t:
            s = pf.load_state(pathlib.Path(t, "s.json"))
            s["requests"]["k"] = {"state": "ready"}
            pf.save_state(pathlib.Path(t, "s.json"), s)
            self.assertEqual(pf.load_state(pathlib.Path(t, "s.json"))["requests"], {"k": {"state": "ready"}})


if __name__ == "__main__":
    unittest.main()
