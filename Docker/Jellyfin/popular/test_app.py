import contextlib
import datetime
import io
import json
import pathlib
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app  # noqa: E402

NOW = datetime.datetime(2026, 10, 29, 1, 0, tzinfo=datetime.timezone.utc)
ORIGIN = "https://jellyfin.holy-grail.ch"
TOKEN = "a" * 32
USERS = {
    TOKEN: {"Id": "40e05f0a-9e96-4fd9-b225-eb1379dfcfa4", "Name": "jane", "Policy": {"IsDisabled": False}},
    "b" * 32: {"Id": "11111111-2222-3333-4444-555555555555", "Name": "guest", "Policy": {"IsDisabled": False}},
    "c" * 32: {"Id": "99999999-2222-3333-4444-555555555555", "Name": "gone", "Policy": {"IsDisabled": True}},
}


class FakeSeerr:
    def __init__(self):
        self.users = [{"id": 7, "jellyfinUserId": "40e05f0a9e964fd9b225eb1379dfcfa4", "permissions": 8352, "email": "x"}]
        self.media = {}
        self.posts = []

    def __call__(self, method, path, body=None, user=None):
        if method == "GET" and path.startswith("/api/v1/user"):
            return {"results": self.users}
        if method == "POST" and path == "/api/v1/user":
            self.users.append({"id": 20, "email": body["email"], "permissions": 8352, "userType": 2})
            self.posts.append(("user", body["email"]))
            return self.users[-1]
        if path.endswith("/settings/permissions"):
            self.users[-1]["permissions"] = body["permissions"]
            self.posts.append(("permissions", body["permissions"]))
            return {}
        if method == "GET" and path.startswith("/api/v1/service/"):
            if path.count("/") == 4:
                return [{"id": 0, "isDefault": True, "is4k": False}]
            return {"tags": [{"id": 3, "label": "audio-german"}, {"id": 5, "label": "download-now"}]}
        if method == "GET" and (path.startswith("/api/v1/movie/") or path.startswith("/api/v1/tv/")):
            kind, tid = path.split("?")[0].split("/")[3:5]
            return {"mediaInfo": self.media.get(f"{kind}/{tid}")}
        if method == "GET" and path.startswith("/api/v1/media"):
            return {"results": []}
        if method == "POST" and path == "/api/v1/request":
            self.posts.append(("request", body, user))
            return {"id": 900 + len(self.posts)}
        raise AssertionError((method, path))


def jellyfin(token):
    if token not in USERS:
        raise app.Unauthorized()
    return USERS[token]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self.tmp.name)
        self.allow = d / "allow.json"
        self.allow.write_text(json.dumps({"updated": "2026-10-28T04:39:00", "entries": {
            "tok-movie-xxxxxxxxxxxx": {"kind": "movie", "tmdb": 101, "season": None, "title": "Animals", "res": "1080p", "ref": "movie/101"},
            "tok-tv-xxxxxxxxxxxxxxx": {"kind": "tv", "tmdb": 202, "season": 4, "title": "The Diplomat", "res": "2160p", "ref": "tv/202/s4"},
            "tok-nores-xxxxxxxxxxxx": {"kind": "movie", "tmdb": 303, "season": None, "title": "Nope", "res": None, "ref": "movie/303"},
        }}))
        self.seerr = FakeSeerr()
        self.now = NOW
        self.core = app.Core(app.Config(allow_file=self.allow, audit_file=d / "audit.jsonl", request_state=d / "rs.json", origins=[ORIGIN]),
                             seerr=self.seerr, jellyfin=jellyfin, clock=lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, body, token=TOKEN, origin=ORIGIN, ctype="application/json"):
        headers = {"Content-Type": ctype}
        if origin:
            headers["Origin"] = origin
        if token:
            headers["X-Emby-Token"] = token
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return self.core.handle("POST", "/hss-popular/request", headers, raw)

    def audit(self):
        return [json.loads(l) for l in pathlib.Path(self.tmp.name, "audit.jsonl").read_text().splitlines()]


class Refusals(Base):
    def test_unauthenticated(self):
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=None)[0], 401)
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token="z" * 32)[0], 401)
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token='x" , Evil="1')[0], 401)

    def test_disabled_user(self):
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token="c" * 32)[0], 403)

    def test_origin_and_content_type(self):
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, origin="https://evil.example.com")[0], 403)
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, origin=None)[0], 403)
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, ctype="text/plain")[0], 415)
        self.assertEqual(self.post(b"{not json")[0], 400)
        self.assertEqual(self.post(b"x" * 5000)[0], 413)

    def test_free_form_fields_are_refused(self):
        for extra in ({"tmdb": 999}, {"season": 3}, {"title": "Anything"}, {"kind": "tv"}):
            self.assertEqual(self.post(dict({"id": "tok-tv-xxxxxxxxxxxxxxx"}, **extra))[0], 400, extra)
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx", "mode": "later"})[0], 400)
        self.assertEqual(self.post({"id": 5})[0], 400)

    def test_tampered_or_unknown_id(self):
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxy"})[0], 403)
        self.assertEqual(self.post({"id": "movie/101"})[0], 403)
        self.assertEqual(self.seerr.posts, [])

    def test_entry_without_a_found_release(self):
        self.assertEqual(self.post({"id": "tok-nores-xxxxxxxxxxxx"})[0], 403)

    def test_stale_list(self):
        self.now = NOW + datetime.timedelta(days=10)
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"})[0], 403)

    def test_already_owned_requested_or_declined(self):
        self.seerr.media["movie/101"] = {"status": 5, "requests": []}
        code, body = self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.assertEqual((code, body["state"]), (409, "owned"))
        self.seerr.media["movie/101"] = {"status": 3, "requests": [{"status": 2, "seasons": []}]}
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"})[1]["state"], "requested")
        self.seerr.media["tv/202"] = {"status": 4, "seasons": [{"seasonNumber": 4, "status": 1}], "requests": [{"status": 3, "seasons": [{"seasonNumber": 4}]}]}
        self.assertEqual(self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"})[1]["state"], "declined")
        self.assertEqual([p for p in self.seerr.posts if p[0] == "request"], [])

    def test_per_user_limit(self):
        self.core.cfg.limit = 2
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"})[0], 200)
        self.assertEqual(self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"})[0], 200)
        self.seerr.media = {}
        self.core.cfg.limit, before = 2, len(self.seerr.posts)
        self.allow.write_text(self.allow.read_text().replace("tok-nores-xxxxxxxxxxxx", "tok-third-xxxxxxxxxxxx").replace('"res": null', '"res": "1080p"'))
        code, body = self.post({"id": "tok-third-xxxxxxxxxxxx"})
        self.assertEqual((code, body["left"]), (429, 0))
        self.assertEqual(len(self.seerr.posts), before)
        self.assertEqual(self.post({"id": "tok-third-xxxxxxxxxxxx"}, token="b" * 32)[0], 200)
        self.now = NOW + datetime.timedelta(days=7, minutes=1)
        self.allow.write_text(self.allow.read_text().replace("2026-10-28T04:39:00", "2026-11-05T04:39:00"))
        self.seerr.media = {}
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"})[0], 200)

    def test_global_daily_cap(self):
        self.core.cfg.daily_max = 1
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"})[0], 200)
        self.assertEqual(self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"}, token="b" * 32)[0], 429)

    def test_rate_limit(self):
        codes = [self.post({"id": "tok-movie-xxxxxxxxxxxy"})[0] for _ in range(self.core.cfg.rate_per_min + 2)]
        self.assertEqual(codes[-1], 429)
        self.assertIn(403, codes)

    def test_lowercase_header_names(self):
        raw = json.dumps({"id": "tok-movie-xxxxxxxxxxxx"}).encode()
        code, _ = self.core.handle("POST", "/hss-popular/request", {"origin": ORIGIN, "content-type": "application/json", "x-emby-token": TOKEN}, raw)
        self.assertEqual(code, 200)
        self.assertEqual(self.core.handle("GET", "/hss-popular/state", {"authorization": f'MediaBrowser Token="{TOKEN}"'}, b"")[0], 200)

    def test_refusals_are_audited(self):
        self.post({"id": "tok-movie-xxxxxxxxxxxy"})
        a = self.audit()[-1]
        self.assertEqual((a["user"], a["entry"], a["decision"]), ("jane", "tok-movie-xxxxxxxxxxxy", "unknown entry"))


class Requests(Base):
    def test_night_request_as_the_linked_seerr_user(self):
        code, body = self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"})
        self.assertEqual(code, 200)
        self.assertEqual(body["left"], 9)
        _, sent, user = self.seerr.posts[-1]
        self.assertEqual((sent, user), ({"mediaType": "tv", "mediaId": 202, "seasons": [4]}, 7))
        a = self.audit()[-1]
        self.assertEqual((a["user"], a["title"], a["mode"], a["decision"], a["seerrUser"]), ("jane", "The Diplomat", "night", "requested", 7))

    def test_now_adds_the_download_now_tag(self):
        self.post({"id": "tok-movie-xxxxxxxxxxxx", "mode": "now"})
        self.assertEqual(self.seerr.posts[-1][1], {"mediaType": "movie", "mediaId": 101, "tags": [5]})

    def test_unlinked_user_goes_through_the_service_user(self):
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token="b" * 32)[0], 200)
        self.assertEqual(self.seerr.posts[:2], [("user", app.SERVICE_EMAIL), ("permissions", 160)])
        self.assertEqual(self.seerr.posts[-1][2], 20)
        self.assertEqual(self.audit()[-1]["user"], "guest")

    def test_service_user_lookup_without_create_never_posts(self):
        with self.assertRaises(app.NoServiceUser):
            self.core.service_user(create=False)
        self.assertEqual(self.seerr.posts, [])

    def test_service_user_must_be_local_and_have_160_without_create(self):
        for extra in ({"userType": 1}, {}, {"userType": 2, "permissions": 2}):
            self.seerr.users = [u for u in self.seerr.users if u["id"] != 20]
            self.core.cache.pop("service_user", None)
            self.seerr.users.append({"id": 20, "email": app.SERVICE_EMAIL, **{"permissions": 160, **extra}})
            with self.assertRaises(app.NoServiceUser, msg=extra):
                self.core.service_user(create=False)
        self.assertEqual(self.seerr.posts, [])

    def test_service_user_create_still_fixes_permissions_of_local_user(self):
        self.seerr.users.append({"id": 20, "email": app.SERVICE_EMAIL, "permissions": 2, "userType": 2})
        self.assertEqual(self.core.service_user()["permissions"], 160)
        self.assertEqual(self.seerr.posts, [("permissions", 160)])

    def test_service_user_cached_5_min(self):
        self.seerr.users.append({"id": 20, "email": app.SERVICE_EMAIL, "permissions": 160, "userType": 2})
        gets = []
        inner = self.core.seerr
        self.core.seerr = lambda method, path, body=None, user=None: (gets.append(path), inner(method, path, body, user))[1]
        self.assertEqual(self.core.service_user(create=False)["id"], 20)
        self.assertEqual(self.core.service_user()["id"], 20)
        self.now = NOW + datetime.timedelta(minutes=4, seconds=59)
        self.core.service_user(create=False)
        self.assertEqual(len(gets), 1)
        self.now = NOW + datetime.timedelta(minutes=5)
        self.core.service_user(create=False)
        self.assertEqual((len(gets), self.seerr.posts), (2, []))

    def test_linked_user_without_request_permission_goes_through_the_service_user(self):
        self.seerr.users[0]["permissions"] = 0
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.assertEqual(self.seerr.posts[-1][2], 20)
        self.assertEqual(self.audit()[-1]["via"], "service user: linked Seerr user may not request")

    def test_audit_says_which_seerr_user_and_why(self):
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.assertEqual(self.audit()[-1]["via"], "linked Seerr user")
        self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"}, token="b" * 32)
        self.assertEqual(self.audit()[-1]["via"], "service user: no Seerr account")

    def test_check_only_sends_nothing(self):
        code, body = self.post({"id": "tok-movie-xxxxxxxxxxxx", "check": True})
        self.assertEqual((code, body["state"]), (200, "requestable"))
        self.assertEqual([p for p in self.seerr.posts if p[0] == "request"], [])
        self.assertEqual(body["left"], 10)


class Remembered(Base):
    GUEST = {"id": "11111111222233334444555555555555", "name": "guest"}

    def entry(self, x="tok-movie-xxxxxxxxxxxx"):
        return self.core.allow()[x]

    def test_send_returns_created_and_remembers_it(self):
        log = {"user": "jane", "userId": "40e05f0a9e964fd9b225eb1379dfcfa4"}
        code, body = self.core.send({"id": "40e05f0a9e964fd9b225eb1379dfcfa4", "name": "jane"}, self.entry(), "night", False, log)
        self.assertEqual(code, 200)
        created_id = 900 + len(self.seerr.posts)
        self.assertEqual(body["request"], {"id": created_id})
        self.assertEqual((body["ok"], body["state"], body["left"]), (True, "requested", 9))
        self.assertEqual(self.core.requested_ids("40e05f0a9e964fd9b225eb1379dfcfa4"), [created_id])
        self.assertEqual(self.core.requested_ids(self.GUEST["id"]), [])

    def test_page_answer_has_no_seerr_request(self):
        self.seerr.users[0]["email"] = "jane@example.com"
        inner = self.seerr
        self.core.seerr = lambda m, path, body=None, user=None: {"id": 5, "requestedBy": {"id": 7, "email": "jane@example.com"}} if (m, path) == ("POST", "/api/v1/request") else inner(m, path, body, user)
        code, body = self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.assertEqual(code, 200)
        self.assertNotIn("request", body)
        self.assertNotIn("@", json.dumps(body))
        self.assertEqual(self.core.requested_ids("40e05f0a9e964fd9b225eb1379dfcfa4"), [5])

    def test_send_without_create_service_never_creates_user(self):
        log = {"user": "guest", "userId": self.GUEST["id"], "entry": "movie/101", "mode": "night", "via": "moonfin"}
        code, body = self.core.send(self.GUEST, self.entry(), "night", False, log, create_service=False)
        self.assertEqual((code, body), (503, {"error": "service user missing"}))
        self.assertNotIn("user", [p[0] for p in self.seerr.posts])
        self.assertEqual([p for p in self.seerr.posts if p[0] == "request"], [])
        self.assertEqual(self.audit()[-1]["decision"], "refused: no service user")
        self.assertEqual(self.core.overview(self.GUEST["id"])[0], 10)
        self.assertEqual(self.core.requested_ids(self.GUEST["id"]), [])

    def test_send_with_existing_service_user_and_no_create(self):
        self.seerr.users.append({"id": 20, "email": app.SERVICE_EMAIL, "permissions": 160, "userType": 2})
        code, body = self.core.send(self.GUEST, self.entry(), "night", False, {"user": "guest", "userId": self.GUEST["id"]}, create_service=False)
        self.assertEqual((code, self.seerr.posts[-1][2]), (200, 20))
        self.assertEqual(self.core.requested_ids(self.GUEST["id"]), [body["request"]["id"]])

    def test_user_requests_backfilled_from_audit_once(self):
        d = pathlib.Path(self.tmp.name) / "old"
        d.mkdir()
        con = sqlite3.connect(d / "state.db")
        con.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, ref TEXT, at TEXT NOT NULL, day TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0)")
        con.commit()
        con.close()
        u = "40e05f0a9e964fd9b225eb1379dfcfa4"
        line = lambda rid, decision="requested", user=u: json.dumps({"at": "2026-09-01T20:00:00+00:00", "decision": decision, "userId": user, "requestId": rid}) + "\n"
        (d / "audit.jsonl").write_text(line(11) + line(12, "refused: owned") + line(13, user="other") + line(None) + "not json\n")
        (d / "audit.jsonl.1").write_text(line(5) + line(11))
        (d / "audit.jsonl.2").write_text(line("7"))
        cfg = lambda: app.Config(allow_file=self.allow, audit_file=d / "audit.jsonl", request_state=d / "rs.json", origins=[ORIGIN])
        core = app.Core(cfg(), seerr=self.seerr, jellyfin=jellyfin, clock=lambda: self.now)
        self.assertEqual(sorted(core.requested_ids(u)), [5, 11])
        self.assertEqual(core.requested_ids("other"), [13])
        with core.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)
        (d / "audit.jsonl").write_text(line(99))
        core = app.Core(cfg(), seerr=self.seerr, jellyfin=jellyfin, clock=lambda: self.now)
        self.assertEqual(sorted(core.requested_ids(u)), [5, 11])

    def test_user_requests_never_pruned(self):
        self.core.remember("40e05f0a9e964fd9b225eb1379dfcfa4", 3)
        self.now = NOW + datetime.timedelta(days=400)
        self.allow.write_text(self.allow.read_text().replace("2026-10-28T04:39:00", (NOW + datetime.timedelta(days=399)).isoformat()))
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"})[0], 200)
        self.assertIn(3, self.core.requested_ids("40e05f0a9e964fd9b225eb1379dfcfa4"))
        self.core.remember("40e05f0a9e964fd9b225eb1379dfcfa4", 3)
        self.assertEqual(self.core.requested_ids("40e05f0a9e964fd9b225eb1379dfcfa4").count(3), 1)

    def test_allow_index_collapses_duplicates(self):
        data = json.loads(self.allow.read_text())
        data["entries"]["tok-movie-dup-xxxxxxxx"] = dict(data["entries"]["tok-movie-xxxxxxxxxxxx"])
        data["entries"]["tok-tv-dup-xxxxxxxxxxx"] = dict(data["entries"]["tok-tv-xxxxxxxxxxxxxxx"])
        self.allow.write_text(json.dumps(data))
        idx = self.core.allow_index()
        self.assertEqual(set(idx), {("movie", 101), ("tv", 202)})
        self.assertEqual((idx[("movie", 101)]["ref"], idx[("tv", 202)]["season"]), ("movie/101", 4))

    def test_no_release_from_details_sidecar(self):
        d = pathlib.Path(self.tmp.name)
        (d / "details.json").write_text(json.dumps({"updated": "2026-10-28T04:39:00", "entries": {"d" * 20: {"k": "movie", "id": 404}, "e" * 20: {"k": "tv", "id": 101}, "f" * 20: {"k": "movie", "id": 101}}}))
        self.assertTrue(self.core.no_release("movie", 404))
        self.assertTrue(self.core.no_release("tv", 101))
        self.assertTrue(self.core.no_release("movie", 303))
        self.assertFalse(self.core.no_release("movie", 101))
        self.assertFalse(self.core.no_release("movie", 999))
        self.assertFalse(self.core.no_release("tv", 404))


class Noise(Base):
    def test_unauthenticated_and_wrong_origin_hits_are_counted_not_logged(self):
        for _ in range(5):
            self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=None)
        for _ in range(3):
            self.post({"id": "tok-movie-xxxxxxxxxxxx"}, origin="https://evil.example.com")
        self.core.handle("GET", "/hss-popular/state", {}, b"")
        self.assertFalse(pathlib.Path(self.tmp.name, "audit.jsonl").exists())
        self.assertEqual(self.core.rejected(), {"unauthenticated": 6, "bad origin": 3})

    def test_counters_are_flushed_to_the_state_db_at_most_once_a_minute(self):
        self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=None)
        self.assertEqual(self.core.db_rejects(), {"unauthenticated": 1})
        self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=None)
        self.assertEqual(self.core.db_rejects(), {"unauthenticated": 1})
        self.now = NOW + datetime.timedelta(seconds=61)
        self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=None)
        self.assertEqual(self.core.db_rejects(), {"unauthenticated": 3})

    def test_audit_log_is_rotated(self):
        self.core.cfg.audit_max_bytes = 600
        for _ in range(40):
            self.post({"id": "tok-movie-xxxxxxxxxxxy"})
            self.core.hits.clear()
        d = pathlib.Path(self.tmp.name)
        self.assertLessEqual((d / "audit.jsonl").stat().st_size, 600)
        self.assertTrue((d / "audit.jsonl.1").exists())
        self.assertFalse((d / f"audit.jsonl.{app.AUDIT_KEEP + 1}").exists())

    def test_limits_come_from_the_state_db_not_the_log(self):
        self.core.cfg.limit = 1
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"})[0], 200)
        pathlib.Path(self.tmp.name, "audit.jsonl").unlink()
        self.seerr.media = {}
        self.assertEqual(self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"})[0], 429)

    def test_existing_requested_rows_are_imported_once(self):
        d = pathlib.Path(self.tmp.name)
        fresh = d / "fresh"
        fresh.mkdir()
        (fresh / "audit.jsonl").write_text(json.dumps({"at": "2026-10-28T20:00:00+00:00", "decision": "requested", "userId": "40e05f0a9e964fd9b225eb1379dfcfa4", "ref": "movie/101"}) + "\n" +
                                           json.dumps({"at": "2026-10-28T20:00:00+00:00", "decision": "unknown entry", "userId": "40e05f0a9e964fd9b225eb1379dfcfa4"}) + "\n")
        core = app.Core(app.Config(allow_file=self.allow, audit_file=fresh / "audit.jsonl", request_state=d / "rs.json", origins=[ORIGIN]), seerr=self.seerr, jellyfin=jellyfin, clock=lambda: self.now)
        self.assertEqual(core.handle("GET", "/hss-popular/state", {"X-Emby-Token": TOKEN}, b"")[1]["left"], 9)

    def test_caches_are_pruned(self):
        for i in range(5):
            self.core.handle("GET", "/hss-popular/state", {"X-Emby-Token": TOKEN}, b"")
            self.post({"id": "tok-movie-xxxxxxxxxxxy"}, token="b" * 32)
        self.now = NOW + datetime.timedelta(minutes=5)
        self.core.handle("GET", "/hss-popular/state", {"X-Emby-Token": "b" * 32}, b"")
        self.post({"id": "tok-movie-xxxxxxxxxxxy"}, token="b" * 32)
        self.assertEqual(len(self.core.auth_cache), 1)
        self.assertEqual(sum(len(v) for v in self.core.hits.values()), 1)


class Days(Base):
    def test_daily_cap_counts_days_in_zurich(self):
        self.core.cfg.daily_max = 1
        self.allow.write_text(self.allow.read_text().replace("2026-10-28T04:39:00", "2026-10-28T04:39:00+01:00"))
        self.now = datetime.datetime(2026, 10, 28, 22, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"})[0], 200)
        self.now = datetime.datetime(2026, 10, 28, 23, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"}, token="b" * 32)[0], 200)

    def test_state_says_when_the_daily_cap_is_used_up(self):
        self.core.cfg.daily_max = 1
        self.assertFalse(self.core.handle("GET", "/hss-popular/state", {"X-Emby-Token": TOKEN}, b"")[1]["dailyFull"])
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.assertTrue(self.core.handle("GET", "/hss-popular/state", {"X-Emby-Token": "b" * 32}, b"")[1]["dailyFull"])


class Concurrency(Base):
    def slow_seerr(self, uid):
        gate, inside = threading.Event(), threading.Event()
        real = self.seerr

        def seerr(method, path, body=None, user=None):
            if method == "POST" and path == "/api/v1/request" and user == uid:
                inside.set()
                gate.wait(5)
            return real(method, path, body, user)
        self.core.seerr = seerr
        return gate, inside

    def test_a_slow_seerr_call_blocks_only_that_user(self):
        gate, inside = self.slow_seerr(7)
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("a", self.post({"id": "tok-movie-xxxxxxxxxxxx"})))
        t.start()
        self.assertTrue(inside.wait(2))
        started = time.monotonic()
        self.assertEqual(self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"}, token="b" * 32)[0], 200)
        self.assertLess(time.monotonic() - started, 1)
        gate.set()
        t.join(5)
        self.assertEqual(out["a"][0], 200)

    def test_the_daily_cap_holds_while_a_request_is_in_flight(self):
        self.core.cfg.daily_max = 1
        gate, inside = self.slow_seerr(7)
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("a", self.post({"id": "tok-movie-xxxxxxxxxxxx"})))
        t.start()
        self.assertTrue(inside.wait(2))
        self.assertEqual(self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"}, token="b" * 32)[0], 429)
        gate.set()
        t.join(5)
        self.assertEqual(out["a"][0], 200)

    def test_a_failed_seerr_call_gives_the_slot_back(self):
        self.core.cfg.daily_max = 1
        real = self.seerr

        def seerr(method, path, body=None, user=None):
            if method == "POST" and path == "/api/v1/request" and user == 7:
                raise app.urllib.error.HTTPError(path, 500, "boom", None, None)
            return real(method, path, body, user)
        self.core.seerr = seerr
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"})[0], 502)
        self.assertEqual(self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"}, token="b" * 32)[0], 200)



POP_D = "dPopularxxxxxxxxxxxxxxxx"
COMING_D = "dComingxxxxxxxxxxxxxxxxx"


class Details(Base):
    def setUp(self):
        super().setUp()
        d = pathlib.Path(self.tmp.name)
        (d / "details.json").write_text(json.dumps({"updated": "2026-10-28T04:39:00", "entries": {POP_D: {"k": "movie", "id": 101, "t": {"en": "Animals"}, "ref": "movie/101"}}}))
        (d / "coming.json").write_text(json.dumps({"updated": "2026-10-28T04:38:00", "entries": {COMING_D: {"k": "tv", "id": 77, "t": {"en": "Soon"}, "ref": "tv/77"}}}))

    def get(self, query, token=TOKEN):
        return self.core.handle("GET", "/hss-popular/details" + query, {"X-Emby-Token": token} if token else {}, b"")

    def test_popular_and_coming_soon_ids_answer_with_their_details(self):
        self.assertEqual(self.get("?id=" + POP_D), (200, {"details": {"k": "movie", "id": 101, "t": {"en": "Animals"}}}))
        self.assertEqual(self.get("?id=" + COMING_D)[1]["details"]["t"], {"en": "Soon"})

    def test_needs_a_signed_in_enabled_user(self):
        self.assertEqual(self.get("?id=" + POP_D, token=None)[0], 401)
        self.assertEqual(self.get("?id=" + POP_D, token="z" * 32)[0], 401)
        self.assertEqual(self.get("?id=" + POP_D, token="c" * 32)[0], 403)

    def test_raw_tmdb_ids_unknown_ids_and_request_ids_are_refused(self):
        for q in ("?id=101", "?id=movie/101", "?id=movie%2F101", "?tmdb=101", "?id=" + POP_D + "&tmdb=5", "?id=" + POP_D + "&id=" + COMING_D, "", "?id=" + "x" * 300):
            self.assertEqual(self.get(q)[0], 400, q)
        self.assertEqual(self.get("?id=dUnknownxxxxxxxxxxxxxxxx")[0], 404)
        self.assertEqual(self.get("?id=tok-movie-xxxxxxxxxxxx")[0], 404, "a request id is not a details id")

    def test_coming_soon_ids_cannot_be_requested(self):
        self.assertEqual(self.post({"id": COMING_D})[0], 403)
        self.assertEqual(self.post({"id": COMING_D, "check": True})[0], 403)
        self.assertEqual(self.post({"id": POP_D})[0], 403)
        self.assertEqual(self.seerr.posts, [])

    def test_stale_or_missing_sidecars_answer_nothing(self):
        self.now = NOW + datetime.timedelta(days=10)
        self.assertEqual(self.get("?id=" + POP_D)[0], 404)
        self.now = NOW
        pathlib.Path(self.tmp.name, "coming.json").write_text("{broken")
        self.assertEqual(self.get("?id=" + COMING_D)[0], 404)
        self.assertEqual(self.get("?id=" + POP_D)[0], 200)

    def test_a_changed_sidecar_is_read_again(self):
        self.assertEqual(self.get("?id=" + POP_D)[0], 200)
        f = pathlib.Path(self.tmp.name, "details.json")
        f.write_text(json.dumps({"updated": "2026-10-28T22:20:00", "entries": {}}))
        import os
        os.utime(f, (time.time() + 5, time.time() + 5))
        self.assertEqual(self.get("?id=" + POP_D)[0], 404)

    def test_rate_limited_per_user_on_its_own_budget(self):
        for _ in range(self.core.cfg.details_per_min):
            self.assertEqual(self.get("?id=" + POP_D)[0], 200)
        self.assertEqual(self.get("?id=" + POP_D)[0], 429)
        self.assertEqual(self.get("?id=" + POP_D, token="b" * 32)[0], 200)
        self.assertNotEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx", "check": True})[0], 429, "browsing does not use up the request budget")

    def test_only_get(self):
        self.assertEqual(self.core.handle("POST", "/hss-popular/details?id=" + POP_D, {"X-Emby-Token": TOKEN}, b"")[0], 404)


class Server(unittest.TestCase):
    def setUp(self):
        self.timeout = app.Handler.timeout
        app.Handler.timeout = 0.5
        app.Handler.core = type("C", (), {"handle": lambda s, m, p, h, b: (200, {"ok": True, "len": len(b), "path": p})})()
        self.srv = app.make_server(("127.0.0.1", 0), workers=2, backlog=1)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        app.Handler.timeout = self.timeout

    def send(self, raw):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            s.sendall(raw)
            out = b""
            while chunk := s.recv(4096):
                out += chunk
            return out.decode(errors="replace")

    def test_production_timeouts(self):
        self.assertEqual(self.timeout, 10)
        self.assertLessEqual(app.UPSTREAM_TIMEOUT, 10)

    def test_a_silent_client_is_dropped_and_does_not_block_others(self):
        idle = [socket.create_connection(("127.0.0.1", self.port)) for _ in range(2)]
        try:
            started = time.monotonic()
            self.assertIn("200", self.send(b"GET /hss-popular/health HTTP/1.0\r\n\r\n").split("\r\n")[0])
            self.assertLess(time.monotonic() - started, 2.5)
            idle[0].settimeout(3)
            self.assertEqual(idle[0].recv(10), b"")
        finally:
            for s in idle:
                s.close()

    def test_content_length_is_validated(self):
        for value in ("-1", "abc", "1e9"):
            self.assertIn(" 400 ", self.send(f"POST /hss-popular/request HTTP/1.0\r\nContent-Length: {value}\r\n\r\n".encode()).split("\r\n")[0], value)
        self.assertIn('"len": 2', self.send(b"POST /hss-popular/request HTTP/1.0\r\nContent-Length: 2\r\n\r\n{}"))
        self.assertIn('"len": 0', self.send(b"POST /hss-popular/request HTTP/1.0\r\n\r\n"))

    def test_the_query_reaches_the_core_and_answers_are_not_cached(self):
        out = self.send(b"GET /hss-popular/details?id=abc HTTP/1.0\r\n\r\n")
        self.assertIn('"path": "/hss-popular/details?id=abc"', out)
        self.assertIn("Cache-Control: no-store", out)


class State(Base):
    def test_state_lists_every_entry_for_the_user(self):
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        code, body = self.core.handle("GET", "/hss-popular/state", {"X-Emby-Token": TOKEN}, b"")
        self.assertEqual(code, 200)
        self.assertEqual(body["left"], 9)
        self.assertEqual(body["entries"]["tok-movie-xxxxxxxxxxxx"]["state"], "requested")
        self.assertEqual(body["entries"]["tok-tv-xxxxxxxxxxxxxxx"]["state"], "requestable")
        self.assertNotIn("tok-nores-xxxxxxxxxxxx", body["entries"])

    def test_old_request_state_gives_no_searching_badge(self):
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        rs = pathlib.Path(self.tmp.name, "rs.json")
        rs.write_text(json.dumps({"updated": (NOW - datetime.timedelta(minutes=30)).isoformat(), "canWait": ["movie/101"]}))
        body = self.core.handle("GET", "/hss-popular/state", {"X-Emby-Token": TOKEN}, b"")[1]
        self.assertEqual(body["entries"]["tok-movie-xxxxxxxxxxxx"], {"state": "requested", "badge": None})
        rs.write_text(json.dumps({"updated": NOW.isoformat(), "canWait": ["movie/101"]}))
        body = self.core.handle("GET", "/hss-popular/state", {"X-Emby-Token": TOKEN}, b"")[1]
        self.assertEqual(body["entries"]["tok-movie-xxxxxxxxxxxx"]["badge"], "night")

    def test_state_needs_a_user(self):
        self.assertEqual(self.core.handle("GET", "/hss-popular/state", {}, b"")[0], 401)

    def test_unknown_path(self):
        self.assertEqual(self.core.handle("GET", "/hss-popular/../etc", {"X-Emby-Token": TOKEN}, b"")[0], 404)
        self.assertEqual(self.core.handle("GET", "/hss-popular/health", {}, b""), (200, {"ok": True}))

SECRET = "s3cr3t-hook-token-xxxxxxxxxxxxxxxxxxxx"
T0 = NOW
SEC = 10_000_000
SERIES = "aaaa1111bbbb2222cccc3333dddd4444"
SEASON = "eeee5555-ffff-6666-0000-777788889999"
CLEARED = {"PositionTicks": None, "MediaSourceId": None, "IsPaused": False, "CanSeek": False}


class HookBase(Base):
    def setUp(self):
        super().setUp()
        d = pathlib.Path(self.tmp.name)
        self.index = d / "trending-index.json"
        rec = {"x": "tok-movie-xxxxxxxxxxxx", "t": "Animals", "modes": {"ms_now": "now", "ms_night": "night"}}
        nope = {"x": None, "t": "Nope", "modes": {}}
        self.index.write_text(json.dumps({"updated": "2026-10-28T04:39:00+00:00", "library": "lib", "runtimeTicks": 10 * SEC, "items": {"ph1": rec, "ms_now": rec, "ms_night": rec, "ph2": nope},
                                          "parents": [SERIES, SEASON.replace("-", "")]}))
        self.signal = d / "signal" / "requested"
        self.signal.parent.mkdir()
        self.dry = d / "hook-dry-run"
        self.admin = []
        self.core = app.Core(app.Config(allow_file=self.allow, audit_file=d / "audit.jsonl", request_state=d / "rs.json", origins=[ORIGIN], hook_secret=SECRET, signal_file=self.signal, dry_flag=self.dry),
                             seerr=self.seerr, jellyfin=jellyfin, clock=lambda: self.now, jellyfin_admin=lambda m, p, b=None: self.admin.append((m, p, b)))
        self.mono = 0.0
        self.core.resets = app.Resetter(self.core.reset, clock=lambda: self.mono, autostart=False)

    def event(self, event, at, item="ph1", ms=None, pos=None, paused=False, user="u1", disabled=False, played=False, session="s1", secret=SECRET, runtime=10 * SEC, kind="Movie", extra=None):
        self.now = T0 + datetime.timedelta(seconds=at)
        ps = dict(CLEARED) if ms is None and pos is None else {"MediaSourceId": ms, "PositionTicks": pos, "IsPaused": paused, "CanSeek": True}
        body = {"Event": event, "Item": {"Id": item, "Type": kind, "RunTimeTicks": runtime, "UserData": {"Played": played}, **(extra or {})},
                "User": {"Id": user, "Name": "anna", "Policy": {"IsDisabled": disabled}},
                "Session": None if session is None else {"Id": session, "Client": "Android TV", "PlayState": ps}, "Server": {"Id": "srv"}}
        return self.core.hook("/hook/" + secret, {"Content-Type": "application/json"}, json.dumps(body).encode())

    def requests(self):
        return [p for p in self.seerr.posts if p[0] == "request"]

    def audits(self):
        f = pathlib.Path(self.tmp.name, "audit.jsonl")
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def reset_calls(self, item="ph1", user="u1"):
        return [c for c in self.admin if c == ("DELETE", f"/UserPlayedItems/{item}?userId={user}", None)]

    def run_later(self):
        for self.mono in (self.mono + 5, self.mono + 30):
            self.core.resets.run_due()


class Hook(HookBase):
    def test_hook_wrong_secret_403_and_counted(self):
        self.assertEqual(self.event("Stop", 10, ms="ms_night", pos=int(9.9 * SEC), secret="wrong")[0], 403)
        self.assertEqual(self.core.hook("/hook/", {}, b"{}")[0], 403)
        self.assertEqual(self.core.rejected(), {"bad hook secret": 2})
        self.assertEqual((self.requests(), self.admin, self.audits()), ([], [], []))

    def test_hook_bad_body(self):
        self.assertEqual(self.core.hook("/hook/" + SECRET, {}, b"{nope")[0], 400)
        self.assertEqual(self.core.hook("/hook/" + SECRET, {}, b"[]")[0], 400)
        self.assertEqual(self.core.hook("/hook/" + SECRET, {}, b"x" * (app.MAX_HOOK_BODY + 1))[0], 413)

    def test_hook_without_a_configured_secret_refuses_everything(self):
        self.core.cfg.hook_secret = None
        self.assertEqual(self.event("Stop", 10, ms="ms_night", pos=int(9.9 * SEC))[0], 403)

    def test_hook_stop_resets_again_after_5_and_30_s(self):
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("Stop", 3)
        self.assertEqual(len(self.reset_calls()), 1)
        self.assertEqual(self.core.resets.pending(), {("u1", "ph1"): [5, 30], ("u1", "ms_night"): [5, 30]})
        self.run_later()
        self.assertEqual(len(self.reset_calls()), 3)
        self.assertEqual(self.core.resets.pending(), {})

    def test_hook_stop_resets_the_played_version_too(self):
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("Stop", 3)
        self.assertEqual((len(self.reset_calls()), len(self.reset_calls(item="ms_night")), len(self.reset_calls(item="ms_now"))), (1, 1, 0))

    def test_hook_episode_stop_resets_episode_season_and_series(self):
        ids = {"SeriesId": SERIES, "SeasonId": SEASON}
        self.event("Play", 0, ms="ms_night", pos=0, kind="Episode", extra=ids)
        self.event("Stop", 3, kind="Episode", extra=ids)
        self.run_later()
        self.assertEqual(len(self.reset_calls()), 3)
        self.assertEqual(len(self.reset_calls(item="aaaa1111bbbb2222cccc3333dddd4444")), 3)
        self.assertEqual(len(self.reset_calls(item="eeee5555ffff66660000777788889999")), 3)

    def test_hook_bad_series_id_is_not_reset(self):
        self.event("Stop", 3, kind="Episode", extra={"SeriesId": "../Users", "SeasonId": 7})
        self.assertEqual({p.split("?")[0].split("/")[2] for _, p, _ in self.admin}, {"ph1"})

    def test_hook_series_and_season_outside_the_index_are_not_reset(self):
        self.event("MarkPlayed", 3, kind="Episode", played=True, session=None, extra={"SeriesId": "0123456789abcdef0123456789abcdef", "SeasonId": SEASON})
        self.assertEqual({p.split("?")[0].split("/")[2] for _, p, _ in self.admin}, {"ph1", SEASON.replace("-", "")})
        self.index.write_text(json.dumps({"items": {"ph1": {"x": None, "modes": {}}}}))
        self.admin.clear()
        self.event("MarkPlayed", 4, kind="Episode", played=True, session=None, extra={"SeriesId": SERIES, "SeasonId": SEASON})
        self.assertEqual({p.split("?")[0].split("/")[2] for _, p, _ in self.admin}, {"ph1"})

    def test_hook_mark_played_on_a_placeholder_resets_it(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.event("MarkPlayed", 3, played=True, session=None), (204, {}))
        self.assertEqual(out.getvalue(), "reset markplayed 1\n")
        self.assertEqual(len(self.reset_calls()), 1)
        self.run_later()
        self.assertEqual(len(self.reset_calls()), 3)
        self.assertEqual((self.requests(), self.audits()), ([], []))

    def test_hook_mark_played_already_unplayed_is_ignored(self):
        self.event("MarkPlayed", 3, played=False, session=None)
        self.event("MarkUnplayed", 4, played=False, session=None)
        self.assertEqual((self.admin, self.core.resets.pending()), ([], {}))

    def test_hook_mark_played_on_a_real_item_is_ignored(self):
        self.event("MarkPlayed", 3, item="realfilm", played=True, session=None)
        self.assertEqual((self.admin, self.core.resets.pending()), ([], {}))

    def test_hook_mark_played_does_not_disturb_the_tracked_play(self):
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("MarkPlayed", 5, played=True, session=None)
        self.event("Stop", 10.5)
        self.assertEqual(len(self.requests()), 1)

    def test_hook_unknown_item_204_silent(self):
        for ev, at in (("Play", 0), ("Progress", 5), ("Pause", 6), ("Stop", 3600)):
            self.assertEqual(self.event(ev, at, item="realfilm", ms="realms", pos=5 * SEC), (204, {}))
        self.assertEqual((self.requests(), self.admin, self.audits()), ([], [], []))

    def test_hook_play_then_stop_at_end_requests_night(self):
        self.assertEqual(self.event("Play", 0, ms="ms_night", pos=0)[0], 204)
        self.assertEqual(self.event("Stop", 10.5)[0], 204)
        self.assertEqual([(b["mediaId"], "tags" in b) for _, b, _ in self.requests()], [(101, False)])
        a = self.audits()[-1]
        self.assertEqual((a["decision"], a["via"], a["mode"], a["entry"], a["client"], a["userId"]), ("requested", "tv", "night", "tok-movie-xxxxxxxxxxxx", "Android TV", "u1"))
        self.assertTrue(self.signal.exists())
        self.assertEqual(len(self.reset_calls()), 1)

    def test_hook_now_version_adds_the_tag(self):
        self.event("Play", 0, ms="ms_now", pos=0)
        self.event("Stop", 10.2)
        self.assertEqual(self.requests()[-1][1]["tags"], [5])
        self.assertEqual(self.audits()[-1]["mode"], "now")

    def test_hook_stop_with_cleared_playstate_back_at_3s_no_request(self):
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("Stop", 3)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.audits()[-1]["decision"], "tv: stopped early")
        self.assertFalse(self.signal.exists())
        self.assertEqual(len(self.reset_calls()), 1)

    def test_hook_pause_time_not_counted(self):
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("Pause", 4, ms="ms_night", pos=4 * SEC, paused=True)
        self.event("Resume", 60, ms="ms_night", pos=4 * SEC)
        self.event("Stop", 63)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.audits()[-1]["decision"], "tv: stopped early")

    def test_hook_pause_without_position_keeps_the_estimate(self):
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("Pause", 4, ms="ms_night", pos=None, paused=True)
        self.event("Resume", 60, ms="ms_night", pos=None)
        self.event("Stop", 66.5)
        self.assertEqual(len(self.requests()), 1)

    def test_hook_progress_position_used(self):
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("Progress", 1, ms="ms_night", pos=int(9.5 * SEC))
        self.event("Stop", 1.2)
        self.assertEqual(len(self.requests()), 1)

    def test_hook_stop_playstate_position_preferred(self):
        self.event("Stop", 0, ms="ms_night", pos=int(9.9 * SEC))
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(self.audits()[-1]["mode"], "night")

    def test_hook_runtime_of_the_record_when_the_event_has_none(self):
        rec = {"x": "tok-movie-xxxxxxxxxxxx", "t": "Animals", "modes": {"ms_night": "night"}, "rt": 60 * SEC}
        self.index.write_text(json.dumps({"updated": "2026-10-28T04:39:00+00:00", "items": {"ph1": rec, "ms_night": rec}}))
        self.event("Stop", 0, ms="ms_night", pos=int(9.9 * SEC), runtime=None)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.audits()[-1]["decision"], "tv: stopped early")
        self.event("Stop", 1, ms="ms_night", pos=int(59.5 * SEC), runtime=None)
        self.assertEqual(len(self.requests()), 1)

    def test_hook_late_session_end_does_not_request(self):
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("Stop", 300)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.audits()[-1]["decision"], "tv: stale stop")

    def test_hook_old_tracked_records_are_dropped(self):
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("Play", 700, item="ph2", ms="ph2", pos=0)
        self.assertEqual(list(self.core.tracked), [("s1", "ph2")])

    def test_hook_ignores_played_flag(self):
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("Stop", 2, played=True)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.audits()[-1]["decision"], "tv: stopped early")

    def test_hook_unknown_version_no_request(self):
        self.event("Play", 0, ms="other", pos=0)
        self.event("Stop", 10.5)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.audits()[-1]["decision"], "tv: unknown version")

    def test_hook_sessions_are_tracked_apart(self):
        self.event("Play", 0, ms="ms_night", pos=0, session="s1")
        self.event("Play", 8, ms="ms_night", pos=0, session="s2")
        self.event("Stop", 10.5, session="s2")
        self.assertEqual(self.requests(), [])

    def test_hook_not_requestable_item_resets_only(self):
        self.event("Play", 0, item="ph2", ms="ph2", pos=0)
        self.event("Stop", 10.5, item="ph2")
        self.assertEqual((self.requests(), self.audits()), ([], []))
        self.assertEqual(len(self.reset_calls(item="ph2")), 1)

    def test_hook_reset_error_is_logged_by_type_only(self):
        def boom(m, p, b=None):
            raise app.urllib.error.URLError("http://jellyfin:8096/secret")
        self.core.jellyfin_admin = boom
        self.event("Play", 0, ms="ms_night", pos=0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.event("Stop", 10.5)[0], 204)
        self.assertIn("URLError", out.getvalue())
        self.assertNotIn("secret", out.getvalue())
        self.assertEqual(len(self.requests()), 1)

    def test_hook_disabled_user_refused(self):
        self.event("Play", 0, ms="ms_night", pos=0, disabled=True)
        self.event("Stop", 10.5, disabled=True)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.audits()[-1]["decision"], "disabled user")
        self.assertEqual(len(self.reset_calls()), 1)

    def test_hook_limits_shared_with_web(self):
        uid = "40e05f0a9e964fd9b225eb1379dfcfa4"
        for _ in range(10):
            self.assertEqual(self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"})[0], 200)
            self.core.hits.clear()
        self.event("Play", 0, ms="ms_night", pos=0, user="40e05f0a-9e96-4fd9-b225-eb1379dfcfa4")
        self.event("Stop", 10.5, user="40e05f0a-9e96-4fd9-b225-eb1379dfcfa4")
        self.assertEqual(len(self.requests()), 10)
        a = self.audits()[-1]
        self.assertEqual((a["decision"], a["userId"], a["via"]), ("refused: user limit", uid, "tv"))

    def test_hook_dry_flag_audits_without_seerr_post(self):
        self.dry.touch()
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("Stop", 10.5)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.audits()[-1]["decision"], "dry-run: requestable")
        self.assertEqual(self.core.overview("u1")[0], 10)
        self.assertFalse(self.signal.exists())
        self.assertEqual(len(self.reset_calls()), 1)

    def test_hook_dry_flag_still_audits_owned(self):
        self.dry.touch()
        self.seerr.media["movie/101"] = {"status": 5, "requests": []}
        self.event("Stop", 0, ms="ms_night", pos=int(9.9 * SEC))
        self.assertEqual(self.audits()[-1]["decision"], "refused: owned")

    def test_public_listener_has_no_hook_route(self):
        self.assertEqual(self.core.handle("POST", "/hook/" + SECRET, {"Content-Type": "application/json"}, b"{}")[0], 404)
        self.assertEqual(self.core.handle("POST", "/hss-popular/hook/" + SECRET, {"Content-Type": "application/json"}, b"{}")[0], 404)


class Resets(HookBase):
    def test_a_burst_uses_one_worker_and_one_entry_per_id(self):
        core = self.core
        core.resets = app.Resetter(core.reset, delays=(0.3, 0.6))
        self.addCleanup(core.resets.close)
        before = threading.active_count()
        with contextlib.redirect_stdout(io.StringIO()):
            for n in range(20):
                self.event("MarkPlayed", n, kind="Episode", played=True, session=None, extra={"SeriesId": SERIES, "SeasonId": SEASON})
        self.assertLessEqual(threading.active_count() - before, 1)
        self.assertEqual(len(core.resets.pending()), 3)
        deadline = time.monotonic() + 3
        while core.resets.pending() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(core.resets.pending(), {})
        self.assertEqual(len(self.reset_calls()), 20 + 2)
        self.assertEqual(threading.active_count() - before, 1)

    def test_adding_again_refreshes_the_due_times(self):
        r = self.core.resets
        r.add("u1", ["a"])
        self.mono = 10
        r.add("u1", ["b", "a"])
        self.assertEqual(r.pending(), {("u1", "b"): [15, 40], ("u1", "a"): [15, 40]})
        self.assertEqual(list(r.pending()), [("u1", "b"), ("u1", "a")])

    def test_late_due_times_run_once(self):
        self.core.resets.add("u1", ["ph1"])
        self.mono = 100
        self.core.resets.run_due()
        self.assertEqual((len(self.reset_calls()), self.core.resets.pending()), (1, {}))

    def test_queue_is_capped_dropping_the_oldest(self):
        calls = []
        r = app.Resetter(lambda iid, uid: calls.append(iid), cap=3, clock=lambda: 0, autostart=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            r.add("u1", ["a", "b", "c", "d", "e"])
        self.assertEqual(list(r.pending()), [("u1", "c"), ("u1", "d"), ("u1", "e")])
        self.assertEqual(out.getvalue(), "reset queue full, dropped 2\n")
        self.assertEqual(app.Resetter(print).cap, 500)

    def test_a_failing_reset_keeps_the_worker(self):
        def boom(iid, uid):
            raise ValueError("x")
        r = app.Resetter(boom, clock=lambda: self.mono, autostart=False)
        r.add("u1", ["a", "b"])
        self.mono = 5
        with contextlib.redirect_stdout(io.StringIO()) as out:
            r.run_due()
        self.assertEqual(out.getvalue(), "reset failed ValueError\nreset failed ValueError\n")
        self.assertEqual(len(r.pending()), 2)


class HookServer(unittest.TestCase):
    def setUp(self):
        self.seen = []
        app.HookHandler.core = type("C", (), {"hook": lambda s, p, h, b: self.seen.append((p, len(b))) or (204, {})})()
        self.srv = app.make_hook_server(("127.0.0.1", 0))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def send(self, raw):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as s:
            s.sendall(raw)
            out = b""
            while chunk := s.recv(4096):
                out += chunk
            return out.decode(errors="replace")

    def test_post_reaches_the_hook_and_the_path_is_never_logged(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            first = self.send(b"POST /hook/" + SECRET.encode() + b" HTTP/1.0\r\nContent-Length: 2\r\n\r\n{}")
            other = self.send(b"GET /hook/" + SECRET.encode() + b" HTTP/1.0\r\n\r\n")
            big = self.send(f"POST /hook/{SECRET} HTTP/1.0\r\nContent-Length: {app.MAX_HOOK_BODY + 5}\r\n\r\n".encode())
        self.assertIn(" 204 ", first.split("\r\n")[0])
        self.assertNotIn(" 2", other.split("\r\n")[0][8:12])
        self.assertIn(" 413 ", big.split("\r\n")[0])
        self.assertEqual(self.seen, [("/hook/" + SECRET, 2)])
        self.assertNotIn(SECRET, out.getvalue())

    def test_four_workers(self):
        self.assertEqual(self.srv.pool._max_workers, 4)


if __name__ == "__main__":
    unittest.main()
