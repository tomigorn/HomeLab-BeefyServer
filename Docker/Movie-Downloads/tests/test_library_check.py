#!/usr/bin/env python3
"""scripts/library-check.py: the one-off library check (README "Library check"): walk, resume, pause on playback and in the night window, report only; fakes for ffprobe, Jellyfin and Radarr/Sonarr.
Real samples (root, docker): LC_REAL=1 python3 tests/test_library_check.py Real
Usage: python3 tests/test_library_check.py"""
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("lc", ROOT / "scripts" / "library-check.py")
lc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lc)

FILM = "movies/A Film (2020) [tmdbid-4242]/A Film (2020) - [WEBDL-1080p].mkv"
EPISODE = "tv/A Show (2024) [tvdbid-77]/Season 01/A Show (2024) - S01E08 - Pilot.mkv"
OTHER = "movies/B Film (2019) [tmdbid-5151]/B Film (2019).mp4"
NOW = 1_800_000_000.0  # 2027-01-15 08:00 UTC, 09:00 in Zurich


def local(h, m):
    return time.mktime(time.strptime(f"2027-01-15 {h:02d}:{m:02d}:00", "%Y-%m-%d %H:%M:%S"))


class FakeArr:
    def __init__(self, app):
        self.app, self.calls = app, []

    def __call__(self, method, path, query=None, body=None):
        self.calls.append((method, path))
        if method != "GET":
            raise AssertionError("report only: no writes")
        if path == "/movie":
            return [{"id": 12, "title": "A Film", "year": 2020, "tmdbId": 4242, "runtime": 120, "hasFile": True, "movieFile": {"path": "/data/media/" + FILM}},
                    {"id": 13, "title": "Not Here", "year": 2021, "tmdbId": 1, "runtime": 90, "hasFile": False}]
        if path == "/series":
            return [{"id": 5, "title": "A Show", "year": 2024, "tmdbId": 99, "tvdbId": 77}]
        if path == "/episodefile":
            return [{"id": 501, "path": "/data/media/" + EPISODE}]
        if path == "/episode":
            return [{"id": 1, "episodeFileId": 501, "runtime": 45}, {"id": 2, "episodeFileId": 0, "runtime": 45}]
        raise AssertionError(path)


class World:
    """Branches with invented files, fake checks, fake Jellyfin sessions and a clock that moves with every sleep."""

    def __init__(self, files=(FILM, EPISODE, OTHER), playing=(), clock=NOW):
        self.t = tempfile.mkdtemp()
        self.branches = [os.path.join(self.t, "ssd-hot"), os.path.join(self.t, "ssd-hot2")]
        for i, rel in enumerate(files):
            self.put(self.branches[i % 2], rel, b"x" * (10 + i))
        self.state = os.path.join(self.t, "state", "library-check.json")
        self.checked, self.sleeps, self.playing, self.now = [], [], list(playing), clock
        self.results = {}

    def put(self, branch, rel, data):
        p = os.path.join(branch, "media", rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def check(self, path):
        self.checked.append(path)
        return dict(self.results.get(path) or {"verdict": "ok", "reasons": [], "notes": [], "duration": None})

    def sessions(self):
        return self.playing.pop(0) if self.playing else []

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s

    def deps(self, apis=None):
        return lc.Deps(branches=self.branches, state=self.state, check=self.check, sessions=self.sessions, clock=lambda: self.now, sleep=self.sleep,
                       apis={"radarr": FakeArr("radarr"), "sonarr": FakeArr("sonarr")} if apis is None else apis)

    def load(self):
        with open(self.state) as f:
            return json.load(f)


class Walk(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(shutil.rmtree, self.w.t)

    def test_videos_of_both_ssds_as_the_apps_see_them(self):
        self.w.put(self.w.branches[0], "movies/A Film (2020) [tmdbid-4242]/A Film (2020).en.srt", b"1")
        got = [p for p, _ in lc.walk(self.w.branches)]
        self.assertEqual(got, sorted("/data/media/" + r for r in (FILM, EPISODE, OTHER)))

    def test_same_path_on_two_branches_counts_once(self):
        self.w.put(self.w.branches[1], FILM, b"dup")
        self.assertEqual(len(lc.walk(self.w.branches)), 3)

    def test_missing_branch_is_skipped(self):
        self.assertEqual(len(lc.walk(self.w.branches + [os.path.join(self.w.t, "gone")])), 3)


class Titles(unittest.TestCase):
    def test_film_and_episode_with_runtime(self):
        t = lc.titles({"radarr": FakeArr("radarr"), "sonarr": FakeArr("sonarr")})
        self.assertEqual(t["/data/media/" + FILM], {"app": "radarr", "key": "movie/4242", "title": "A Film", "year": 2020, "minutes": 120})
        self.assertEqual(t["/data/media/" + EPISODE], {"app": "sonarr", "key": "tv/99", "title": "A Show", "year": 2024, "minutes": 45})

    def test_unreachable_app_gives_no_titles(self):
        def down(*a, **k):
            raise lc.lb.ApiError("down")
        self.assertEqual(lc.titles({"radarr": down}), {})

    def test_folder_name_when_the_apps_do_not_know_the_file(self):
        self.assertEqual(lc.folder_title("/data/media/" + OTHER), {"app": None, "key": None, "title": "B Film", "year": 2019, "minutes": None})
        self.assertEqual(lc.folder_title("/data/media/" + EPISODE)["title"], "A Show")


class Run(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(shutil.rmtree, self.w.t)

    def test_every_file_once_with_title_and_progress(self):
        lc.run(self.w.deps())
        s = self.w.load()
        self.assertEqual(sorted(self.w.checked), sorted("/data/media/" + r for r in (FILM, EPISODE, OTHER)))
        self.assertEqual((s["run"]["state"], s["run"]["total"], s["run"]["checked"]), ("done", 3, 3))
        e = s["files"]["/data/media/" + FILM]
        self.assertEqual((e["verdict"], e["key"], e["title"], e["size"]), ("ok", "movie/4242", "A Film", os.path.getsize(os.path.join(self.w.branches[0], "media", FILM))))

    def test_second_run_checks_nothing_again(self):
        lc.run(self.w.deps())
        self.w.checked.clear()
        lc.run(self.w.deps())
        self.assertEqual(self.w.checked, [])

    def test_changed_file_and_earlier_error_are_checked_again(self):
        self.w.results["/data/media/" + OTHER] = {"verdict": "error", "reasons": [], "notes": [], "duration": None, "why": "ffprobe: killed after 600 s"}
        lc.run(self.w.deps())
        self.w.results.clear()
        self.w.put(self.w.branches[0], FILM, b"a new release, other size")
        self.w.checked.clear()
        lc.run(self.w.deps())
        self.assertEqual(sorted(self.w.checked), sorted(["/data/media/" + FILM, "/data/media/" + OTHER]))

    def test_broken_file_is_recorded_and_nothing_written_to_the_apps(self):
        self.w.results["/data/media/" + EPISODE] = {"verdict": "broken", "reasons": ["timestamp jump 280.9 s -> 118.0 s"], "notes": ["container error: [matroska,webm] File ended prematurely"], "duration": 2700.0}
        apis = {"radarr": FakeArr("radarr"), "sonarr": FakeArr("sonarr")}
        lc.run(self.w.deps(apis))
        e = self.w.load()["files"]["/data/media/" + EPISODE]
        self.assertEqual((e["verdict"], e["reasons"], e["key"]), ("broken", ["timestamp jump 280.9 s -> 118.0 s"], "tv/99"))
        self.assertEqual({m for a in apis.values() for m, _ in a.calls}, {"GET"})

    def test_ok_with_a_container_error_is_a_note(self):
        self.w.results["/data/media/" + OTHER] = {"verdict": "ok", "reasons": [], "notes": ["container error: [mov,mp4] stream 1, offset 0x1: partial file"], "duration": 10.0}
        lc.run(self.w.deps())
        self.assertEqual(self.w.load()["files"]["/data/media/" + OTHER]["verdict"], "note")

    def test_duration_far_off_the_runtime_is_a_note(self):
        self.w.results["/data/media/" + FILM] = {"verdict": "ok", "reasons": [], "notes": [], "duration": 90 * 60.0}
        lc.run(self.w.deps())
        e = self.w.load()["files"]["/data/media/" + FILM]
        self.assertEqual((e["verdict"], e["notes"]), ("note", ["duration 90 min, expected 120 min"]))

    def test_files_gone_from_the_library_leave_the_list(self):
        lc.run(self.w.deps())
        os.remove(os.path.join(self.w.branches[0], "media", FILM))
        lc.run(self.w.deps())
        s = self.w.load()
        self.assertNotIn("/data/media/" + FILM, s["files"])
        self.assertEqual(s["run"]["total"], 2)

    def test_limit_stops_early_and_says_so(self):
        lc.run(self.w.deps(), limit=1)
        s = self.w.load()
        self.assertEqual((len(self.w.checked), s["run"]["state"], s["run"]["checked"]), (1, "stopped", 1))

    def test_state_file_readable_for_the_manual_page(self):
        lc.run(self.w.deps())
        self.assertEqual(os.stat(self.w.state).st_mode & 0o777, 0o644)


class Pause(unittest.TestCase):
    def setUp(self):
        self.w = World(files=(FILM,))
        self.addCleanup(shutil.rmtree, self.w.t)

    def test_waits_while_someone_plays(self):
        self.w.playing = [[{"NowPlayingItem": {"Id": "x"}}], [{"NowPlayingItem": {"Id": "x"}}], [{"Id": "idle"}]]
        seen = []
        deps = self.w.deps()
        real_sleep = deps.sleep

        def sleep(s):
            seen.append(self.w.load()["run"]["state"])
            real_sleep(s)
        deps = deps._replace(sleep=sleep)
        lc.run(deps)
        self.assertEqual(self.w.sleeps, [lc.POLL, lc.POLL])
        self.assertEqual(seen, ["paused-playback", "paused-playback"])
        self.assertEqual(self.w.load()["run"]["state"], "done")

    def test_unreadable_sessions_count_as_playing(self):
        self.w.playing = [None, []]
        lc.run(self.w.deps())
        self.assertEqual(self.w.sleeps, [lc.POLL])

    def test_playing_says_who_is_playing(self):
        self.assertTrue(lc.playing([{"NowPlayingItem": {}}, {}]))
        self.assertFalse(lc.playing([{"Id": "a"}]))
        self.assertTrue(lc.playing(None))

    def test_night_window_pauses_until_it_is_over(self):
        self.w.now = local(3, 52)
        lc.run(self.w.deps())
        self.assertGreaterEqual(self.w.now, local(4, 50))
        self.assertEqual(len(self.w.checked), 1)
        self.assertTrue(lc.in_night(local(3, 50)) and lc.in_night(local(4, 49)))
        self.assertFalse(lc.in_night(local(3, 49)) or lc.in_night(local(4, 50)) or lc.in_night(local(21, 0)))


class Cli(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(shutil.rmtree, self.w.t)

    def test_help(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(lc.main(["--help"]), 0)
        self.assertIn("Usage:", out.getvalue())

    def test_unknown_command(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(lc.main(["frobnicate"]), 2)

    def test_status_counts_and_next_command(self):
        self.w.results["/data/media/" + EPISODE] = {"verdict": "broken", "reasons": ["no audio stream"], "notes": [], "duration": None}
        lc.run(self.w.deps())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(lc.status(self.w.state), 0)
        text = out.getvalue()
        self.assertIn("3 of 3 checked", text)
        self.assertIn("1 broken", text)
        self.assertIn("A Show", text)
        self.assertIn("next:", text)

    def test_status_before_the_first_run(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(lc.status(self.w.state), 0)
        self.assertIn("not started", out.getvalue())


@unittest.skipUnless(os.environ.get("LC_REAL") == "1", "real samples: LC_REAL=1 as root (docker)")
class Real(unittest.TestCase):
    """The real check through the throw-away Jellyfin-image container, on samples built from ffmpeg's test pattern (no library files)."""

    @classmethod
    def setUpClass(cls):
        cls.t = tempfile.mkdtemp(dir=os.environ.get("LC_REAL_DIR"))
        cls.addClassCleanup(shutil.rmtree, cls.t, True)
        cls.image = lc.jellyfin_image()
        ff = ["docker", "run", "--rm", "--network", "none", "--user", lc.PROBE_USER, "-v", f"{cls.t}:/w", "--entrypoint", "/usr/lib/jellyfin-ffmpeg/ffmpeg", cls.image,
              "-hide_banner", "-loglevel", "error", "-y"]
        src = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
        enc = ["-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-c:a", "aac"]
        os.makedirs(os.path.join(cls.t, "media", "movies", "S"))
        subprocess.run(["chmod", "-R", "a+rwX", cls.t], check=True)
        for cmd in ([*src, "-t", "6", *enc, "/w/good.mkv"], [*src, "-t", "6", *enc, "/w/a.ts"], [*src, "-t", "3", *enc, "/w/b.ts"],
                    [*src, "-t", "6", "-an", *enc[:6], "/w/media/movies/S/silent.mkv"]):
            subprocess.run(ff + cmd, check=True)
        # two recordings back to back: the second starts again at 0 s, the 2026-11-17 case (timestamps 280.9 s -> 118.0 s)
        with open(os.path.join(cls.t, "media", "movies", "S", "jump.ts"), "wb") as out:
            for n in ("a.ts", "b.ts"):
                with open(os.path.join(cls.t, n), "rb") as f:
                    out.write(f.read())
        shutil.copy(os.path.join(cls.t, "good.mkv"), os.path.join(cls.t, "media", "movies", "S", "good.mkv"))
        with open(os.path.join(cls.t, "good.mkv"), "rb") as f:
            data = f.read()
        with open(os.path.join(cls.t, "media", "movies", "S", "tail.mkv"), "wb") as f:
            f.write(data[: len(data) * 2 // 3])
        subprocess.run(["chmod", "-R", "a+rX", cls.t], check=True)
        cls.probe = lc.Prober(os.path.join(cls.t, "media"), name="library-check-test")
        cls.probe.start()

    @classmethod
    def tearDownClass(cls):
        cls.probe.stop()

    def verdict(self, name):
        return self.probe.check(f"/data/media/movies/S/{name}")

    def test_good(self):
        self.assertEqual(self.verdict("good.mkv")["verdict"], "ok")

    def test_segments_out_of_order_are_broken(self):
        r = self.verdict("jump.ts")
        self.assertEqual(r["verdict"], "broken")
        self.assertRegex(r["reasons"][0], r"^timestamp jump")

    def test_no_audio_is_broken(self):
        self.assertEqual(self.verdict("silent.mkv")["reasons"], ["no audio stream"])

    def test_truncated_tail_is_no_block(self):
        self.assertIn(self.verdict("tail.mkv")["verdict"], ("ok", "note"))

    def test_container_has_no_network_and_reads_the_library_only(self):
        out = subprocess.run(["docker", "inspect", self.probe.name, "--format",
                              '{{.HostConfig.NetworkMode}} {{.HostConfig.ReadonlyRootfs}} {{range .Mounts}}{{if eq .Destination "/data/media"}}{{.RW}}{{end}}{{end}} {{.Config.User}}'],
                             capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(out, ["none", "true", "false", lc.PROBE_USER])


if __name__ == "__main__":
    unittest.main()
