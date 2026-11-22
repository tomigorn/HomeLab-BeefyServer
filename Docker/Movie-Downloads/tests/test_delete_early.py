#!/usr/bin/env python3
"""scripts/delete-early.py: the "Delete early" runner (README "Delete early") against fake Radarr, Sonarr, Jellyfin, Seerr and a fake pool: direct deletes into the recycle bin, Keep forever and mid-watch approvals, answers, the 7-day expiry, the weekly quota, the test film, seasons, restore, notices and the audit log.
Usage: python3 tests/test_delete_early.py"""
import contextlib
import datetime
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("delete_early", HERE.parent / "scripts" / "delete-early.py")
de = importlib.util.module_from_spec(spec)
spec.loader.exec_module(de)

NOW = datetime.datetime(2026, 12, 20, 12, 0, tzinfo=datetime.timezone.utc)
JF_JANE, JF_JOE, JF_ANN = "a" * 32, "b" * 32, "c" * 32
FILM = "/data/media/movies/New Film (2025)"
SHOW = "/data/media/tv/New Show (2025)"


class Arr:
    """Radarr or Sonarr: a file DELETE moves the file into the bin like the apps do."""

    def __init__(self, world, app):
        self.w, self.app, self.calls = world, app, []
        self.tags = [{"id": 1, "label": "keep-test"}]

    def __call__(self, path, method="GET", body=None):
        self.calls.append((method, path, body))
        w = self.w
        if path == "/tag":
            if method == "POST":
                self.tags.append({"id": len(self.tags) + 1, "label": body["label"]})
                return self.tags[-1]
            return self.tags
        if path == "/command":
            return {"id": 1}
        if self.app == "radarr":
            if path.startswith("/movie?tmdbId="):
                tmdb = int(path.split("=")[1])
                return [m for m in w.movies if m["tmdbId"] == tmdb]
            if path.startswith("/movie/"):
                m = next(m for m in w.movies if m["id"] == int(path.split("/")[2]))
                if method == "PUT":
                    m.update(body)
                return m
            if path.startswith("/moviefile/") and method == "DELETE":
                m = next(m for m in w.movies if (m.get("movieFile") or {}).get("id") == int(path.split("/")[2]))
                w.to_bin(m["movieFile"]["path"])
                m["movieFile"], m["hasFile"] = None, False
                return None
        else:
            if path.startswith("/series?tvdbId="):
                return [s for s in w.series if s["tvdbId"] == int(path.split("=")[1])]
            if path.startswith("/series/"):
                s = next(s for s in w.series if s["id"] == int(path.split("/")[2]))
                if method == "PUT":
                    s.update(body)
                return s
            if path.startswith("/episodefile?seriesId="):
                return list(w.epfiles)
            if path.startswith("/episode?seriesId="):
                season = int(path.split("seasonNumber=")[1])
                return [e for e in w.episodes if e["seasonNumber"] == season]
            if path == "/episode/monitor" and method == "PUT":
                for e in w.episodes:
                    if e["id"] in body["episodeIds"]:
                        e["monitored"] = body["monitored"]
                return None
            if path == "/episodefile/bulk" and method == "DELETE":
                for f in [f for f in w.epfiles if f["id"] in body["episodeFileIds"]]:
                    w.to_bin(f["path"])
                    w.epfiles.remove(f)
                return None
        raise AssertionError(f"unexpected {self.app} {method} {path}")


class Jf:
    def __init__(self, world):
        self.w, self.calls = world, []

    def __call__(self, path, method="GET", body=None):
        self.calls.append((method, path, body))
        w = self.w
        if path == "/Users":
            return [{"Id": u} for u in (JF_JANE, JF_JOE, JF_ANN)]
        if path.startswith("/Items?") and "IncludeItemTypes=Movie" in path:
            return {"Items": [{"Id": "m1", "Path": "/media/movies/New Film (2025)/New Film (2025).mkv", "MediaSources": [{"Id": "m1"}, {"Id": "m2"}]}]}
        if path.startswith("/Items?") and "IncludeItemTypes=Series" in path:
            return {"Items": [{"Id": "s1", "Path": "/media/tv/New Show (2025)"}]}
        if path.startswith("/Users/") and "/Items/" in path:
            uid, item = path.split("/")[2], path.split("/")[4].split("?")[0]
            return {"Id": item, "UserData": w.userdata.get((uid, item), {})}
        if path.startswith("/Users/") and "ParentId=s1" in path:
            uid = path.split("/")[2]
            return {"Items": [{"Id": e, "ParentIndexNumber": s, "UserData": w.userdata.get((uid, e), {})} for e, s in (("e1", 1), ("e2", 1), ("e3", 2))]}
        if path.startswith("/Sessions") and method == "GET":
            return [{"Id": "sess-jane", "UserId": JF_JANE}]
        if path.startswith("/DisplayPreferences/"):
            return {"CustomPrefs": {"language": "de"}}
        if path.startswith("/Sessions/") and method == "POST":
            return None
        if path == "/Library/Media/Updated":
            return None
        raise AssertionError(f"unexpected jellyfin {method} {path}")


SEERR_CALLS = []


def seerr(path, method="GET", body=None):
    SEERR_CALLS.append((method, path))
    assert method == "GET" or body is not None, "Seerr answers 415 to a POST without a JSON body"
    if path in ("/movie/10", "/tv/20"):
        return {"mediaInfo": {"id": 77, "status": 5}}
    if path.startswith("/media/77/") and method == "POST":
        return {}
    if path.startswith("/user?"):
        return {"results": [{"id": 1, "displayName": "Owner", "jellyfinUserId": None}, {"id": 7, "displayName": "Jane Doe", "jellyfinUserId": JF_JANE},
                            {"id": 5, "displayName": "Joe Bloggs", "jellyfinUserId": None}, {"id": 12, "displayName": "Ann Example", "jellyfinUserId": None}]}
    raise AssertionError(path)


class World:
    def __init__(self, d):
        self.d = pathlib.Path(d)
        self.pool = self.d / "pool"
        self.state_dir = self.d / "state"
        self.jobs = self.d / "jobs"
        for p in (self.pool / "media/movies", self.pool / ".recyclebin", self.state_dir, self.jobs):
            p.mkdir(parents=True)
        self.movies = [{"id": 1, "tmdbId": 10, "title": "New Film", "year": 2025, "path": FILM, "hasFile": True, "monitored": True, "tags": [],
                        "movieFile": {"id": 101, "path": FILM + "/New Film (2025).mkv"}},
                       {"id": 2, "tmdbId": 653, "title": "Nosferatu", "year": 1922, "path": "/data/media/movies/Nosferatu (1922)", "hasFile": True, "monitored": True, "tags": [1],
                        "movieFile": {"id": 102, "path": "/data/media/movies/Nosferatu (1922)/Nosferatu (1922).mkv"}},
                       {"id": 3, "tmdbId": 11, "title": "Tagged", "year": 2020, "path": "/data/media/movies/Tagged (2020)", "hasFile": True, "monitored": True, "tags": [1],
                        "movieFile": {"id": 103, "path": "/data/media/movies/Tagged (2020)/Tagged (2020).mkv"}}]
        self.series = [{"id": 4, "tvdbId": 4020, "title": "New Show", "year": 2025, "path": SHOW, "monitored": True, "tags": [],
                        "seasons": [{"seasonNumber": 1, "monitored": True}, {"seasonNumber": 2, "monitored": True}]}]
        self.epfiles = [{"id": 201, "seasonNumber": 1, "path": SHOW + "/Season 01/New Show S01E01.mkv"}, {"id": 202, "seasonNumber": 1, "path": SHOW + "/Season 01/New Show S01E02.mkv"},
                        {"id": 203, "seasonNumber": 2, "path": SHOW + "/Season 02/New Show S02E01.mkv"}]
        self.episodes = [{"id": 301, "seasonNumber": 1, "monitored": True}, {"id": 302, "seasonNumber": 1, "monitored": True}, {"id": 303, "seasonNumber": 2, "monitored": True}]
        for f in [m["movieFile"]["path"] for m in self.movies] + [f["path"] for f in self.epfiles] + [FILM + "/New Film (2025) - Phone 1080p.mkv", FILM + "/New Film (2025).de.srt"]:
            self.write(f)
        self.userdata = {}
        self.notices = []
        self.retention = self.d / "retention.json"
        self.retention.write_text(json.dumps({"titles": {"movie/10": {"t": "New Film", "default": False, "protected": False},
                                                         "movie/12": {"t": "Kept", "default": True, "protected": False}}}))
        self.keep = self.d / "keep.json"
        self.identity = self.d / "identity.json"
        self.identity.write_text(json.dumps({"jellyfin": {JF_JOE: "sub-joe"}, "seerr": {"5": "sub-joe"}}))
        self.radarr, self.sonarr, self.jf = Arr(self, "radarr"), Arr(self, "sonarr"), Jf(self)
        self.locked = True
        self.now = NOW

        @contextlib.contextmanager
        def lock():
            yield self.locked

        self.env = de.Env(radarr=self.radarr, sonarr=self.sonarr, jf=self.jf, seerr=seerr, jobs=self.jobs, state=self.state_dir / "delete-early.json",
                          audit=self.state_dir / "delete-early-audit.jsonl", gone=self.state_dir / "retention-gone.json", retention=self.retention, keep=self.keep,
                          identity=self.identity, pool=str(self.pool), now=lambda: self.now, notify=lambda sid, event, info: self.notices.append((sid, event, info)), lock=lock)

    def host(self, p):
        return self.pool / p[len("/data/"):]

    def write(self, p):
        h = self.host(p) if p.startswith("/data/") else pathlib.Path(p)
        h.parent.mkdir(parents=True, exist_ok=True)
        h.write_text("video " + h.name)

    def to_bin(self, p):
        video = self.host(p)
        for extra in sorted(video.parent.glob(video.stem + ".*")) if getattr(self, "move_extras", True) else []:
            if extra != video and not extra.name.endswith(".mkv"):
                self.move_to_bin(extra)
        self.move_to_bin(video)

    def move_to_bin(self, src):
        rel = src.relative_to(self.pool / "media")
        dest = self.pool / ".recyclebin" / pathlib.Path(*rel.parts[1:])
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dest)

    def job(self, op, **kw):
        body = {"op": op, "at": self.now.isoformat(), **kw}
        name = f"{op}-{kw.get('id')}-{kw.get('user')}"
        (self.jobs / f"{name}.req").write_text(json.dumps(body))

    def delete(self, jid="j1", user=7, kind="movie", tmdb=10, season=None, requesters=(7,), keep=False, tvdb=None, title="New Film"):
        self.job("delete", id=jid, user=user, userName={7: "Jane Doe", 5: "Joe Bloggs", 12: "Ann Example"}.get(user, f"user {user}"), account="x",
                 kind=kind, tmdb=tmdb, tvdb=tvdb, season=season, title=title, year=2025, requesters=list(requesters), keep=keep)

    def state(self):
        return json.loads(self.env.state.read_text())["items"]

    def audit(self):
        return [json.loads(line)["event"] for line in self.env.audit.read_text().splitlines()]

    def binned(self):
        return sorted(str(p.relative_to(self.pool / ".recyclebin")) for p in (self.pool / ".recyclebin").rglob("*") if p.is_file())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = World(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self):
        return de.run(self.w.env)


class Direct(Base):
    def test_requester_deletes_a_temporary_film_into_the_bin(self):
        self.w.delete()
        self.run_once()
        it = self.w.state()["j1"]
        self.assertEqual((it["state"], it["binUntil"], it["by"]), ("deleted", "2026-12-27T12:00:00+00:00", 7))
        self.assertEqual(self.w.binned(), ["New Film (2025)/New Film (2025) - Phone 1080p.mkv", "New Film (2025)/New Film (2025).de.srt", "New Film (2025)/New Film (2025).mkv"])
        m = self.w.movies[0]
        self.assertFalse(m["monitored"])
        self.assertEqual([t["label"] for t in self.w.radarr.tags if t["id"] in m["tags"]], ["retention-deleted"])
        gone = json.loads(self.w.env.gone.read_text())["gone"]
        self.assertEqual([g["key"] for g in gone], ["movie/10"])
        self.assertEqual(list(self.w.jobs.glob("*.req")), [])
        self.assertEqual(self.w.audit(), ["requested", "deleted"])
        self.assertEqual(self.w.notices, [])
        self.assertIn(("POST", "/media/77/unknown"), SEERR_CALLS, "Seerr shows it as deleted at once: requestable again, the row stays")
        self.assertFalse([c for c in SEERR_CALLS if c[0] == "DELETE"])

    def test_someone_elses_title_tells_the_requester_with_undo(self):
        self.w.delete(user=5, requesters=(7,))
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "deleted")
        ((sid, event, info),) = self.w.notices
        self.assertEqual((sid, event, info["who"], info["link"], info["until"]), (7, "deleted", "Joe Bloggs", "https://requests.holy-grail.ch/movie/10", "2026-12-27T12:00:00+00:00"))

    def test_test_film_and_keep_test_tag_never(self):
        self.w.delete(tmdb=653)
        self.w.delete(jid="j2", tmdb=11, title="Tagged")
        self.run_once()
        st = self.w.state()
        self.assertEqual([(st[j]["state"], st[j]["why"]) for j in ("j1", "j2")], [("refused", "protected")] * 2)
        self.assertEqual(self.w.binned(), [])
        self.assertFalse([c for c in self.w.radarr.calls if c[0] in ("PUT", "DELETE")])

    def test_not_in_radarr_or_no_file(self):
        self.w.movies[0]["hasFile"] = False
        self.w.delete()
        self.run_once()
        self.assertEqual((self.w.state()["j1"]["state"], self.w.state()["j1"]["why"]), ("refused", "library"))

    def test_busy_media_lock_waits_for_the_next_run(self):
        self.w.locked = False
        self.w.delete()
        self.run_once()
        self.assertEqual((self.w.state()["j1"]["state"], self.w.binned()), ("approved", []))
        self.w.locked = True
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "deleted")


class Failures(Base):
    def test_jellyfin_down_fails_the_request_and_drops_the_job(self):
        def down(path, method="GET", body=None):
            raise OSError("connection refused")
        self.w.env.jf = down
        self.w.delete()
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "failed")
        self.assertEqual((list(self.w.jobs.glob("*.req")), self.w.binned()), ([], []))


class SideFiles(Base):
    def test_a_subtitle_the_arr_left_behind_goes_to_the_bin_too(self):
        self.w.move_extras = False
        self.w.delete()
        self.run_once()
        self.assertIn("New Film (2025)/New Film (2025).de.srt", self.w.binned())
        self.assertFalse((self.w.pool / "media/movies/New Film (2025)/New Film (2025).de.srt").exists())


class Seasons(Base):
    def test_one_season_only(self):
        SEERR_CALLS.clear()
        self.w.delete(kind="tv", tmdb=20, tvdb=4020, season=1, title="New Show")
        self.run_once()
        self.assertFalse([c for c in SEERR_CALLS if c[0] == "POST"], "a season leaves the title's status alone")
        self.assertEqual(self.w.state()["j1"]["state"], "deleted")
        self.assertEqual(self.w.binned(), ["New Show (2025)/Season 01/New Show S01E01.mkv", "New Show (2025)/Season 01/New Show S01E02.mkv"])
        s = self.w.series[0]
        self.assertEqual((s["monitored"], s["tags"], [x["monitored"] for x in s["seasons"]]), (True, [], [False, True]))
        self.assertEqual([e["monitored"] for e in self.w.episodes], [False, False, True])
        self.assertFalse(self.w.env.gone.exists())

    def test_whole_series(self):
        self.w.delete(kind="tv", tmdb=20, tvdb=4020, title="New Show")
        self.run_once()
        self.assertEqual(len(self.w.binned()), 3)
        self.assertFalse(self.w.series[0]["monitored"])
        self.assertEqual([g["key"] for g in json.loads(self.w.env.gone.read_text())["gone"]], ["tv/20"])


class Approval(Base):
    def test_keep_forever_needs_the_requester(self):
        self.w.delete(user=5, requesters=(7,), keep=True)
        self.run_once()
        it = self.w.state()["j1"]
        self.assertEqual((it["state"], it["expires"]), ("pending", "2026-12-27T12:00:00+00:00"))
        self.assertEqual([(a["reason"], a["approvers"], a["names"]) for a in it["asks"]], [("keep", [7], ["Jane Doe"])])
        self.assertEqual(self.w.binned(), [])
        self.assertEqual([(n[0], n[1]) for n in self.w.notices], [(7, "ask")])

    def test_keep_forever_from_the_store_too(self):
        self.w.delete(user=5, tmdb=10, requesters=(7,), keep=False)
        self.w.keep.write_text(json.dumps({"titles": {"movie/10": {"keep": True}}}))
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "pending")

    def test_keep_forever_without_a_requester_asks_the_owner(self):
        self.w.delete(user=5, requesters=(), keep=True)
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["asks"][0]["approvers"], [1])

    def test_own_keep_forever_title_goes_at_once(self):
        self.w.delete(user=7, requesters=(7,), keep=True)
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "deleted")

    def test_someone_else_mid_watch_must_ok(self):
        self.w.userdata[(JF_JOE, "m2")] = {"PlaybackPositionTicks": 5, "Played": False, "LastPlayedDate": "2026-12-10T20:00:00Z"}
        self.w.delete(user=7)
        self.run_once()
        it = self.w.state()["j1"]
        self.assertEqual([(a["reason"], a["approvers"]) for a in it["asks"]], [("watching", [5])])
        self.assertEqual(self.w.binned(), [])

    def test_viewer_without_a_seerr_account_asks_the_owner(self):
        self.w.userdata[(JF_ANN, "m1")] = {"PlaybackPositionTicks": 5, "Played": False, "LastPlayedDate": "2026-12-10T20:00:00Z"}
        self.w.delete(user=7)
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["asks"][0]["approvers"], [1])

    def test_own_finished_or_old_watching_does_not_count(self):
        self.w.userdata[(JF_JANE, "m1")] = {"PlaybackPositionTicks": 5, "Played": False, "LastPlayedDate": "2026-12-10T20:00:00Z"}
        self.w.userdata[(JF_JOE, "m1")] = {"PlaybackPositionTicks": 0, "Played": True, "LastPlayedDate": "2026-12-10T20:00:00Z"}
        self.w.userdata[(JF_ANN, "m1")] = {"PlaybackPositionTicks": 5, "Played": False, "LastPlayedDate": "2026-11-01T20:00:00Z"}
        self.w.delete(user=7)
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "deleted")

    def test_series_mid_watch_started_not_finished(self):
        self.w.userdata[(JF_JOE, "e1")] = {"Played": True, "LastPlayedDate": "2026-12-15T20:00:00Z"}
        self.w.delete(kind="tv", tmdb=20, tvdb=4020, title="New Show")
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["asks"][0]["approvers"], [5])

    def test_season_finished_by_the_viewer_is_free(self):
        for e in ("e1", "e2"):
            self.w.userdata[(JF_JOE, e)] = {"Played": True, "LastPlayedDate": "2026-12-15T20:00:00Z"}
        self.w.delete(kind="tv", tmdb=20, tvdb=4020, season=1, title="New Show")
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "deleted")

    def pending(self):
        self.w.delete(user=5, requesters=(7,), keep=True)
        self.run_once()
        self.w.notices.clear()

    def test_approve_deletes(self):
        self.pending()
        self.w.job("answer", id="j1", user=7, userName="Jane Doe", approve=True)
        self.run_once()
        it = self.w.state()["j1"]
        self.assertEqual((it["state"], it["asks"][0]["answer"]), ("deleted", True))
        self.assertEqual(len(self.w.binned()), 3)
        self.assertIn((5, "approved"), [(n[0], n[1]) for n in self.w.notices])
        self.assertEqual(self.w.audit(), ["requested", "pending", "approved", "deleted"])

    def test_deny_keeps_it(self):
        self.pending()
        self.w.job("answer", id="j1", user=7, userName="Jane Doe", approve=False)
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "denied")
        self.assertEqual(self.w.binned(), [])
        self.assertEqual([(n[0], n[1]) for n in self.w.notices], [(5, "denied")])
        self.assertEqual(self.w.audit()[-1], "denied")

    def test_answer_by_someone_else_is_ignored(self):
        self.pending()
        self.w.job("answer", id="j1", user=5, userName="Joe Bloggs", approve=True)
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "pending")
        self.assertEqual(list(self.w.jobs.glob("*.req")), [])

    def test_no_answer_in_7_days_is_denied(self):
        self.pending()
        self.w.now = NOW + datetime.timedelta(days=7, minutes=1)
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "expired")
        self.assertEqual([(n[0], n[1]) for n in self.w.notices], [(5, "expired")])
        self.w.job("answer", id="j1", user=7, userName="Jane Doe", approve=True)
        self.run_once()
        self.assertEqual(self.w.binned(), [])


class Quota(Base):
    def test_ten_per_week(self):
        items = {f"q{i}": {"id": f"q{i}", "by": 7, "state": "deleted", "at": (NOW - datetime.timedelta(days=3)).isoformat(), "kind": "movie", "tmdb": 900 + i} for i in range(10)}
        self.w.env.state.write_text(json.dumps({"items": items}))
        self.w.delete()
        self.run_once()
        self.assertEqual((self.w.state()["j1"]["state"], self.w.state()["j1"]["why"]), ("refused", "quota"))
        self.assertEqual(self.w.binned(), [])


class Restore(Base):
    def deleted(self):
        self.w.delete(user=5)
        self.run_once()
        self.w.notices.clear()

    def test_files_go_back_and_the_title_is_watched_again(self):
        self.deleted()
        self.w.job("restore", id="j1", user=7, userName="Jane Doe", manager=False)
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "restored")
        self.assertEqual(self.w.binned(), [])
        self.assertTrue((self.w.pool / "media/movies/New Film (2025)/New Film (2025).mkv").is_file())
        self.assertTrue((self.w.pool / "media/movies/New Film (2025)/New Film (2025) - Phone 1080p.mkv").is_file())
        self.assertTrue((self.w.pool / "media/movies/New Film (2025)/New Film (2025).de.srt").is_file(), "subtitles come back too")
        m = self.w.movies[0]
        self.assertTrue(m["monitored"])
        self.assertEqual(m["tags"], [])
        self.assertIn(("POST", "/command", {"name": "RescanMovie", "movieId": 1}), self.w.radarr.calls)
        self.assertEqual(json.loads(self.w.env.gone.read_text())["gone"], [])
        self.assertEqual(self.w.audit()[-1], "restored")
        self.assertEqual([c for c in SEERR_CALLS if c[0] == "POST"][-1], ("POST", "/media/77/available"))

    def test_season_restore(self):
        self.w.delete(kind="tv", tmdb=20, tvdb=4020, season=1, title="New Show")
        self.run_once()
        self.w.job("restore", id="j1", user=7, userName="Jane Doe", manager=False)
        self.run_once()
        self.assertEqual(self.w.binned(), [])
        self.assertEqual([x["monitored"] for x in self.w.series[0]["seasons"]], [True, True])
        self.assertEqual([e["monitored"] for e in self.w.episodes], [True, True, True])

    def test_a_stranger_cannot(self):
        self.deleted()
        self.w.job("restore", id="j1", user=12, userName="Ann Example", manager=False)
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "deleted")

    def test_not_after_the_bin_date(self):
        self.deleted()
        self.w.now = NOW + datetime.timedelta(days=8)
        self.w.job("restore", id="j1", user=5, userName="Joe Bloggs", manager=False)
        self.run_once()
        self.assertEqual(self.w.state()["j1"]["state"], "deleted")
        self.assertEqual(len(self.w.binned()), 3)

    def test_an_existing_file_is_never_overwritten(self):
        self.deleted()
        self.w.write(FILM + "/New Film (2025).mkv")
        self.w.job("restore", id="j1", user=5, userName="Joe Bloggs", manager=False)
        self.run_once()
        self.assertEqual((self.w.pool / "media/movies/New Film (2025)/New Film (2025).mkv").read_text(), "video New Film (2025).mkv")
        self.assertIn("New Film (2025)/New Film (2025).mkv", self.w.binned())


class Notices(unittest.TestCase):
    def test_pop_up_on_live_sessions_and_telegram_where_known(self):
        calls, sent = [], []

        def jf(path, method="GET", body=None):
            calls.append((method, path, body))
            if path.startswith("/DisplayPreferences/"):
                return {"CustomPrefs": {"language": "de"}}
            if path.startswith("/Sessions?"):
                return [{"Id": "s1", "UserId": JF_JANE}, {"Id": "s2", "UserId": JF_JOE}]
            return None

        n = de.Notifier(jf, {JF_JANE: 7, JF_JOE: 5}, {7: "chat-7"}, lambda chat, text: sent.append((chat, text)))
        n(7, "deleted", {"who": "Joe Bloggs", "title": "New Film", "scope": None, "link": "https://requests.holy-grail.ch/movie/10", "until": "2026-12-27T12:00:00+00:00"})
        (popup,) = [c for c in calls if c[0] == "POST"]
        self.assertEqual(popup[1], "/Sessions/s1/Message")
        self.assertIn("gelöscht", popup[2]["Text"])
        self.assertEqual(sent[0][0], "chat-7")
        self.assertIn("deleted New Film", sent[0][1])
        calls.clear()
        n(12, "ask", {"who": "Joe Bloggs", "title": "New Film", "reason": "keep", "link": "x", "until": None})
        self.assertEqual([c for c in calls if c[0] == "POST"], [])

    def test_a_failing_channel_does_not_stop_the_run(self):
        def jf(path, method="GET", body=None):
            raise OSError("down")

        def tg(chat, text):
            raise OSError("down")
        de.Notifier(jf, {JF_JANE: 7}, {7: "c"}, tg)(7, "expired", {"title": "X"})


class Messages(unittest.TestCase):
    def test_texts_in_both_languages(self):
        info = {"who": "Joe Bloggs", "title": "New Film", "scope": None, "link": "https://requests.holy-grail.ch/movie/10", "until": "2026-12-27T12:00:00+00:00", "reason": "keep"}
        for event in ("ask", "deleted", "denied", "expired", "approved"):
            for lang in ("en", "de"):
                head, text = de.message(event, info, lang)
                self.assertTrue(head and text, (event, lang))
        self.assertIn("https://requests.holy-grail.ch/movie/10", de.message("deleted", info, "en")[1])
        self.assertIn("27", de.message("deleted", info, "de")[1])
        self.assertIn("Staffel 2", de.message("ask", dict(info, scope=2), "de")[1])


if __name__ == "__main__":
    unittest.main()
