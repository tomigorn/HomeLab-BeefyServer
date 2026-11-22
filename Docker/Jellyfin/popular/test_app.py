import contextlib
import datetime
import io
import json
import os
import pathlib
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app  # noqa: E402

NOW = datetime.datetime(2026, 10, 29, 1, 0, tzinfo=datetime.timezone.utc)
ORIGIN = "https://jellyfin.holy-grail.ch"
TOKEN = "a" * 32
USERS = {
    TOKEN: {"Id": "40e05f0a-9e96-4fd9-b225-eb1379dfcfa4", "Name": "jane", "Policy": {"IsDisabled": False}},
    "b" * 32: {"Id": "11111111-2222-3333-4444-555555555555", "Name": "guest", "Policy": {"IsDisabled": False}},
    "c" * 32: {"Id": "99999999-2222-3333-4444-555555555555", "Name": "gone", "Policy": {"IsDisabled": True}},
    "d" * 32: {"Id": "77777777-2222-3333-4444-555555555555", "Name": "boss", "Policy": {"IsDisabled": False, "IsAdministrator": True}},
}


class FakeSeerr:
    def __init__(self):
        self.users = [{"id": 7, "jellyfinUserId": "40e05f0a9e964fd9b225eb1379dfcfa4", "permissions": 8352, "email": "x"}]
        self.media = {}
        self.posts = []
        self.linked = {}
        self.link_gets = 0

    def __call__(self, method, path, body=None, user=None):
        if method == "GET" and path.startswith("/api/v1/user/") and path.endswith("/settings/linked-accounts"):
            self.link_gets += 1
            return self.linked.get(int(path.split("/")[4]), [])
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

    def test_admin_exempt_from_per_user_limit(self):
        self.core.cfg.limit = 1
        code, body = self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token="d" * 32)
        self.assertEqual((code, body["left"]), (200, None))
        self.assertEqual(self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"}, token="d" * 32)[0], 200)
        self.assertIsNone(self.core.overview("77777777222233334444555555555555", True)[0])
        self.assertEqual(self.core.overview("77777777222233334444555555555555")[0], 0)

    def test_admin_still_under_daily_cap(self):
        self.core.cfg.daily_max = 1
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"})[0], 200)
        code, body = self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"}, token="d" * 32)
        self.assertEqual((code, body["error"]), (429, "daily cap"))

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


def holy_grail(name, slug="holy-grail"):
    return [{"id": 1, "username": name, "provider": {"slug": slug, "name": "Holy Grail"}}]


class Attribution(Base):
    GUEST = "b" * 32

    def setUp(self):
        super().setUp()
        self.seerr.users.append({"id": 20, "email": app.SERVICE_EMAIL, "permissions": 160, "userType": 2})
        self.seerr.users.append({"id": 5, "email": "x5", "permissions": 24736, "userType": 2})

    def sent_as(self):
        return [p for p in self.seerr.posts if p[0] == "request"][-1][2]

    def test_holy_grail_account_with_the_jellyfin_name_is_the_requester(self):
        self.seerr.linked[5] = holy_grail("Guest")
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=self.GUEST)[0], 200)
        self.assertEqual(self.sent_as(), 5)
        a = self.audit()[-1]
        self.assertEqual((a["via"], a["seerrUser"], a["person"], a["source"], a["match"]), ("linked Seerr user", 5, 5, "jellyfin", "name"))

    def test_other_sign_in_provider_does_not_count(self):
        self.seerr.linked[5] = holy_grail("guest", slug="elsewhere")
        self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=self.GUEST)
        self.assertEqual(self.sent_as(), 20)
        self.assertEqual((self.audit()[-1]["via"], self.audit()[-1]["person"]), ("service user: no Seerr account", None))

    def test_two_accounts_with_the_name_fall_back(self):
        self.seerr.users.append({"id": 6, "email": "x6", "permissions": 24736, "userType": 2})
        self.seerr.linked[5] = self.seerr.linked[6] = holy_grail("guest")
        self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=self.GUEST)
        self.assertEqual(self.sent_as(), 20)
        self.assertEqual(self.audit()[-1]["via"], "service user: ambiguous Seerr account")

    def test_jellyfin_link_wins_without_asking_linked_accounts(self):
        self.seerr.linked[5] = holy_grail("jane")
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.assertEqual((self.sent_as(), self.seerr.link_gets), (7, 0))

    def test_service_user_is_never_the_person(self):
        self.seerr.linked[20] = holy_grail("guest")
        self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=self.GUEST)
        self.assertEqual((self.sent_as(), self.audit()[-1]["person"]), (20, None))

    def test_without_auto_approve_the_service_user_requests_for_the_person(self):
        self.seerr.users[0]["permissions"] = 32 | 8192 | 16384
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.assertEqual(self.sent_as(), 20)
        a = self.audit()[-1]
        self.assertEqual((a["via"], a["seerrUser"], a["person"]), ("service user: linked Seerr user not auto-approved", 20, 7))

    def test_permissions_per_kind(self):
        self.seerr.users[0]["permissions"] = 262144 | 256
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.assertEqual(self.sent_as(), 7)
        self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"})
        self.assertEqual(self.sent_as(), 20)
        self.assertEqual(self.audit()[-1]["via"], "service user: linked Seerr user may not request")

    def test_admin_may_everything(self):
        self.seerr.users[0]["permissions"] = 2
        self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"})
        self.assertEqual(self.sent_as(), 7)

    def test_linked_accounts_cached_5_min(self):
        self.seerr.linked[5] = holy_grail("guest")
        self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=self.GUEST)
        n = self.seerr.link_gets
        self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"}, token=self.GUEST)
        self.assertEqual(self.seerr.link_gets, n)
        self.now = NOW + datetime.timedelta(minutes=5)
        self.core.seerr_user("11111111222233334444555555555555", name="guest", kind="movie")
        self.assertGreater(self.seerr.link_gets, n)

    def test_linked_accounts_error_means_no_link(self):
        inner = self.seerr
        def seerr(m, path, body=None, user=None):
            if path.endswith("/settings/linked-accounts"):
                raise OSError("down")
            return inner(m, path, body, user)
        self.core.seerr = seerr
        self.assertEqual(self.core.seerr_user("11111111222233334444555555555555", name="guest", kind="movie")[1], "service user: sign-in accounts unreadable")


    def test_one_unreadable_account_list_means_no_name_match_and_no_cache(self):
        self.seerr.users.append({"id": 6, "email": "x6", "permissions": 24736, "userType": 2})
        self.seerr.linked[5] = self.seerr.linked[6] = holy_grail("guest")
        inner = self.seerr
        def seerr(m, path, body=None, user=None):
            if path == "/api/v1/user/6/settings/linked-accounts":
                raise OSError("down")
            return inner(m, path, body, user)
        self.core.seerr = seerr
        self.assertEqual(self.core.seerr_user("11111111222233334444555555555555", name="guest", kind="movie")[0], 20)
        self.core.seerr = inner
        self.assertEqual(self.core.seerr_user("11111111222233334444555555555555", name="guest", kind="movie")[1], "service user: ambiguous Seerr account")

    def test_account_linked_to_another_jellyfin_user_is_never_matched_by_name(self):
        self.seerr.users[2]["jellyfinUserId"] = "ffffffffffffffffffffffffffffffff"
        self.seerr.linked[5] = holy_grail("guest")
        self.assertEqual(self.core.seerr_user("11111111222233334444555555555555", name="guest", kind="movie")[:2], (20, "service user: no Seerr account"))

    def test_odd_linked_accounts_answer_never_breaks_a_request(self):
        self.seerr.linked[5] = [{"username": "guest", "provider": "holy-grail"}, "x", None]
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=self.GUEST)[0], 200)
        self.assertEqual(self.sent_as(), 20)


class SubjectMatch(Base):
    GUEST = "b" * 32
    GUEST_ID = "11111111222233334444555555555555"

    def setUp(self):
        super().setUp()
        self.seerr.users.append({"id": 20, "email": app.SERVICE_EMAIL, "permissions": 160, "userType": 2})
        self.seerr.users.append({"id": 5, "email": "x5", "permissions": 24736, "userType": 2})
        self.ids = pathlib.Path(self.tmp.name, "identity.json")
        self.core.cfg.identity_file = self.ids

    def identities(self, jellyfin, seerr):
        self.ids.write_text(json.dumps({"updated": "2026-10-31T00:00:00", "jellyfin": jellyfin, "seerr": seerr}))

    def sent_as(self):
        return [p for p in self.seerr.posts if p[0] == "request"][-1][2]

    def test_same_sign_in_subject_is_the_requester(self):
        self.identities({self.GUEST_ID: "sub-guest"}, {"5": "sub-guest"})
        self.seerr.linked[5] = holy_grail("someone-else")
        self.assertEqual(self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token=self.GUEST)[0], 200)
        a = self.audit()[-1]
        self.assertEqual((self.sent_as(), a["via"], a["person"], a["match"]), (5, "linked Seerr user", 5, "subject"))
        self.assertEqual(self.seerr.link_gets, 0)

    def test_renamed_user_keeps_their_account_and_the_new_holder_of_the_old_name_does_not_get_it(self):
        self.identities({self.GUEST_ID: "sub-guest", "22222222222233334444555555555555": "sub-new"}, {"5": "sub-guest"})
        self.seerr.linked[5] = holy_grail("guest")
        self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="guest-renamed", kind="movie"), (5, "linked Seerr user", 5, "subject"))
        self.assertEqual(self.core.seerr_user("22222222222233334444555555555555", name="guest", kind="movie"), (20, "service user: no Seerr account", None, None))

    def test_account_with_a_known_other_subject_is_never_matched_by_name(self):
        self.identities({}, {"5": "sub-guest"})
        self.seerr.linked[5] = holy_grail("guest")
        self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="guest", kind="movie"), (20, "service user: no Seerr account", None, None))

    def test_name_fallback_only_where_the_subject_is_unknown(self):
        self.identities({self.GUEST_ID: "sub-guest"}, {})
        self.seerr.linked[5] = holy_grail("Guest")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="guest", kind="movie"), (5, "linked Seerr user", 5, "name"))
        self.assertIn("name fallback", out.getvalue())

    def test_without_any_identity_map_the_name_is_the_fallback(self):
        self.seerr.linked[5] = holy_grail("guest")
        self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="guest", kind="movie")[3], "name")
        self.ids.write_text("{not json")
        self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="guest", kind="movie")[3], "name")

    def test_broken_identity_file_keeps_the_last_good_map(self):
        self.seerr.linked[5] = holy_grail("guest")
        self.identities({}, {"5": "sub-guest"})
        self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="guest", kind="movie")[:3], (20, "service user: no Seerr account", None))
        for bad in ("{not json", json.dumps({"jellyfin": [], "seerr": "x"}), json.dumps({"jellyfin": {}, "seerr": {"x": "s"}})):
            self.ids.write_text(bad)
            self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="guest", kind="movie")[:3], (20, "service user: no Seerr account", None), bad)
        self.ids.unlink()
        self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="guest", kind="movie")[:3], (20, "service user: no Seerr account", None))

    def test_two_unmapped_accounts_with_the_name_are_ambiguous(self):
        self.seerr.users.append({"id": 6, "email": "x6", "permissions": 24736, "userType": 2})
        self.identities({self.GUEST_ID: "sub-guest"}, {})
        self.seerr.linked[5] = self.seerr.linked[6] = holy_grail("guest")
        self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="guest", kind="movie")[:3], (20, "service user: ambiguous Seerr account", None))

    def test_identity_file_reread_when_it_changes(self):
        self.identities({self.GUEST_ID: "sub-guest"}, {})
        self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="nobody", kind="movie")[0], 20)
        self.identities({self.GUEST_ID: "sub-guest"}, {"5": "sub-guest"})
        os.utime(self.ids, ns=(1, 2))
        self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="nobody", kind="movie")[3], "subject")

    def test_jellyfin_link_comes_first(self):
        self.identities({"40e05f0a9e964fd9b225eb1379dfcfa4": "sub-jane"}, {"5": "sub-jane"})
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.assertEqual((self.sent_as(), self.audit()[-1]["match"]), (7, "jellyfin"))

    def test_subject_of_an_account_linked_to_another_jellyfin_user_does_not_count(self):
        self.seerr.users[2]["jellyfinUserId"] = "ffffffffffffffffffffffffffffffff"
        self.identities({self.GUEST_ID: "sub-guest"}, {"5": "sub-guest"})
        self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="guest", kind="movie")[:3], (20, "service user: no Seerr account", None))

    def test_two_accounts_with_the_subject_are_ambiguous(self):
        self.seerr.users.append({"id": 6, "email": "x6", "permissions": 24736, "userType": 2})
        self.identities({self.GUEST_ID: "sub-guest"}, {"5": "sub-guest", "6": "sub-guest"})
        self.assertEqual(self.core.seerr_user(self.GUEST_ID, name="guest", kind="movie")[:3], (20, "service user: ambiguous Seerr account", None))

    def test_dry_run_says_which_path_matched(self):
        self.identities({self.GUEST_ID: "sub-guest"}, {"5": "sub-guest"})
        out = self.core.dry_who(self.GUEST_ID, "guest", "movie", {})
        self.assertEqual((out["dryAs"], out["match"]), (5, "subject"))
        self.assertEqual([p for p in self.seerr.posts if p[0] == "request"], [])


class Origins(Base):
    def setUp(self):
        super().setUp()
        self.export = pathlib.Path(self.tmp.name, "export", "origins.json")
        self.core.cfg.export_file = self.export
        self.seerr.users.append({"id": 20, "email": app.SERVICE_EMAIL, "permissions": 160, "userType": 2})

    def origins(self):
        return json.loads(self.export.read_text())

    def test_web_request_exported_with_source_and_person(self):
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"}, token="b" * 32)
        reqs = self.origins()["requests"]
        ids = sorted(reqs, key=int)
        self.assertEqual(reqs[ids[0]], {"source": "jellyfin", "name": "jane", "seerrUser": 7, "person": 7, "service": False})
        self.assertEqual(reqs[ids[1]], {"source": "jellyfin", "name": "guest", "seerrUser": 20, "person": None, "service": True})
        self.assertNotIn("@", self.export.read_text())
        self.assertNotIn("userId", self.export.read_text())

    def jellyfin_names(self, **names):
        self.jf_users = [{"Id": "40e05f0a-9e96-4fd9-b225-eb1379dfcfa4", "Name": names.get("jane", "jane")},
                         {"Id": "11111111-2222-3333-4444-555555555555", "Name": names.get("guest", "guest")}]
        def admin(method, path, body=None):
            if method == "GET" and path == "/Users":
                return self.jf_users
            raise AssertionError((method, path))
        self.core.jellyfin_admin = admin

    def test_export_shows_the_current_jellyfin_name(self):
        self.jellyfin_names(guest="Guesty")
        self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"}, token="b" * 32)
        self.assertEqual([v["name"] for v in self.origins()["requests"].values()], ["Guesty"])
        self.jf_users[1]["Name"] = "Guest Renamed"
        self.now = NOW + datetime.timedelta(minutes=5)
        self.core.refresh_origins()
        self.assertEqual([v["name"] for v in self.origins()["requests"].values()], ["Guest Renamed"])

    def test_export_takes_the_seerr_display_name_without_a_jellyfin_name(self):
        self.seerr.users[0]["displayName"] = "Jane D"
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.assertEqual([v["name"] for v in self.origins()["requests"].values()], ["Jane D"])

    def test_mail_like_current_names_are_never_exported(self):
        self.seerr.users[0]["displayName"] = "jane@example.com"
        self.jellyfin_names(jane="jane@example.com")
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.assertEqual([v["name"] for v in self.origins()["requests"].values()], ["jane"])
        self.assertNotIn("@", self.export.read_text())

    def test_name_lookups_failing_keep_the_stored_name(self):
        def down(*a, **k):
            raise OSError("down")
        self.core.jellyfin_admin = down
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.core.seerr = down
        self.now = NOW + datetime.timedelta(minutes=5)
        self.core.refresh_origins()
        self.assertEqual([v["name"] for v in self.origins()["requests"].values()], ["jane"])

    def test_failed_name_lookups_are_not_retried_for_30_s(self):
        calls = []
        def down(*a, **k):
            calls.append(1)
            raise OSError("down")
        self.core.jellyfin_admin = down
        self.core.current_names()
        self.core.current_names()
        self.assertEqual(len(calls), 1)
        self.now = NOW + datetime.timedelta(seconds=31)
        self.core.current_names()
        self.assertEqual(len(calls), 2)

    def test_refresh_rewrites_only_on_change(self):
        self.jellyfin_names()
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        first = self.origins()["updated"]
        self.now = NOW + datetime.timedelta(minutes=10)
        self.core.refresh_origins()
        self.assertEqual(self.origins()["updated"], first)
        self.jf_users[0]["Name"] = "Jane New"
        self.now = NOW + datetime.timedelta(minutes=20)
        self.core.refresh_origins()
        self.assertNotEqual(self.origins()["updated"], first)
        self.assertEqual([v["name"] for v in self.origins()["requests"].values()], ["Jane New"])

    def test_moonfin_and_tv_sources(self):
        e = self.core.allow()["tok-movie-xxxxxxxxxxxx"]
        jane = {"id": "40e05f0a9e964fd9b225eb1379dfcfa4", "name": "jane"}
        self.core.send(jane, e, "night", False, {"user": "jane", "userId": jane["id"], "via": "moonfin"})
        self.seerr.media.clear()
        self.core.send(jane, self.core.allow()["tok-tv-xxxxxxxxxxxxxxx"], "night", False, {"user": "jane", "userId": jane["id"], "via": "tv"})
        self.assertEqual([v["source"] for _, v in sorted(self.origins()["requests"].items(), key=lambda kv: int(kv[0]))], ["moonfin", "tv"])
        self.assertEqual(self.audit()[-1]["source"], "tv")

    def test_written_at_start_and_unwritable_never_breaks_a_request(self):
        self.post({"id": "tok-movie-xxxxxxxxxxxx"})
        self.export.unlink()
        core = app.Core(app.Config(allow_file=self.allow, audit_file=self.core.cfg.audit_file, request_state=self.core.cfg.request_state, origins=[ORIGIN], export_file=self.export),
                        seerr=self.seerr, jellyfin=jellyfin, clock=lambda: self.now)
        self.assertEqual(len(self.origins()["requests"]), 1)
        core.cfg.export_file = pathlib.Path(self.tmp.name, "allow.json", "x.json")
        core.seerr = self.seerr
        self.assertEqual(core.handle("POST", "/hss-popular/request", {"Content-Type": "application/json", "Origin": ORIGIN, "X-Emby-Token": "b" * 32}, json.dumps({"id": "tok-tv-xxxxxxxxxxxxxxx"}).encode())[0], 200)

    def test_half_migrated_state_db_gets_the_missing_columns(self):
        d = pathlib.Path(self.tmp.name, "half")
        d.mkdir()
        con = sqlite3.connect(d / "state.db")
        con.execute("CREATE TABLE user_requests (user_id TEXT NOT NULL, seerr_id INTEGER NOT NULL, at TEXT, source TEXT, PRIMARY KEY (user_id, seerr_id))")
        con.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, ref TEXT, at TEXT NOT NULL, day TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0)")
        con.commit()
        con.close()
        core = app.Core(app.Config(allow_file=self.allow, audit_file=d / "audit.jsonl", request_state=d / "rs.json", origins=[ORIGIN], export_file=d / "o.json"),
                        seerr=self.seerr, jellyfin=jellyfin, clock=lambda: self.now)
        core.remember("u1", 5, ("tv", "tom", 7, 7, 0))
        self.assertEqual(json.loads((d / "o.json").read_text())["requests"]["5"]["source"], "tv")

    def test_old_state_db_gets_the_columns_and_the_audit_backfill(self):
        d = pathlib.Path(self.tmp.name, "old")
        d.mkdir()
        con = sqlite3.connect(d / "state.db")
        con.execute("CREATE TABLE user_requests (user_id TEXT NOT NULL, seerr_id INTEGER NOT NULL, at TEXT, PRIMARY KEY (user_id, seerr_id))")
        con.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, ref TEXT, at TEXT NOT NULL, day TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0)")
        for rid in (64, 65, 70):
            con.execute("INSERT INTO user_requests VALUES ('u1', ?, '2026-10-30T00:00:00+00:00')", (rid,))
        con.commit()
        con.close()
        lines = [
            {"decision": "requested", "requestId": 64, "user": "tom", "userId": "u1", "via": "tv", "seerrVia": "linked Seerr user", "seerrUser": 7, "at": "2026-10-30T00:00:00+00:00"},
            {"decision": "requested", "requestId": 65, "user": "tom", "userId": "u1", "via": "moonfin", "seerrVia": "service user: no Seerr account", "seerrUser": 8, "at": "2026-10-30T00:00:00+00:00"},
            {"decision": "requested", "requestId": 70, "user": "tom", "userId": "u1", "via": "linked Seerr user", "seerrUser": 7, "at": "2026-10-30T00:00:00+00:00"},
            {"decision": "dry-run", "user": "tom", "userId": "u1", "via": "moonfin"},
        ]
        (d / "audit.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
        export = d / "origins.json"
        app.Core(app.Config(allow_file=self.allow, audit_file=d / "audit.jsonl", request_state=d / "rs.json", origins=[ORIGIN], export_file=export),
                 seerr=self.seerr, jellyfin=jellyfin, clock=lambda: self.now)
        reqs = json.loads(export.read_text())["requests"]
        self.assertEqual(reqs, {"64": {"source": "tv", "name": "tom", "seerrUser": 7, "person": 7, "service": False},
                                "65": {"source": "moonfin", "name": "tom", "seerrUser": 8, "person": None, "service": True},
                                "70": {"source": "jellyfin", "name": "tom", "seerrUser": 7, "person": 7, "service": False}})


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

    def test_state_admin_has_no_limit(self):
        self.core.cfg.limit = 1
        self.post({"id": "tok-movie-xxxxxxxxxxxx"}, token="d" * 32)
        body = self.core.handle("GET", "/hss-popular/state", {"X-Emby-Token": "d" * 32}, b"")[1]
        self.assertEqual((body["left"], body["dailyFull"]), (None, False))
        self.assertEqual(self.core.handle("GET", "/hss-popular/state", {"X-Emby-Token": TOKEN}, b"")[1]["left"], 1)

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

    def event(self, event, at, item="ph1", ms=None, pos=None, paused=False, user="u1", disabled=False, admin=False, played=False, session="s1", secret=SECRET, runtime=10 * SEC, kind="Movie", extra=None):
        self.now = T0 + datetime.timedelta(seconds=at)
        ps = dict(CLEARED) if ms is None and pos is None else {"MediaSourceId": ms, "PositionTicks": pos, "IsPaused": paused, "CanSeek": True}
        body = {"Event": event, "Item": {"Id": item, "Type": kind, "RunTimeTicks": runtime, "UserData": {"Played": played}, **(extra or {})},
                "User": {"Id": user, "Name": "anna", "Policy": {"IsDisabled": disabled, "IsAdministrator": admin}},
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

    def test_hook_admin_exempt_from_user_limit(self):
        uid = "40e05f0a9e964fd9b225eb1379dfcfa4"
        for _ in range(10):
            self.assertEqual(self.post({"id": "tok-tv-xxxxxxxxxxxxxxx"})[0], 200)
            self.core.hits.clear()
        self.event("Play", 0, ms="ms_night", pos=0, user="40e05f0a-9e96-4fd9-b225-eb1379dfcfa4", admin=True)
        self.event("Stop", 10.5, user="40e05f0a-9e96-4fd9-b225-eb1379dfcfa4", admin=True)
        self.assertEqual(len(self.requests()), 11)
        self.assertEqual(self.audits()[-1]["userId"], uid)
        self.assertNotEqual(self.audits()[-1]["decision"], "refused: user limit")

    def test_hook_dry_flag_audits_without_seerr_post(self):
        self.dry.touch()
        self.event("Play", 0, ms="ms_night", pos=0)
        self.event("Stop", 10.5)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.audits()[-1]["decision"], "dry-run: requestable")
        self.assertEqual(self.core.overview("u1")[0], 10)
        self.assertEqual({k: self.audits()[-1].get(k) for k in ("dryVia", "source")}, {"dryVia": "service user missing", "source": "tv"})
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


class DownloadBlocked(Base):
    DEVICE = "a4859e97-7caf-4d39-a378-60798b8c22a8"
    PATH = "/hss-popular/download-blocked"

    def setUp(self):
        super().setUp()
        self.calls = []
        self.sessions = [
            {"Id": "sess-phone", "DeviceId": self.DEVICE, "UserId": USERS[TOKEN]["Id"]},
            {"Id": "sess-other-user", "DeviceId": self.DEVICE, "UserId": USERS["b" * 32]["Id"]},
            {"Id": "sess-other-device", "DeviceId": "other-device", "UserId": USERS[TOKEN]["Id"]},
        ]

        def admin(method, path, body=None):
            self.calls.append((method, path, body))
            if method == "GET" and path.startswith("/Sessions?"):
                return self.sessions
            return None

        self.core.jellyfin_admin = admin
        self.core.spawn = lambda fn: fn()

    def hit(self, query, method="GET"):
        return self.core.handle(method, f"{self.PATH}?{query}", {}, b"")

    def query(self, device=DEVICE, token=TOKEN, play="84f05907-8109-4bb2-95ab-73e3cc3045e3"):
        return f"MediaSourceId=x&ApiKey={token}&Static=false&container=mp4&PlaySessionId={play}&DeviceId={device}"

    def messages(self):
        return [(p, b) for m, p, b in self.calls if m == "POST"]

    def test_refused_and_message_goes_only_to_the_callers_session_on_that_device(self):
        code, body = self.hit(self.query())
        self.assertEqual(code, 403)
        self.assertIn("Original", body["error"])
        self.assertEqual(self.calls[0][:2], ("GET", f"/Sessions?deviceId={self.DEVICE}"))
        self.assertEqual(self.messages(), [("/Sessions/sess-phone/Message", {"Header": app.DOWNLOAD_HEADER, "Text": app.DOWNLOAD_TEXT, "TimeoutMs": app.DOWNLOAD_TIMEOUT_MS})])

    def test_message_text_is_fixed(self):
        self.assertEqual(app.DOWNLOAD_HEADER, "📱 Download")
        self.assertEqual(app.DOWNLOAD_TEXT, "Converted downloads are switched off. Please choose 'Original'. A smaller phone version will come automatically later.")
        self.hit(self.query() + "&Header=evil&Text=evil")
        self.assertEqual(self.messages()[0][1]["Text"], app.DOWNLOAD_TEXT)

    def test_one_message_per_device_within_two_minutes(self):
        for _ in range(4):
            self.assertEqual(self.hit(self.query())[0], 403)
        self.assertEqual(len(self.messages()), 1)
        self.assertEqual(len(self.calls), 2)
        self.now = NOW + datetime.timedelta(seconds=119)
        self.hit(self.query(play="another-play-session"))
        self.assertEqual(len(self.messages()), 1)
        self.now = NOW + datetime.timedelta(seconds=121)
        self.hit(self.query())
        self.assertEqual(len(self.messages()), 2)

    def test_lowercase_query_keys(self):
        self.hit(f"api_key={TOKEN}&static=false&deviceId={self.DEVICE}")
        self.assertEqual(len(self.messages()), 1)

    def test_unknown_device_gets_no_message(self):
        self.assertEqual(self.hit(self.query(device="fake-device-0000"))[0], 403)
        self.assertEqual(self.messages(), [])

    def test_no_or_bad_token_no_lookup(self):
        for token in ("", "z" * 32, "c" * 32):
            self.assertEqual(self.hit(self.query(token=token))[0], 403)
        self.assertEqual(self.messages(), [])

    def test_no_or_bad_device_id_no_lookup(self):
        for q in (f"ApiKey={TOKEN}&Static=false", self.query(device="x" * 200), self.query(device="a%22b"), self.query(device="a%0Ab")):
            self.assertEqual(self.hit(q)[0], 403)
        self.assertEqual(self.calls, [])

    def test_any_method_is_refused(self):
        self.assertEqual(self.hit(self.query(), method="POST")[0], 403)

    def test_jellyfin_errors_still_refuse(self):
        def down(method, path, body=None):
            raise OSError("down")
        self.core.jellyfin_admin = down
        self.assertEqual(self.hit(self.query())[0], 403)

    def test_cache_is_bounded(self):
        for i in range(app.DOWNLOAD_NOTICE_CAP + 10):
            self.hit(self.query(device=f"device-{i}"))
        self.assertLessEqual(len(self.core.notified), app.DOWNLOAD_NOTICE_CAP)

    def test_without_admin_key_still_refused(self):
        self.core.jellyfin_admin = None
        self.assertEqual(self.hit(self.query())[0], 403)

    def test_query_is_not_logged(self):
        h = app.Handler.__new__(app.Handler)
        h.command, h.path, h.requestline = "GET", self.PATH + "?" + self.query(), "GET " + self.PATH + "?" + self.query() + " HTTP/1.1"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            h.log_request(403)
        self.assertIn(self.PATH, out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue())


class ClientKind(unittest.TestCase):
    """The one place that says what a Jellyfin client is: names as the apps and the server's device list show them (Jellyfin /Devices on beefy 2026-11-22, app sources)."""

    def test_tv_apps(self):
        for name in ("Moonfin for Android TV", "Moonfin for tvOS", "Moonfin for Tizen", "Jellyfin Android TV", "Swiftfin tvOS", "Jellyfin for Tizen", "Jellyfin for WebOS"):
            self.assertEqual(app.client_kind(name), "tv", name)

    def test_the_web_client_is_a_tv_only_on_a_tv_device_name(self):
        for device in ("Samsung Smart TV", "LG Smart TV", "Xbox One", "Sony PS4"):
            self.assertEqual(app.client_kind("Jellyfin Web", device), "tv", device)
        for device in ("Chrome Android", "Safari iPhone", "Firefox iPad", "Jane's iPhone", "", None):
            self.assertIsNone(app.client_kind("Jellyfin Web", device), device)

    def test_phone_apps(self):
        for name in ("Moonfin for Android", "Moonfin for iOS", "Jellyfin Android", "Jellyfin iOS", "Swiftfin iOS", "Findroid"):
            self.assertEqual(app.client_kind(name), "phone", name)

    def test_desktop_apps(self):
        # Moonfin-Core injection.dart _clientName; Jellyfin Media Player 1.x / Jellyfin Desktop 2.x nativeshell.js appName
        for name in ("Moonfin for Windows", "Moonfin for macOS", "Moonfin for Linux", "Jellyfin Media Player", "Jellyfin Desktop"):
            self.assertEqual(app.client_kind(name, "SurfaceLap6A Windows 11 Pro"), "desktop", name)

    def test_the_web_client_is_a_desktop_on_a_desktop_browser_name(self):
        # jellyfin-web apphost.js BrowserName, without the " iPad" / " iPhone" / " Android" suffix of mobile browsers
        for device in ("Chrome", "Edge Chromium", "Edge", "Firefox", "Opera", "Safari", "Web Browser"):
            self.assertEqual(app.client_kind("Jellyfin Web", device), "desktop", device)

    def test_everything_else_is_neither(self):
        for name in ("Moonfin for Web", "Seerr", "hss-popular", "Kodi", "", None):
            self.assertIsNone(app.client_kind(name), name)
        self.assertIsNone(app.client_kind("Seerr", "Chrome"))


class PhoneBase(Base):
    """Fakes shared by Original and converted downloads: a 4K episode, its phone version, the user's devices and sessions."""
    ITEM = "72a1b6d4112a1f5f68e7b23409a9069f"
    PHONE = "0123456789abcdef0123456789abcdef"
    DEVICE = "a4859e97-7caf-4d39-a378-60798b8c22a8"
    IP = "203.0.113.7"

    def setUp(self):
        super().setUp()
        self.box = pathlib.Path(self.tmp.name) / "phone"
        (self.box / "requests").mkdir(parents=True)
        self.core.cfg.phone_box = str(self.box)
        self.calls = []
        self.height = 2160
        self.language = None
        self.sessions = [{"Id": "sess-phone", "DeviceId": self.DEVICE, "UserId": USERS[TOKEN]["Id"], "Client": "Moonfin for Android"}]
        uid = USERS[TOKEN]["Id"]
        self.devices = [{"Id": "tv", "AppName": "Moonfin for Android TV", "LastUserId": uid, "DateLastActivity": self.iso(-3600)},
                        {"Id": self.DEVICE, "AppName": "Moonfin for Android", "LastUserId": uid, "DateLastActivity": self.iso(-40, digits=7)},
                        {"Id": "web", "AppName": "Jellyfin Web", "LastUserId": USERS["b" * 32]["Id"], "DateLastActivity": self.iso(-1)}]
        # the server copy has German at index 1, the phone version at index 3 (Godzilla Minus One, 2026-11-22)
        self.sources = [{"Id": self.ITEM, "MediaStreams": [{"Type": "Video", "Index": 0}, {"Type": "Audio", "Index": 1, "Language": "deu"}, {"Type": "Audio", "Index": 2, "Language": "jpn"}]},
                        {"Id": self.PHONE, "MediaStreams": [{"Type": "Video", "Index": 0}, {"Type": "Audio", "Index": 1, "Language": "jpn"}, {"Type": "Audio", "Index": 3, "Language": "deu"}]}]

        def admin(method, path, body=None):
            self.calls.append((method, path, body))
            if method == "GET" and path.startswith("/Sessions"):
                return self.sessions
            if method == "GET" and path.startswith("/Devices"):
                return {"Items": [d for d in self.devices if d["LastUserId"] == urllib.parse.parse_qs(path.partition("?")[2]).get("userId", [None])[0]]}
            if method == "GET" and path.startswith("/DisplayPreferences"):
                return {"CustomPrefs": {"language": self.language}} if self.language else {}
            if method == "GET" and path.startswith("/Users"):
                return [{"Id": "admin-id", "Policy": {"IsAdministrator": True}}]
            if method == "GET" and path.startswith("/Items?"):
                return {"Items": [{"Id": self.ITEM, "Type": "Episode", "MediaStreams": [{"Type": "Video", "Height": self.height, "Width": 3840 if self.height > 1080 else 1920}],
                                   "MediaSources": self.sources}]}
            return None

        self.core.jellyfin_admin = admin
        self.core.spawn = lambda fn: fn()

    def iso(self, seconds, digits=6):
        t = (self.now + datetime.timedelta(seconds=seconds)).astimezone(datetime.timezone.utc)
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + ("%07d" % (t.microsecond * 10) if digits == 7 else "%06d" % t.microsecond) + "Z"

    def state(self, **item):
        (self.box / "state.json").write_text(json.dumps({"items": {self.ITEM: item}}))

    def bypass(self, item=ITEM, token=TOKEN):
        return f"/Items/{item}/Download?ApiKey={token}&hssPhone=1"

    def messages(self):
        return [b for m, p, b in self.calls if m == "POST" and p.endswith("/Message")]

    def requests(self):
        return [json.loads(f.read_text()) for f in sorted((self.box / "requests").glob("*.json"))]

    TV_DEVICE = "tv-device-0001"

    def tv_auth(self, client, device="Sony BRAVIA", header="Authorization"):
        return {header: f'MediaBrowser Client="{client}", Device="{device}", DeviceId="{self.TV_DEVICE}", Version="1"'}

    def tv_session(self):
        self.sessions = [{"Id": "sess-tv", "DeviceId": self.TV_DEVICE, "UserId": USERS[TOKEN]["Id"]}]


class PhoneDownload(PhoneBase):
    """Original downloads (/Items/<id>/Download and /File, path unchanged by Traefik): the full original file for every client; only a TV is refused (owner 2026-11-22).
    Smaller qualities get the phone version: ConvertedDownload."""

    def hit(self, token=TOKEN, ip=PhoneBase.IP, item=PhoneBase.ITEM, extra="", headers=None):
        q = f"ApiKey={token}{extra}" if token else extra.lstrip("&")
        h = dict({"X-Real-Ip": ip} if ip else {}, **(headers or {}))
        return self.core.handle("GET", f"/Items/{item}/Download?{q}", h, b"")

    def test_a_phone_gets_the_original_even_when_a_phone_version_is_ready(self):
        self.state(state="ready", phoneItem=self.PHONE)
        self.assertEqual(self.hit(), (302, {"location": self.bypass()}))
        self.assertFalse((self.box / "sent.jsonl").exists())
        self.assertEqual(self.messages(), [])

    def test_a_phone_original_download_of_a_4k_title_asks_for_nothing(self):
        self.assertEqual(self.hit(), (302, {"location": self.bypass()}))
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.messages(), [])

    def test_a_phone_original_download_while_preparing_still_gets_the_original(self):
        self.state(state="downloading", percent=42)
        self.assertEqual(self.hit(), (302, {"location": self.bypass()}))
        self.assertEqual(self.messages(), [])

    def test_a_named_phone_client_gets_the_original(self):
        auth = {"X-Emby-Authorization": 'MediaBrowser Client="Moonfin for Android", Device="Pixel", DeviceId="x", Version="1"'}
        self.assertEqual(self.hit(headers=auth), (302, {"location": self.bypass()}))

    def test_a_browser_or_desktop_gets_the_original(self):
        uid = USERS[TOKEN]["Id"]
        self.devices.append({"Id": "chrome", "AppName": "Jellyfin Web", "LastUserId": uid, "DateLastActivity": self.iso(-2)})
        self.assertEqual(self.hit(), (302, {"location": self.bypass()}))
        auth = {"Authorization": 'MediaBrowser Client="Jellyfin Media Player", Device="PC", DeviceId="pc", Version="1"'}
        self.assertEqual(self.hit(headers=auth), (302, {"location": self.bypass()}))

    # --- TV clients: Original download refused with a pop-up (Jellyfin README "TV downloads off") ---
    def test_tv_clients_named_in_the_header_are_refused_with_a_pop_up(self):
        self.tv_session()
        for client, device in (("Moonfin for Android TV", "Sony BRAVIA 4K GB ATV3"), ("Moonfin for tvOS", "Apple TV"), ("Moonfin for Tizen", "Samsung Smart TV"),
                               ("Jellyfin Android TV", "KD-65XF9005"), ("Swiftfin tvOS", "AppleTV"), ("Jellyfin for Tizen", "Samsung Smart TV"), ("Jellyfin for WebOS", "LG Smart TV"),
                               ("Jellyfin Web", "Xbox One"), ("Jellyfin Web", "Samsung Smart TV")):
            self.core.notified.clear()
            self.calls.clear()
            code, body = self.hit(headers=self.tv_auth(client, device))
            self.assertEqual(code, 503, client)
            self.assertIn("TV", body["error"])
            self.assertEqual(self.requests(), [], client)
            [m] = self.messages()
            self.assertEqual((m["Header"], m["Text"]), (app.TV_HEADER, app.TV_TEXT["en"]), client)

    def test_the_tv_pop_up_goes_to_the_callers_session_on_the_header_device(self):
        self.sessions = [{"Id": "sess-tv", "DeviceId": self.TV_DEVICE, "UserId": USERS[TOKEN]["Id"]},
                         {"Id": "sess-other-user", "DeviceId": self.TV_DEVICE, "UserId": USERS["b" * 32]["Id"]},
                         {"Id": "sess-phone", "DeviceId": self.DEVICE, "UserId": USERS[TOKEN]["Id"]}]
        self.hit(headers=self.tv_auth("Moonfin for Android TV", header="X-Emby-Authorization"))
        self.assertEqual([p for m, p, b in self.calls if m == "POST"], ["/Sessions/sess-tv/Message"])

    def test_the_tv_refusal_covers_file_too_and_never_asks_for_a_phone_version(self):
        self.tv_session()
        code, _ = self.core.handle("GET", f"/Items/{self.ITEM}/File?ApiKey={TOKEN}", self.tv_auth("Swiftfin tvOS"), b"")
        self.assertEqual(code, 503)
        self.assertEqual(self.requests(), [])
        self.assertFalse((self.box / "sent.jsonl").exists())

    def test_the_tv_pop_up_is_german_for_a_german_display_language(self):
        self.tv_session()
        self.language = "de-DE"
        self.hit(headers=self.tv_auth("Moonfin for Android TV"))
        self.assertEqual(self.messages()[0]["Text"], app.TV_TEXT["de"])
        self.assertNotEqual(app.TV_TEXT["de"], app.TV_TEXT["en"])

    def test_the_tv_pop_up_is_english_when_the_language_is_unreadable(self):
        self.tv_session()
        for lang in (None, "fr-FR", "en-US"):
            self.core.notified.clear()
            self.calls.clear()
            self.language = lang
            self.hit(headers=self.tv_auth("Moonfin for Android TV"))
            self.assertEqual(self.messages()[0]["Text"], app.TV_TEXT["en"], lang)

    def test_one_tv_pop_up_per_device_within_two_minutes(self):
        self.tv_session()
        for _ in range(4):
            self.assertEqual(self.hit(headers=self.tv_auth("Moonfin for Android TV"))[0], 503)
        self.assertEqual(len(self.messages()), 1)
        self.now = NOW + datetime.timedelta(seconds=121)
        self.hit(headers=self.tv_auth("Moonfin for Android TV"))
        self.assertEqual(len(self.messages()), 2)

    def test_a_tv_without_a_live_session_is_refused_without_a_pop_up(self):
        self.sessions = []
        self.assertEqual(self.hit(headers=self.tv_auth("Moonfin for Android TV"))[0], 503)
        self.assertEqual(self.messages(), [])

    def test_a_tv_with_a_bad_token_is_refused_without_a_pop_up(self):
        self.tv_session()
        self.assertEqual(self.hit(token="z" * 32, headers=self.tv_auth("Moonfin for Android TV"))[0], 503)
        self.assertEqual(self.messages(), [])

    def test_an_unnamed_downloader_on_a_tv_is_refused_by_the_latest_device(self):
        # Moonfin's downloader sends no Client name: the user's latest active device decides, as for phones
        uid = USERS[TOKEN]["Id"]
        self.devices = [{"Id": self.TV_DEVICE, "AppName": "Moonfin for Android TV", "Name": "Sony BRAVIA", "LastUserId": uid, "DateLastActivity": self.iso(-20)},
                        {"Id": self.DEVICE, "AppName": "Moonfin for Android", "LastUserId": uid, "DateLastActivity": self.iso(-600)}]
        self.tv_session()
        self.assertEqual(self.hit()[0], 503)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.messages()[0]["Text"], app.TV_TEXT["en"])
        self.assertEqual([p for m, p, b in self.calls if m == "POST"], ["/Sessions/sess-tv/Message"])

    def test_an_unnamed_downloader_whose_latest_device_is_a_web_tv_is_refused(self):
        uid = USERS[TOKEN]["Id"]
        self.devices = [{"Id": self.TV_DEVICE, "AppName": "Jellyfin Web", "Name": "Samsung Smart TV", "LastUserId": uid, "DateLastActivity": self.iso(-20)}]
        self.tv_session()
        self.assertEqual(self.hit()[0], 503)

    def test_an_old_tv_session_does_not_block_a_phone_download(self):
        self.devices[0].update({"AppName": "Moonfin for Android TV", "DateLastActivity": self.iso(-3600)})
        self.assertEqual(self.hit(), (302, {"location": self.bypass()}))

    def test_a_phone_used_after_the_tv_gets_the_original(self):
        self.devices[0].update({"AppName": "Moonfin for Android TV", "DateLastActivity": self.iso(-120)})
        self.assertEqual(self.hit(), (302, {"location": self.bypass()}))

    def test_a_streaming_request_is_not_touched(self):
        self.assertEqual(self.core.handle("GET", f"/Videos/{self.ITEM}/stream?Static=true&ApiKey={TOKEN}", self.tv_auth("Moonfin for Android TV"), b"")[0], 404)

    # --- plumbing ---
    def test_bad_or_missing_token_goes_to_jellyfin_which_refuses_it(self):
        self.assertEqual(self.hit(token="z" * 32)[1], {"location": self.bypass(token="z" * 32)})
        self.assertEqual(self.hit(token=None), (302, {"location": f"/Items/{self.ITEM}/Download?hssPhone=1"}))

    def test_other_query_parameters_are_kept(self):
        self.assertEqual(self.hit(extra="&mediaSourceId=x")[1], {"location": f"/Items/{self.ITEM}/Download?ApiKey={TOKEN}&mediaSourceId=x&hssPhone=1"})

    def test_bad_item_id_is_refused(self):
        self.assertEqual(self.hit(item="../x")[0], 404)

    def test_the_file_endpoint_keeps_the_endpoint(self):
        self.state(state="ready", phoneItem=self.PHONE)
        got = self.core.handle("GET", f"/Items/{self.ITEM}/File?MediaSourceId={self.ITEM}&ApiKey={TOKEN}", {}, b"")
        self.assertEqual(got, (302, {"location": f"/Items/{self.ITEM}/File?MediaSourceId={self.ITEM}&ApiKey={TOKEN}&hssPhone=1"}))

    def test_emby_prefix_and_any_case(self):
        got = self.core.handle("GET", f"/emby/items/{self.ITEM}/download?ApiKey={TOKEN}", {"X-Real-Ip": self.IP}, b"")
        self.assertEqual(got, (302, {"location": self.bypass()}))

    def test_jellyfin_down_downloads_the_original(self):
        def down(method, path, body=None):
            raise OSError("down")
        self.core.jellyfin_admin = down
        self.assertEqual(self.hit(), (302, {"location": self.bypass()}))

    def test_redirect_is_sent_as_a_location_header(self):
        app.Handler.core = self.core
        h = app.Handler.__new__(app.Handler)
        sent = {}
        h.headers, h.path, h.rfile, h.wfile = {}, f"/Items/{self.ITEM}/Download?ApiKey={TOKEN}", io.BytesIO(), io.BytesIO()
        h.headers = {"X-Real-Ip": self.IP}
        h.send_response = lambda code: sent.setdefault("code", code)
        h.send_header = lambda k, v: sent.setdefault(k, v)
        h.end_headers = lambda: None
        h._go("GET")
        self.assertEqual((sent["code"], sent["Location"]), (302, self.bypass()))


class ConvertedDownload(PhoneBase):
    """Smaller-quality downloads: Moonfin's High/Medium/Low/Mobile is GET /Videos/<id>/stream?Static=false&videoCodec=hevc&videoBitRate=…&maxWidth=…&container=mp4&…
    (Moonfin-Core download_service.dart _buildDownloadUrl, Traefik access log 2026-11-20); fastpi's router jellyfin-converted-download-block rewrites it to
    /hss-popular/converted/<id>, query unchanged. A phone gets the 1080p phone version, remuxed by Jellyfin to the MP4 the app expects (owner 2026-11-22)."""
    PLAY = "84f05907-8109-4bb2-95ab-73e3cc3045e3"

    def moonfin(self, token=TOKEN, audio="1", extra=""):
        a = f"&AudioStreamIndex={audio}" if audio is not None else ""
        return (f"MediaSourceId={self.ITEM}&ApiKey={token}&Static=false&videoCodec=hevc&audioCodec=aac&videoBitRate=4000000&audioBitRate=192000&maxWidth=1920"
                f"&container=mp4&audioChannels=2{a}&PlaySessionId={self.PLAY}&DeviceId={self.DEVICE}{extra}")

    def hit(self, query=None, item=PhoneBase.ITEM, headers=None):
        return self.core.handle("GET", f"/hss-popular/converted/{item}?{self.moonfin() if query is None else query}", dict(headers or {}), b"")

    def remux(self, target, audio="3", token=TOKEN):
        a = f"&AudioStreamIndex={audio}" if audio is not None else ""
        return (f"/Videos/{target}/stream?MediaSourceId={target}&ApiKey={token}&audioCodec=aac&audioBitRate=192000&container=mp4&audioChannels=2"
                f"&PlaySessionId={self.PLAY}&DeviceId={self.DEVICE}&Static=false&videoCodec=hevc%2Ch264{a}&hssPhone=1")

    # --- phone, phone version ready ---
    def test_a_ready_phone_version_is_sent_as_an_mp4_remux_without_the_quality_limits(self):
        self.state(state="ready", phoneItem=self.PHONE)
        self.assertEqual(self.hit(), (302, {"location": self.remux(self.PHONE)}))

    def test_every_smaller_quality_gets_the_same_phone_version(self):
        self.state(state="ready", phoneItem=self.PHONE)
        for rate, width in (("4000000", "1920"), ("2000000", "1280"), ("1000000", "854"), ("500000", "640")):
            q = self.moonfin().replace("videoBitRate=4000000", f"videoBitRate={rate}").replace("maxWidth=1920", f"maxWidth={width}")
            self.assertEqual(self.hit(q), (302, {"location": self.remux(self.PHONE)}), rate)

    def test_the_audio_language_follows_to_the_phone_version(self):
        self.state(state="ready", phoneItem=self.PHONE)
        self.assertEqual(self.hit(self.moonfin(audio="2")), (302, {"location": self.remux(self.PHONE, audio="1")}))

    def test_an_audio_language_the_phone_version_lacks_is_left_to_jellyfin(self):
        self.state(state="ready", phoneItem=self.PHONE)
        self.sources[0]["MediaStreams"].append({"Type": "Audio", "Index": 5, "Language": "eng"})
        self.assertEqual(self.hit(self.moonfin(audio="5")), (302, {"location": self.remux(self.PHONE, audio=None)}))
        self.assertEqual(self.hit(self.moonfin(audio=None)), (302, {"location": self.remux(self.PHONE, audio=None)}))

    def test_unreadable_audio_streams_leave_the_audio_to_jellyfin(self):
        self.state(state="ready", phoneItem=self.PHONE)
        self.sources = []
        self.assertEqual(self.hit(), (302, {"location": self.remux(self.PHONE, audio=None)}))

    def test_a_sent_phone_version_is_recorded_per_user_and_device(self):
        self.state(state="ready", phoneItem=self.PHONE)
        self.hit()
        [rec] = [json.loads(l) for l in (self.box / "sent.jsonl").read_text().splitlines()]
        self.assertEqual(rec, {"at": self.now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "user": USERS[TOKEN]["Id"], "device": self.DEVICE,
                               "item": self.ITEM, "phoneItem": self.PHONE})

    def test_the_phone_downloader_user_agent_does_not_matter(self):
        self.state(state="ready", phoneItem=self.PHONE)
        ua = {"User-Agent": "Mozilla/5.0 (compatible; Moonfin/2.9.0)"}
        self.assertEqual(self.hit(headers=ua)[1], {"location": self.remux(self.PHONE)})

    def test_any_case_of_the_query_keys(self):
        self.state(state="ready", phoneItem=self.PHONE)
        q = f"api_key={TOKEN}&static=false&VIDEOBITRATE=1000000&MaxWidth=854&audiocodec=aac&deviceid={self.DEVICE}"
        self.assertEqual(self.hit(q), (302, {"location": f"/Videos/{self.PHONE}/stream?MediaSourceId={self.PHONE}&api_key={TOKEN}&audiocodec=aac&deviceid={self.DEVICE}"
                                                         "&container=mp4&Static=false&videoCodec=hevc%2Ch264&hssPhone=1"}))

    # --- phone, no phone version yet ---
    def test_first_smaller_download_starts_the_phone_version_with_a_pop_up(self):
        code, body = self.hit()
        self.assertEqual((code, body["error"]), (503, "phone version being prepared"))
        self.assertEqual(self.requests(), [{"item": self.ITEM, "user": USERS[TOKEN]["Id"], "device": self.DEVICE}])
        [m] = self.messages()
        self.assertEqual((m["Header"], m["Text"]), (app.PHONE_HEADER, app.PHONE_TEXT_NEW))

    def test_one_request_file_per_item_while_waiting(self):
        self.hit()
        self.hit()
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(len(self.messages()), 1)

    def test_a_tap_while_preparing_shows_progress_once_per_device_within_two_minutes(self):
        self.state(state="downloading", percent=42)
        for _ in range(4):
            self.assertEqual(self.hit()[0], 503)
        self.assertEqual(self.requests(), [])
        [m] = self.messages()
        self.assertIn("42 % downloaded", m["Text"])
        self.now = NOW + datetime.timedelta(seconds=121)
        self.hit()
        self.assertEqual(len(self.messages()), 2)

    def test_a_tap_while_converting_shows_the_conversion(self):
        for st, pct, want in (("converting", 37, "37 % converted"), ("queued", 0, "conversion starts soon")):
            self.core.notified.clear()
            self.state(state=st, percent=pct)
            self.assertEqual(self.hit()[0], 503, st)
            self.assertIn(want, self.messages()[-1]["Text"])
        self.assertEqual(self.requests(), [])

    def test_phone_without_a_live_session_still_starts_the_phone_version(self):
        self.sessions = []
        self.assertEqual(self.hit()[0], 503)
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(self.messages(), [])

    def test_the_pop_ups_say_to_pick_a_smaller_quality_again(self):
        for text in (app.PHONE_TEXT_NEW, app.PHONE_TEXT_WAIT):
            self.assertIn("smaller quality", text)
            self.assertNotIn("Original", text)

    # --- phone, no separate phone version: the title's own picture ---
    def test_not_4k_gets_its_own_file_remuxed_with_its_own_audio_index(self):
        self.height = 1080
        self.assertEqual(self.hit(), (302, {"location": self.remux(self.ITEM, audio="1")}))
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.messages(), [])

    def test_nothing_smaller_not_needed_or_failed_gets_its_own_file(self):
        for st in ("nothing", "not-needed", "failed"):
            self.state(state=st)
            self.assertEqual(self.hit(), (302, {"location": self.remux(self.ITEM, audio="1")}), st)
        self.assertEqual(self.requests(), [])

    def test_jellyfin_errors_after_the_phone_check_get_its_own_file(self):
        def flaky(method, path, body=None):
            if path.startswith("/Items?"):
                raise OSError("down")
            return admin(method, path, body)
        admin = self.core.jellyfin_admin
        self.core.jellyfin_admin = flaky
        self.assertEqual(self.hit(), (302, {"location": self.remux(self.ITEM, audio="1")}))

    def test_phone_versions_off_gets_its_own_file(self):
        self.core.cfg.phone_box = None
        self.assertEqual(self.hit(), (302, {"location": self.remux(self.ITEM, audio="1")}))

    # --- TV: refused as an Original download is ---
    def test_a_tv_is_refused_with_the_tv_pop_up(self):
        self.tv_session()
        code, body = self.hit(headers=self.tv_auth("Moonfin for Android TV"))
        self.assertEqual(code, 503)
        self.assertIn("TV", body["error"])
        self.assertEqual(self.messages()[0]["Text"], app.TV_TEXT["en"])
        self.assertEqual(self.requests(), [])

    def test_an_unnamed_downloader_on_a_tv_is_refused_by_the_latest_device(self):
        uid = USERS[TOKEN]["Id"]
        self.devices = [{"Id": self.TV_DEVICE, "AppName": "Moonfin for Android TV", "Name": "Sony BRAVIA", "LastUserId": uid, "DateLastActivity": self.iso(-20)}]
        self.tv_session()
        self.assertEqual(self.hit()[0], 503)
        self.assertEqual(self.messages()[0]["Text"], app.TV_TEXT["en"])

    # --- Moonfin desktop: the same mapping as a phone (owner 2026-11-22) ---
    def test_moonfin_desktop_gets_the_ready_phone_version(self):
        self.state(state="ready", phoneItem=self.PHONE)
        for name in ("Moonfin for Windows", "Moonfin for macOS", "Moonfin for Linux"):
            self.devices[1].update(AppName=name, Name="SurfaceLap6A Windows 11 Pro")
            self.assertEqual(self.hit(), (302, {"location": self.remux(self.PHONE)}), name)

    def test_moonfin_desktop_starts_a_missing_phone_version_with_a_pop_up(self):
        self.devices[1]["AppName"] = "Moonfin for Windows"
        self.assertEqual(self.hit()[0], 503)
        self.assertEqual(self.requests(), [{"item": self.ITEM, "user": USERS[TOKEN]["Id"], "device": self.DEVICE}])
        self.assertEqual([m["Text"] for m in self.messages()], [app.PHONE_TEXT_NEW])

    def test_moonfin_desktop_on_a_title_that_is_not_4k_gets_its_own_file(self):
        self.devices[1]["AppName"] = "Moonfin for Linux"
        self.height = 1080
        self.assertEqual(self.hit(), (302, {"location": self.remux(self.ITEM, audio="1")}))

    # --- everything else: unchanged, refused with the "choose Original" pop-up ---
    def test_an_unknown_app_is_refused_as_before(self):
        self.devices[1]["AppName"] = "Moonfin for Web"
        code, body = self.hit()
        self.assertEqual(code, 403)
        self.assertIn("Original", body["error"])
        [m] = self.messages()
        self.assertEqual((m["Header"], m["Text"]), (app.DOWNLOAD_HEADER, app.DOWNLOAD_TEXT))
        self.assertEqual(self.requests(), [])

    def test_a_named_client_that_is_neither_phone_nor_desktop_is_refused_as_before(self):
        for client, device in (("Kodi", "PC"), ("Jellyfin Web", "Chrome Android")):
            auth = {"Authorization": f'MediaBrowser Client="{client}", Device="{device}", DeviceId="web", Version="1"'}
            self.assertEqual(self.hit(headers=auth)[0], 403, client)

    def test_no_device_active_lately_is_refused_as_before(self):
        self.devices[1]["DateLastActivity"] = self.iso(-3600)
        self.assertEqual(self.hit()[0], 403)

    def test_bad_token_or_bad_item_is_refused_without_a_pop_up(self):
        for token in ("z" * 32, "c" * 32):
            self.assertEqual(self.hit(self.moonfin(token=token))[0], 403, token)
        self.assertEqual(self.hit(item="not-an-item-id")[0], 403)
        self.assertEqual(self.hit(item="../../x")[0], 404)
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.requests(), [])

    def test_jellyfin_down_is_refused(self):
        def down(method, path, body=None):
            raise OSError("down")
        self.core.jellyfin_admin = down
        self.assertEqual(self.hit()[0], 403)

    def test_the_old_blocked_path_is_unchanged(self):
        self.state(state="ready", phoneItem=self.PHONE)
        self.assertEqual(self.core.handle("GET", f"/hss-popular/download-blocked?{self.moonfin()}", {}, b"")[0], 403)

    def test_query_is_not_logged(self):
        h = app.Handler.__new__(app.Handler)
        path = f"/hss-popular/converted/{self.ITEM}?{self.moonfin()}"
        h.command, h.path, h.requestline = "GET", path, f"GET {path} HTTP/1.1"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            h.log_request(302)
        self.assertIn("/hss-popular/converted/", out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue())


class DownloadChoice(PhoneBase):
    """Desktop web client and Jellyfin Media Player (owner 2026-11-22): their Download has no quality choice, so the injector entry "Download choice" asks
    POST /hss-popular/download-choice {"items": [...]} first and shows full file / phone version; picking the phone version posts {"items", "pick": "phone"}."""
    MOVIE = "6368815bff4f9df65ec668a9c397cc6e"
    SMALL = "1111111111111111111111111111111a"
    WEB = 'MediaBrowser Client="Jellyfin Web", Device="Chrome", DeviceId="web-device-0001", Version="12.4.0", Token="' + TOKEN + '"'

    def setUp(self):
        super().setUp()
        self.items = {
            self.ITEM: {"Id": self.ITEM, "Name": "Money Heist S02E01", "Type": "Episode", "MediaStreams": [{"Type": "Video", "Width": 3840, "Height": 2160, "VideoRange": "HDR"}],
                        "MediaSources": [{"Id": self.ITEM, "Size": 29363263741, "MediaStreams": [{"Type": "Video", "Width": 3840, "Height": 1608, "VideoRange": "HDR"}]},
                                         {"Id": self.PHONE, "Size": 4384001056, "MediaStreams": [{"Type": "Video", "Width": 1920, "Height": 1080, "VideoRange": "HDR"}]}]},
            self.SMALL: {"Id": self.SMALL, "Name": "Nosferatu", "Type": "Movie", "MediaStreams": [{"Type": "Video", "Width": 1920, "Height": 1080, "VideoRange": "SDR"}],
                         "MediaSources": [{"Id": self.SMALL, "Size": 2100000000, "MediaStreams": [{"Type": "Video", "Width": 1920, "Height": 1080, "VideoRange": "SDR"}]}]},
            self.MOVIE: {"Id": self.MOVIE, "Name": "Godzilla Minus One", "Type": "Movie", "MediaStreams": [{"Type": "Video", "Width": 3840, "Height": 1608}],
                         "MediaSources": [{"Id": self.MOVIE, "Size": 30000000000, "MediaStreams": [{"Type": "Video", "Width": 3840, "Height": 1608}]}]},
        }
        self.states = {}
        old = self.core.jellyfin_admin

        def admin(method, path, body=None):
            if method == "GET" and path.startswith("/Items?"):
                self.calls.append((method, path, body))
                q = urllib.parse.parse_qs(path.partition("?")[2])
                return {"Items": [self.items[i] for i in q["ids"][0].split(",") if i in self.items]}
            return old(method, path, body)

        self.core.jellyfin_admin = admin

    def state(self, item=PhoneBase.ITEM, **st):
        self.states[item] = st
        (self.box / "state.json").write_text(json.dumps({"items": self.states}))

    def post(self, body, auth=None, origin=ORIGIN, ctype="application/json", token_header=False):
        h = {"Content-Type": ctype, "Authorization": self.WEB if auth is None else auth}
        if origin:
            h["Origin"] = origin
        if token_header:
            h = {"Content-Type": ctype, "Origin": origin, "X-Emby-Token": TOKEN}
        return self.core.handle("POST", "/hss-popular/download-choice", h, body if isinstance(body, bytes) else json.dumps(body).encode())

    def client(self, name, device):
        return f'MediaBrowser Client="{name}", Device="{device}", DeviceId="pc-0001", Version="1", Token="{TOKEN}"'

    # --- the offer ---
    def test_a_ready_phone_version_is_offered_with_sizes_and_labels(self):
        self.state(state="ready", phoneItem=self.PHONE)
        code, body = self.post({"items": [self.ITEM]})
        self.assertEqual(code, 200)
        self.assertEqual(body, {"choice": True, "items": {self.ITEM: {"name": "Money Heist S02E01", "full": {"size": 29363263741, "label": "4K HDR"},
                                                                      "phone": {"state": "ready", "size": 4384001056, "label": "1080p HDR"}}}})

    def test_a_4k_title_without_a_phone_version_offers_one_on_request(self):
        code, body = self.post({"items": [self.MOVIE]})
        self.assertEqual(body["items"][self.MOVIE]["phone"], {"state": "order"})
        self.assertEqual(body["items"][self.MOVIE]["full"], {"size": 30000000000, "label": "4K"})
        self.assertEqual(self.requests(), [], "an offer orders nothing")

    def test_a_phone_version_being_prepared_shows_its_progress(self):
        self.state(state="downloading", percent=42)
        self.assertEqual(self.post({"items": [self.ITEM]})[1]["items"][self.ITEM]["phone"], {"state": "preparing", "what": "42 % downloaded"})

    def test_no_choice_when_no_title_can_have_a_phone_version(self):
        self.assertEqual(self.post({"items": [self.SMALL]}), (200, {"choice": False}))
        for st in ("nothing", "failed", "not-needed"):
            self.state(item=self.MOVIE, state=st)
            self.assertEqual(self.post({"items": [self.MOVIE]}), (200, {"choice": False}), st)

    def test_a_ready_state_whose_version_is_gone_offers_none(self):
        self.state(state="ready", phoneItem="ffffffffffffffffffffffffffffffff")
        self.assertEqual(self.post({"items": [self.ITEM]}), (200, {"choice": False}))

    def test_a_batch_offers_each_title(self):
        self.state(state="ready", phoneItem=self.PHONE)
        code, body = self.post({"items": [self.ITEM, self.SMALL, self.MOVIE]})
        self.assertEqual(code, 200)
        self.assertEqual([body["items"][i]["phone"] for i in (self.ITEM, self.SMALL, self.MOVIE)],
                         [{"state": "ready", "size": 4384001056, "label": "1080p HDR"}, None, {"state": "order"}])

    def test_items_are_read_as_the_caller_so_only_their_titles_count(self):
        self.post({"items": [self.ITEM]})
        [path] = [p for m, p, b in self.calls if p.startswith("/Items?")]
        self.assertIn("userId=" + USERS[TOKEN]["Id"].replace("-", ""), path)
        self.assertEqual(self.post({"items": ["22222222222222222222222222222222"]}), (200, {"choice": False}))

    def test_the_web_clients_encoded_header_is_read(self):
        # jellyfin-web's API client URL-encodes the client and device names (seen live 2026-11-22: Client="Jellyfin%20Web")
        self.state(state="ready", phoneItem=self.PHONE)
        self.assertTrue(self.post({"items": [self.ITEM]}, auth=self.client("Jellyfin%20Web", "Edge%20Chromium"))[1]["choice"])
        self.assertEqual(self.post({"items": [self.ITEM]}, auth=self.client("Jellyfin%20Web", "Chrome%20Android")), (200, {"choice": False}))

    def test_only_desktop_clients_get_a_choice(self):
        self.state(state="ready", phoneItem=self.PHONE)
        for name, device in (("Jellyfin Web", "Firefox"), ("Jellyfin Web", "Edge%20Chromium"), ("Jellyfin Media Player", "PC"), ("Jellyfin Desktop", "PC"), ("Moonfin for Windows", "PC")):
            self.assertTrue(self.post({"items": [self.ITEM]}, auth=self.client(name, device))[1]["choice"], name + device)
        for name, device in (("Jellyfin Web", "Chrome Android"), ("Jellyfin Web", "Safari iPhone"), ("Jellyfin Web", "Samsung Smart TV"), ("Moonfin for Android", "Pixel"), ("Kodi", "PC")):
            self.assertEqual(self.post({"items": [self.ITEM]}, auth=self.client(name, device)), (200, {"choice": False}), name + device)
        self.assertEqual(self.post({"items": [self.ITEM]}, token_header=True), (200, {"choice": False}), "no client name")

    # --- picking the phone version ---
    def test_picking_a_ready_phone_version_returns_its_file_and_records_it(self):
        self.state(state="ready", phoneItem=self.PHONE)
        code, body = self.post({"items": [self.ITEM], "pick": "phone"})
        self.assertEqual((code, body), (200, {"downloads": [{"item": self.ITEM, "file": self.PHONE}], "preparing": [], "ordered": []}))
        [rec] = [json.loads(l) for l in (self.box / "sent.jsonl").read_text().splitlines()]
        self.assertEqual((rec["user"], rec["device"], rec["item"], rec["phoneItem"]), (USERS[TOKEN]["Id"].replace("-", ""), "web-device-0001", self.ITEM, self.PHONE))
        self.assertEqual(self.messages(), [])

    def test_picking_a_missing_phone_version_orders_it_like_a_phone_does(self):
        code, body = self.post({"items": [self.MOVIE], "pick": "phone"})
        self.assertEqual((code, body), (200, {"downloads": [], "preparing": [], "ordered": [self.MOVIE]}))
        self.assertEqual(self.requests(), [{"item": self.MOVIE, "user": USERS[TOKEN]["Id"].replace("-", ""), "device": "web-device-0001"}])
        self.post({"items": [self.MOVIE], "pick": "phone"})
        self.assertEqual(len(self.requests()), 1, "one request per item per 5 min")
        self.assertFalse((self.box / "sent.jsonl").exists())

    def test_picking_a_phone_version_being_prepared_orders_nothing(self):
        self.state(state="converting", percent=37)
        self.assertEqual(self.post({"items": [self.ITEM], "pick": "phone"})[1], {"downloads": [], "preparing": [{"item": self.ITEM, "what": "37 % converted"}], "ordered": []})
        self.assertEqual(self.requests(), [])

    def test_a_batch_pick_gets_each_title_its_best_file(self):
        self.state(state="ready", phoneItem=self.PHONE)
        body = self.post({"items": [self.ITEM, self.SMALL, self.MOVIE], "pick": "phone"})[1]
        self.assertEqual(body, {"downloads": [{"item": self.ITEM, "file": self.PHONE}, {"item": self.SMALL, "file": self.SMALL}], "preparing": [], "ordered": [self.MOVIE]})

    def test_a_pick_from_a_non_desktop_client_does_nothing(self):
        self.assertEqual(self.post({"items": [self.MOVIE], "pick": "phone"}, auth=self.client("Jellyfin Web", "Samsung Smart TV")), (200, {"choice": False}))
        self.assertEqual(self.requests(), [])

    def test_a_bad_device_id_still_orders_without_one(self):
        auth = self.WEB.replace('DeviceId="web-device-0001"', 'DeviceId="bad id/../"')
        self.post({"items": [self.MOVIE], "pick": "phone"}, auth=auth)
        self.assertEqual(self.requests()[0]["device"], None)

    # --- refusals ---
    def test_refusals(self):
        self.assertEqual(self.post({"items": [self.ITEM]}, auth='MediaBrowser Client="Jellyfin Web", Device="Chrome"')[0], 401)
        self.assertEqual(self.post({"items": [self.ITEM]}, auth=self.WEB.replace(TOKEN, "c" * 32))[0], 403)
        self.assertEqual(self.post({"items": [self.ITEM]}, origin="https://evil.example.com")[0], 403)
        self.assertEqual(self.post({"items": [self.ITEM]}, origin=None)[0], 403)
        self.assertEqual(self.post({"items": [self.ITEM]}, ctype="text/plain")[0], 415)
        self.assertEqual(self.post(b"x" * 4097)[0], 413)
        for bad in ({"items": []}, {"items": self.ITEM}, {"items": ["../x"]}, {"items": [self.ITEM] * 51}, {"items": [self.ITEM], "pick": "full"},
                    {"items": [self.ITEM], "x": 1}, [self.ITEM], {"items": [1]}):
            self.assertEqual(self.post(bad)[0], 400, bad)
        self.assertEqual(self.post(b"{bad")[0], 400)
        self.assertEqual(self.core.handle("GET", "/hss-popular/download-choice", {"Authorization": self.WEB}, b"")[0], 404)
        self.assertEqual(self.requests(), [])

    def test_fifty_items_fit_in_one_call(self):
        ids = ["%032x" % n for n in range(50)]
        self.assertEqual(self.post({"items": ids, "pick": "phone"})[0], 200)

    def test_rate_limited_per_user(self):
        codes = [self.post({"items": [self.SMALL]})[0] for _ in range(app.CHOICE_PER_MIN + 1)]
        self.assertEqual((codes[0], codes[-1]), (200, 429))

    def test_jellyfin_down_is_an_error_the_page_answers_with_the_full_file(self):
        def down(method, path, body=None):
            raise OSError("down")
        self.core.jellyfin_admin = down
        self.assertEqual(self.post({"items": [self.ITEM]})[0], 502)


WSECRET = "w" * 40
PLAYED = [
    {"Id": "m1", "Type": "Movie", "ProviderIds": {"Tmdb": "603"}},
    {"Id": "e1", "Type": "Episode", "SeriesId": "aaaa0000000000000000000000000001", "ProviderIds": {"Tvdb": "9999"}},
    {"Id": "m2", "Type": "Movie", "ProviderIds": {"Imdb": "tt1"}},
    {"Id": "e2", "Type": "Episode", "SeriesId": "bbbb0000000000000000000000000002"},
    {"Id": "e3", "Type": "Episode", "SeriesId": "aaaa0000000000000000000000000001"},
    {"Id": "m3", "Type": "Movie", "ProviderIds": {"Tmdb": "11"}},
    {"Id": "m4", "Type": "Movie", "ProviderIds": {"Tmdb": "603"}},
]
JF_SERIES = {"aaaa0000000000000000000000000001": {"Id": "aaaa0000000000000000000000000001", "ProviderIds": {"Tvdb": "81189"}},
          "bbbb0000000000000000000000000002": {"Id": "bbbb0000000000000000000000000002", "ProviderIds": {"Tmdb": "5"}}}


class Watched(Base):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.jf_users = [{"Id": "40e05f0a-9e96-4fd9-b225-eb1379dfcfa4", "Name": "Jane"}, {"Id": "11111111-2222-3333-4444-555555555555", "Name": "guest"},
                         {"Id": "22222222-2222-3333-4444-555555555555", "Name": "GUEST"}]
        self.core.cfg.watched_secret = WSECRET
        self.core.jellyfin_admin = self.admin

    def admin(self, method, path, body=None):
        self.calls.append((method, path))
        url = urllib.parse.urlsplit(path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if url.path == "/Users":
            return self.jf_users
        if url.path == "/Items" and "ids" in q:
            return {"Items": [JF_SERIES[i] for i in q["ids"].split(",") if i in JF_SERIES]}
        if url.path == "/Items":
            return {"Items": PLAYED}
        raise AssertionError(path)

    def get(self, user="jane", secret=WSECRET, query=None):
        h = {"X-Watched-Secret": secret} if secret is not None else {}
        return self.core.watched(h, query if query is not None else "user=" + urllib.parse.quote(user))

    def test_most_recent_first_unique_ids_by_kind(self):
        code, data = self.get()
        self.assertEqual(code, 200)
        self.assertEqual(data, {"movie": [603, 11], "tv": [81189]})

    def test_asks_jellyfin_for_the_users_played_items_newest_first(self):
        self.get()
        items = [p for (_, p) in self.calls if p.startswith("/Items?") and "ids=" not in p][0]
        for part in ("userId=40e05f0a9e964fd9b225eb1379dfcfa4", "Filters=IsPlayed", "SortBy=DatePlayed", "SortOrder=Descending", "IncludeItemTypes=Movie%2CEpisode", "Fields=ProviderIds"):
            self.assertIn(part, items)

    def test_user_matched_by_name_case_insensitive(self):
        self.assertEqual(self.get("JANE")[0], 200)

    def test_unknown_or_ambiguous_user_is_404_and_asks_nothing_more(self):
        self.assertEqual(self.get("nobody")[0], 404)
        self.assertEqual(self.get("guest")[0], 404)
        self.assertEqual([p for (_, p) in self.calls if p.startswith("/Items")], [])

    def test_bad_secret_or_none_is_403_and_never_reaches_jellyfin(self):
        for s in ("x" * 40, "", None):
            self.assertEqual(self.get(secret=s)[0], 403)
        self.core.cfg.watched_secret = None
        self.assertEqual(self.get()[0], 403)
        self.assertEqual(self.calls, [])

    def test_bad_user_parameter(self):
        for q in ("", "user=", "user=" + "a" * 65, "other=1"):
            self.assertEqual(self.get(query=q)[0], 400, q)

    def test_jellyfin_down_is_502(self):
        def down(method, path, body=None):
            raise OSError("down")
        self.core.jellyfin_admin = down
        self.assertEqual(self.get()[0], 502)

    def test_without_the_jellyfin_key_is_503(self):
        self.core.jellyfin_admin = None
        self.assertEqual(self.get()[0], 503)

    def test_routing_only_on_the_internal_handler(self):
        self.assertEqual(self.core.handle("GET", "/hss-popular/watched?user=jane", {"x-watched-secret": WSECRET}, b"")[0], 404)


class WatchedServer(unittest.TestCase):
    def setUp(self):
        self.seen = []
        app.WatchedHandler.core = type("C", (), {"watched": lambda s, h, q: self.seen.append((h.get("X-Watched-Secret"), q)) or (200, {"movie": [], "tv": []})})()
        self.srv = app.make_watched_server(("127.0.0.1", 0))
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

    def test_get_watched_only_and_the_user_is_never_logged(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            ok = self.send(b"GET /watched?user=jane HTTP/1.0\r\nX-Watched-Secret: s3\r\n\r\n")
            post = self.send(b"POST /watched?user=jane HTTP/1.0\r\nContent-Length: 0\r\n\r\n")
            other = self.send(b"GET /hss-popular/state HTTP/1.0\r\n\r\n")
        self.assertIn(" 200 ", ok.split("\r\n")[0])
        self.assertIn(" 405 ", post.split("\r\n")[0])
        self.assertIn(" 404 ", other.split("\r\n")[0])
        self.assertEqual(self.seen, [("s3", "user=jane")])
        self.assertNotIn("jane", out.getvalue())


if __name__ == "__main__":
    unittest.main()
