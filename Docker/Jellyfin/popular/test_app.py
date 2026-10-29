import datetime
import json
import pathlib
import sys
import tempfile
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

    def test_check_only_sends_nothing(self):
        code, body = self.post({"id": "tok-movie-xxxxxxxxxxxx", "check": True})
        self.assertEqual((code, body["state"]), (200, "requestable"))
        self.assertEqual([p for p in self.seerr.posts if p[0] == "request"], [])
        self.assertEqual(body["left"], 10)


class State(Base):
    def test_state_lists_every_entry_for_the_user(self):
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        code, body = self.core.handle("GET", "/hss-popular/state", {"X-Emby-Token": TOKEN}, b"")
        self.assertEqual(code, 200)
        self.assertEqual(body["left"], 9)
        self.assertEqual(body["entries"]["tok-movie-xxxxxxxxxxxx"]["state"], "requested")
        self.assertEqual(body["entries"]["tok-tv-xxxxxxxxxxxxxxx"]["state"], "requestable")
        self.assertNotIn("tok-nores-xxxxxxxxxxxx", body["entries"])

    def test_state_needs_a_user(self):
        self.assertEqual(self.core.handle("GET", "/hss-popular/state", {}, b"")[0], 401)

    def test_unknown_path(self):
        self.assertEqual(self.core.handle("GET", "/hss-popular/../etc", {"X-Emby-Token": TOKEN}, b"")[0], 404)
        self.assertEqual(self.core.handle("GET", "/hss-popular/health", {}, b""), (200, {"ok": True}))


if __name__ == "__main__":
    unittest.main()
