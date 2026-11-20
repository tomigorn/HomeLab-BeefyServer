#!/usr/bin/env python3
"""scripts/quality-block.py: a broken download is blocked before or after its import, notes and the duration rule never block, against a fake Radarr/Sonarr.
Usage: python3 tests/test_quality_block.py"""
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("qb", ROOT / "scripts" / "quality-block.py")
qb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qb)

DROPPED = "/data/usenet/complete/movies/A.2020.1080p-GRP/A.2020.1080p-GRP.mkv"
IMPORTED = "/data/media/movies/A (2020) [tmdbid-4242]/A (2020) - [WEBDL-1080p]-GRP.mkv"
MOVIE = {"id": 12, "title": "A Film", "year": 2020, "tmdbId": 4242, "runtime": 120, "tags": []}
SERIES = {"id": 5, "title": "A Show", "year": 2024, "tmdbId": 99, "runtime": 45, "tags": []}
NOW = 1_800_000_000.0


def line(verdict="broken", reasons=("timestamp jump 280.9 s -> 118.0 s",), notes=(), duration=7200.0, path=DROPPED, at="2027-01-15T08:00:00Z"):
    return {"at": at, "path": path, "title": "A.2020.1080p-GRP", "hash": None, "verdict": verdict, "reasons": list(reasons), "notes": list(notes), "duration": duration}


class FakeArr:
    def __init__(self, app="radarr", queue=(), imports=(), title=MOVIE, episodes=None, fail=()):
        self.app, self.calls, self.fail = app, [], set(fail)
        self.queue, self.imports, self.title, self.episodes = list(queue), list(imports), title, episodes or {}

    def __call__(self, method, path, query=None, body=None):
        self.calls.append((method, path, query, body))
        if (method, path.split("/")[1]) in self.fail:
            raise qb.lb.ApiError(f"{method} {path}: boom")
        if path == "/queue":
            return {"records": self.queue, "totalRecords": len(self.queue)}
        if path == "/history" and (query or {}).get("eventType") == 3:
            return {"records": self.imports, "totalRecords": len(self.imports)}
        if path == "/history":
            return {"records": [{"id": 501, "eventType": "grabbed", "downloadId": "DL1", "sourceTitle": "A.2020.1080p-GRP"}], "totalRecords": 1}
        if path in ("/movie/12", "/series/5"):
            return self.title
        if path.startswith("/episode/"):
            return self.episodes[int(path.rsplit("/", 1)[1])]
        if path == "/episode":
            return [{"id": e} for e in self.episodes]
        if path == "/config/mediamanagement":
            return {"recycleBin": "/data/.recyclebin"}
        if path == "/rootfolder":
            return [{"path": "/data/media/movies"}, {"path": "/data/media/tv"}]
        if path == "/moviefile":
            return [{"id": 77, "path": IMPORTED}]
        return {}

    def made(self, method, prefix):
        return [c for c in self.calls if c[0] == method and c[1].startswith(prefix)]


def imported(app="radarr", path=DROPPED, dl="DL1", **kw):
    r = {"id": 900, "eventType": "downloadFolderImported", "downloadId": dl, "sourceTitle": "A.2020.1080p-GRP", "data": {"droppedPath": path, "importedPath": IMPORTED}}
    r.update({"movieId": 12} if app == "radarr" else {"seriesId": 5, "episodeId": 31})
    r.update(kw)
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.host = lambda p: str(self.root / p.lstrip("/"))
        self.state = qb.load_state(self.root / "none.json")
        movie = self.root / IMPORTED.lstrip("/")
        movie.parent.mkdir(parents=True)
        movie.write_text("new")

    def tearDown(self):
        self.tmp.cleanup()

    def handle(self, apis, ln, imports=None, dry=False, action="block", now=NOW):
        return qb.handle(apis, ln, imports or {}, self.state, now=now, dry=dry, action=action, probe=lambda p: [{"lang": "eng", "name": "", "commentary": False}],
                         host=self.host)


class Broken(Base):
    def test_still_in_the_queue_is_removed_with_blocklist_and_never_imported(self):
        arr = FakeArr(queue=[{"id": 7, "movieId": 12, "downloadId": "DL1", "title": "A.2020.1080p-GRP", "outputPath": "/data/usenet/complete/movies/A.2020.1080p-GRP"}])
        status, e = self.handle({"radarr": arr, "sonarr": FakeArr("sonarr", title=SERIES)}, line())
        self.assertEqual(status, "done")
        self.assertEqual(arr.made("DELETE", "/queue/7")[0][2], {"removeFromClient": "true", "blocklist": "true", "skipRedownload": "false"})
        self.assertEqual((e["file"], e["blocked"], e["searched"], e["key"], e["release"]), ("never imported", True, True, "movie/4242", "A.2020.1080p-GRP"))
        self.assertEqual(e["reasons"], ["timestamp jump 280.9 s -> 118.0 s"])

    def test_queue_item_without_an_output_path_yet_is_not_matched(self):
        arr = FakeArr(queue=[{"id": 7, "movieId": 12, "downloadId": "DL1", "outputPath": None}])
        status, _ = self.handle({"radarr": arr}, line())
        self.assertEqual(status, "pending")
        self.assertEqual(arr.made("DELETE", "/queue"), [])

    def test_imported_new_file_is_removed_blocklisted_and_searched(self):
        arr = FakeArr(imports=[imported()])
        imports = {"radarr": [{"at": NOW - 60, "id": "12", "path": IMPORTED, "fileId": "77", "downloadId": "DL1", "upgrade": False, "deleted": [], "recycled": []}]}
        status, e = self.handle({"radarr": arr}, line(), imports)
        self.assertEqual(status, "done")
        self.assertEqual(len(arr.made("DELETE", "/moviefile/77")), 1)
        self.assertEqual(len(arr.made("POST", "/history/failed/501")), 1)
        self.assertEqual(arr.made("POST", "/command")[-1][3], {"name": "MoviesSearch", "movieIds": [12]})
        self.assertEqual((e["file"], e["blocked"], e["searched"], e["fileName"]), ("removed", True, True, "A (2020) - [WEBDL-1080p]-GRP.mkv"))

    def test_imported_upgrade_puts_the_previous_file_back(self):
        old = self.root / "data/.recyclebin/A (2020) [tmdbid-4242]/A (2020) - old.mkv"
        old.parent.mkdir(parents=True)
        old.write_text("old")
        deleted = "/data/media/movies/A (2020) [tmdbid-4242]/A (2020) - old.mkv"
        arr = FakeArr(imports=[imported()])
        imports = {"radarr": [{"at": NOW - 60, "id": "12", "path": IMPORTED, "fileId": "77", "downloadId": "DL1", "upgrade": True, "deleted": [deleted], "recycled": []}]}
        status, e = self.handle({"radarr": arr}, line(), imports)
        self.assertEqual(e["file"], "previous restored")
        self.assertTrue(pathlib.Path(self.host(deleted)).exists())

    def test_imported_without_an_import_log_line_finds_the_file_id(self):
        arr = FakeArr(imports=[imported()])
        status, e = self.handle({"radarr": arr}, line())
        self.assertEqual(len(arr.made("DELETE", "/moviefile/77")), 1)
        self.assertIn("no import log line", e["note"])

    def test_sonarr_episode(self):
        arr = FakeArr("sonarr", imports=[imported("sonarr")], title=SERIES, episodes={31: {"id": 31, "runtime": 45}})
        imports = {"sonarr": [{"at": NOW - 60, "id": "5", "path": IMPORTED, "fileId": "88", "downloadId": "DL1", "upgrade": False, "deleted": [], "recycled": []}]}
        status, e = self.handle({"radarr": FakeArr(), "sonarr": arr}, line(), imports)
        self.assertEqual(len(arr.made("DELETE", "/episodefile/88")), 1)
        self.assertEqual(arr.made("POST", "/command")[-1][3], {"name": "EpisodeSearch", "episodeIds": [31]})
        self.assertEqual(e["key"], "tv/99")

    def test_hand_import_is_not_blocked(self):
        arr = FakeArr(imports=[imported(dl=None)])
        status, e = self.handle({"radarr": arr}, line())
        self.assertEqual((status, e["blocked"], e["file"]), ("done", False, "kept"))
        self.assertEqual(arr.made("DELETE", "/"), [])

    def test_the_test_film_is_never_touched(self):
        arr = FakeArr(imports=[imported()], title=dict(MOVIE, tmdbId=653))
        status, e = self.handle({"radarr": arr}, line())
        self.assertEqual((e["blocked"], e["file"]), (False, "kept"))
        self.assertEqual(arr.made("DELETE", "/") + arr.made("POST", "/"), [])

    def test_log_mode_decides_only(self):
        arr = FakeArr(queue=[{"id": 7, "movieId": 12, "downloadId": "DL1", "outputPath": "/data/usenet/complete/movies/A.2020.1080p-GRP"}])
        status, e = self.handle({"radarr": arr}, line(), action="log")
        self.assertEqual((status, e["file"], e["blocked"]), ("done", "log only", False))
        self.assertEqual(arr.made("DELETE", "/"), [])

    def test_not_found_waits_then_gives_up_with_a_note(self):
        arr = FakeArr()
        self.assertEqual(self.handle({"radarr": arr}, line())[0], "pending")
        status, e = self.handle({"radarr": arr}, line(), now=NOW + qb.PENDING_HOURS * 3600 + 1)
        self.assertEqual((status, e["file"], e["blocked"]), ("done", "not found", False))

    def test_phone_versions_are_left_to_phone_fetch(self):
        arr = FakeArr()
        self.assertEqual(self.handle({"radarr": arr}, line(path="/data/usenet/complete/phone/phone radarr-12 X/X.mkv")), ("done", None))
        self.assertEqual(arr.calls, [])

    def test_api_down_stays_pending(self):
        arr = FakeArr(fail={("GET", "queue")})
        self.assertEqual(self.handle({"radarr": arr}, line())[0], "pending")


class Notes(Base):
    def test_ok_without_notes_leaves_no_entry(self):
        status, e = self.handle({"radarr": FakeArr(imports=[imported()])}, line("ok", reasons=()))
        self.assertEqual((status, e), ("done", None))

    def test_container_error_is_a_note_only(self):
        arr = FakeArr(imports=[imported()])
        status, e = self.handle({"radarr": arr}, line("ok", reasons=(), notes=("container error: [matroska,webm] File ended prematurely",)))
        self.assertEqual((e["verdict"], e["blocked"], e["file"]), ("note", False, "kept"))
        self.assertEqual(arr.made("DELETE", "/") + arr.made("POST", "/"), [])

    def test_duration_outside_ten_percent_and_five_minutes_is_a_note(self):
        status, e = self.handle({"radarr": FakeArr(imports=[imported()])}, line("ok", reasons=(), duration=90 * 60))
        self.assertEqual(e["notes"], ["duration 90 min, expected 120 min"])

    def test_duration_inside_the_tolerance(self):
        for minutes, runtime in ((109, 120), (126, 120)):
            status, e = self.handle({"radarr": FakeArr(imports=[imported()], title=dict(MOVIE, runtime=runtime))}, line("ok", reasons=(), duration=minutes * 60))
            self.assertIsNone(e, minutes)

    def test_short_runtime_uses_five_minutes(self):
        title = dict(SERIES)
        arr = FakeArr("sonarr", imports=[imported("sonarr")], title=title, episodes={31: {"id": 31, "runtime": 22}})
        self.assertIsNone(self.handle({"sonarr": arr}, line("ok", reasons=(), duration=26 * 60))[1])
        self.assertEqual(self.handle({"sonarr": arr}, line("ok", reasons=(), duration=28 * 60))[1]["notes"], ["duration 28 min, expected 22 min"])

    def test_multi_episode_file_sums_the_runtimes(self):
        arr = FakeArr("sonarr", imports=[imported("sonarr"), imported("sonarr", episodeId=32)], title=SERIES, episodes={31: {"id": 31, "runtime": 45}, 32: {"id": 32, "runtime": 45}})
        self.assertIsNone(self.handle({"sonarr": arr}, line("ok", reasons=(), duration=90 * 60))[1])

    def test_no_runtime_known_skips_the_rule(self):
        self.assertIsNone(self.handle({"radarr": FakeArr(imports=[imported()], title=dict(MOVIE, runtime=0))}, line("ok", reasons=(), duration=10))[1])

    def test_error_lines_are_skipped(self):
        self.assertEqual(self.handle({"radarr": FakeArr()}, line("error", reasons=())), ("done", None))


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_results_are_read_once_across_the_rotated_file(self):
        res = self.root / "quality-results.jsonl"
        (self.root / "quality-results.jsonl.1").write_text(json.dumps(line(at="2027-01-15T07:00:00Z")) + "\n")
        res.write_text(json.dumps(line(at="2027-01-15T08:00:00Z")) + "\n" + "not json\n")
        state = qb.load_state(self.root / "s.json")
        self.assertEqual([x["at"] for x in qb.new_lines(res, state)], ["2027-01-15T07:00:00Z", "2027-01-15T08:00:00Z"])
        state["seen"][qb.line_key(line(at="2027-01-15T07:00:00Z"))] = "x"
        self.assertEqual([x["at"] for x in qb.new_lines(res, state)], ["2027-01-15T08:00:00Z"])

    def test_import_log_is_parsed_and_pruned(self):
        log = self.root / "quality-block.imports"
        log.write_text(f"{int(NOW) - 20 * 86400}\t12\t/old.mkv\t1\tX\tFalse\t\t\n{int(NOW) - 60}\t12\t{IMPORTED}\t77\tDL1\tTrue\t/a.mkv\t/bin/a.mkv\n")
        items = qb.read_imports(log, now=NOW, prune=True)
        self.assertEqual([(i["path"], i["fileId"], i["upgrade"], i["deleted"]) for i in items], [(IMPORTED, "77", True, ["/a.mkv"])])
        self.assertNotIn("/old.mkv", log.read_text())

    def test_state_round_trip(self):
        state = qb.load_state(self.root / "s.json")
        state["seen"]["k"] = "2027-01-15T08:00:00Z"
        qb.save_state(self.root / "s.json", state)
        self.assertEqual(qb.load_state(self.root / "s.json")["seen"], {"k": "2027-01-15T08:00:00Z"})


if __name__ == "__main__":
    unittest.main()
