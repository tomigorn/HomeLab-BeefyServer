import datetime
import json
import pathlib
import socket
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
    TOKEN: {"Id": "70d44a36-7988-405d-8792-444c1c716d9a", "Name": "tomigorn", "Policy": {"IsDisabled": False}},
    "b" * 32: {"Id": "11111111-2222-3333-4444-555555555555", "Name": "guest", "Policy": {"IsDisabled": False}},
    "c" * 32: {"Id": "99999999-2222-3333-4444-555555555555", "Name": "gone", "Policy": {"IsDisabled": True}},
}


class FakeSeerr:
    def __init__(self):
        self.users = [{"id": 7, "jellyfinUserId": "70d44a367988405d8792444c1c716d9a", "permissions": 8352, "email": "x"}]
        self.media = {}
        self.posts = []

    def __call__(self, method, path, body=None, user=None):
        if method == "GET" and path.startswith("/api/v1/user"):
            return {"results": self.users}
        if method == "POST" and path == "/api/v1/user":
            self.users.append({"id": 20, "email": body["email"], "permissions": 8352})
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
        self.assertEqual((a["user"], a["entry"], a["decision"]), ("tomigorn", "tok-movie-xxxxxxxxxxxy", "unknown entry"))


class Requests(Base):
    def test_night_request_as_the_linked_seerr_user(self):
        code, body = self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"})
        self.assertEqual(code, 200)
        self.assertEqual(body["left"], 9)
        _, sent, user = self.seerr.posts[-1]
        self.assertEqual((sent, user), ({"mediaType": "tv", "mediaId": 202, "seasons": [4]}, 7))
        a = self.audit()[-1]
        self.assertEqual((a["user"], a["title"], a["mode"], a["decision"], a["seerrUser"]), ("tomigorn", "The Diplomat", "night", "requested", 7))

    def test_now_adds_the_download_now_tag(self):
        self.post({"id": "tok-movie-xxxxxxxxxxxx", "mode": "now"})
        self.assertEqual(self.seerr.posts[-1][1], {"mediaType": "movie", "mediaId": 101, "tags": [5]})

    def test_unlinked_user_goes_through_the_service_user(self):
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token="b" * 32)[0], 200)
        self.assertEqual(self.seerr.posts[:2], [("user", app.SERVICE_EMAIL), ("permissions", 160)])
        self.assertEqual(self.seerr.posts[-1][2], 20)
        self.assertEqual(self.audit()[-1]["user"], "guest")

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
        (fresh / "audit.jsonl").write_text(json.dumps({"at": "2026-10-28T20:00:00+00:00", "decision": "requested", "userId": "70d44a367988405d8792444c1c716d9a", "ref": "movie/101"}) + "\n" +
                                           json.dumps({"at": "2026-10-28T20:00:00+00:00", "decision": "unknown entry", "userId": "70d44a367988405d8792444c1c716d9a"}) + "\n")
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


if __name__ == "__main__":
    unittest.main()
