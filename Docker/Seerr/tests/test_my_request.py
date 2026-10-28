#!/usr/bin/env python3
"""download-now/my_request.py: the "My request" panel backend — who sees and changes which request, what a change or a delete does to Seerr and Radarr/Sonarr, against fake clients.
Usage: python3 tests/test_my_request.py"""
import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "download-now"))
spec = importlib.util.spec_from_file_location("download_now", ROOT / "download-now" / "download_now.py")
dn = importlib.util.module_from_spec(spec)
sys.modules["download_now"] = dn
spec.loader.exec_module(dn)
import my_request as mr  # noqa: E402

ORIGIN = "https://requests.example.com"
TAGS = [{"id": 13, "label": "audio-swiss-german"}, {"id": 14, "label": "audio-german"}, {"id": 21, "label": "audio-spanish"}, {"id": 20, "label": "subs-spanish"},
        {"id": 1, "label": "lang-es"}, {"id": 63, "label": "old-rare"}, {"id": 64, "label": "download-now"}, {"id": 66, "label": "profile-pinned"}]
PROFILES = [{"id": 7, "name": "4K HDR"}, {"id": 11, "name": "4K preferred · older versions allowed"}, {"id": 13, "name": "Swiss German (Mundart) only - any quality"}]
ME, FRIEND, ADMIN = {"id": 7, "permissions": 8352}, {"id": 5, "permissions": 8352}, {"id": 1, "permissions": 2}


def req(rid=57, uid=7, status=2, tags=(), profile=7, created="2026-10-28T08:41:20.000Z", name="tomigorn", seasons=()):
    return {"id": rid, "status": status, "is4k": False, "serverId": 0, "profileId": profile, "rootFolder": "/data/media/movies", "languageProfileId": None,
            "tags": list(tags), "createdAt": created, "requestedBy": {"id": uid, "displayName": name}, "seasons": [{"seasonNumber": n} for n in seasons]}


def title(tags=(1,), profile=7, added="2026-10-28T08:41:21Z", has_file=False):
    return {"id": 54, "title": "Tied Up", "tmdbId": 673, "tags": list(tags), "qualityProfileId": profile, "added": added, "hasFile": has_file, "rootFolderPath": "/data/media/movies"}


class World:
    def __init__(self, me=ME, requests=None, t=None, kind="movie", tags=None):
        self.me, self.kind, self.tag_list = me, kind, tags if tags is not None else TAGS
        self.requests = requests if requests is not None else [req()]
        self.title = t if t is not None else title()
        self.writes, self.admin_calls = [], []

    def seerr(self, path, auth):
        if path == "/auth/me":
            return self.me
        return {"mediaInfo": {"id": 69, "status": 3, "externalServiceId": 54, "tvdbId": 999, "requests": self.requests}}

    def admin(self, path, method="GET", body=None):
        self.admin_calls.append((method, path, body))
        return None

    def arr(self, app):
        def call(path, method="GET", body=None):
            if method != "GET":
                self.writes.append((app, method, path, body))
                return {"id": 99, "label": body["label"]} if path == "/tag" else {}
            if path == "/tag":
                return self.tag_list
            if path == "/qualityprofile":
                return PROFILES
            if path == "/rootfolder":
                return [{"path": "/data/media/movies"}]
            if path in ("/movie/54", "/series/54"):
                return self.title
            if path.startswith("/queue"):
                return {"records": []}
            if path == "/command":
                return []
            raise dn.ApiError(f"{app} {path}: 404")
        return call

    def deps(self):
        return dn.Deps(seerr=self.seerr, arr=self.arr, qbit=lambda path, data=None: [], sab=lambda p: {"queue": {"slots": []}, "status": True}, origins={ORIGIN}, seerr_admin=self.admin)


def get(w, q="type=movie&tmdb=673"):
    return dn.handle("GET", "/_download-now/request?" + q, {"Cookie": "connect.sid=s%3Aabc"}, b"", w.deps())


def post(w, body, headers=None):
    h = {"Content-Type": "application/json", "Origin": ORIGIN, "Cookie": "connect.sid=s%3Aabc"}
    h.update(headers or {})
    return dn.handle("POST", "/_download-now/request", h, json.dumps({"type": w.kind, "tmdbId": 673, **body}).encode(), w.deps())


class Labels(unittest.TestCase):
    def test_human_labels_for_the_allowed_tags_only(self):
        self.assertEqual(mr.human("audio-swiss-german"), {"label": "audio-swiss-german", "kind": "audio", "name": "Swiss German"})
        self.assertEqual(mr.human("subs-spanish"), {"label": "subs-spanish", "kind": "subs", "name": "Spanish"})
        for t in ("lang-es", "old-rare", "download-now", "audio-", "Audio-x", "subs-es;rm"):
            self.assertFalse(mr.ALLOWED.match(t), t)


class Read(unittest.TestCase):
    def test_own_request_shows_what_was_set(self):
        status, out = get(World(t=title(tags=(1, 13, 20))))
        self.assertEqual(status, 200)
        r = out["requests"][0]
        self.assertEqual((r["id"], r["mine"], r["by"], r["status"], r["canWait"], r["editable"], r["deleteEffect"]), (57, True, None, 2, True, True, "clear"))
        self.assertEqual(r["profile"], {"id": 7, "name": "4K HDR"})
        self.assertEqual([t["label"] for t in r["tags"]], ["audio-swiss-german", "subs-spanish"])
        self.assertEqual(r["auto"], [])
        self.assertNotIn("rootFolder", r)
        self.assertEqual([p["id"] for p in out["profiles"]], [7, 11, 13])
        self.assertEqual([t["label"] for t in out["tags"]], ["audio-german", "audio-spanish", "audio-swiss-german", "subs-spanish"])

    def test_old_rare_is_shown_as_automatic_download_now_as_can_wait_off(self):
        r = get(World(t=title(tags=(63, 64))))[1]["requests"][0]
        self.assertEqual((r["auto"], r["canWait"]), (["old-rare"], False))

    def test_pinned_profile_is_shown_as_automatic(self):
        r = get(World(t=title(tags=(66,))))[1]["requests"][0]
        self.assertEqual(r["auto"], ["profile-pinned"])

    def test_pending_request_reads_the_seerr_request(self):
        r = get(World(requests=[req(status=1, tags=[21, 64], profile=11)], t=title(tags=())))[1]["requests"][0]
        self.assertEqual((r["profile"]["id"], [t["label"] for t in r["tags"]], r["canWait"]), (11, ["audio-spanish"], False))

    def test_friends_title_is_refused_for_a_requester(self):
        self.assertEqual(get(World(requests=[req(uid=5, name="friend")]))[0], 403)
        self.assertEqual(get(World(requests=[req(status=3)]))[0], 403)

    def test_manager_sees_every_request_with_its_requester(self):
        out = get(World(me=ADMIN, requests=[req(), req(rid=60, uid=5, name="friend"), req(rid=61, uid=5, status=3)]))[1]
        self.assertEqual([(r["id"], r["mine"], r["by"], r["editable"]) for r in out["requests"]], [(57, False, "tomigorn", True), (60, False, "friend", True)])

    def test_title_shared_with_a_friend_is_read_only_for_the_requester_and_delete_keeps_the_title(self):
        r = get(World(requests=[req(), req(rid=60, uid=5, name="friend")]))[1]["requests"]
        self.assertEqual(len(r), 1)
        self.assertEqual((r[0]["editable"], r[0]["deleteEffect"]), (False, "request-only"))

    def test_title_in_radarr_before_the_request_is_never_cleared(self):
        r = get(World(t=title(added="2026-01-01T00:00:00Z")))[1]["requests"][0]
        self.assertEqual(r["deleteEffect"], "request-only")

    def test_not_logged_in_is_refused(self):
        w = World()
        self.assertEqual(dn.handle("GET", "/_download-now/request?type=movie&tmdb=673", {}, b"", w.deps())[0], 403)


class Change(unittest.TestCase):
    def test_profile_and_tags_on_an_approved_request_change_the_radarr_title(self):
        w = World(t=title(tags=(1, 13, 20)))
        status, out = post(w, {"action": "update", "requestId": 57, "profileId": 11, "tags": ["audio-spanish", "subs-spanish"]})
        self.assertEqual(status, 200)
        self.assertEqual(w.writes, [("radarr", "PUT", "/movie/editor", {"movieIds": [54], "qualityProfileId": 11}),
                                    ("radarr", "PUT", "/movie/editor", {"movieIds": [54], "tags": [21], "applyTags": "add"}),
                                    ("radarr", "PUT", "/movie/editor", {"movieIds": [54], "tags": [13], "applyTags": "remove"})])
        self.assertEqual(w.admin_calls, [])
        self.assertEqual(out["changed"], ["profile 7 -> 11", "tags +audio-spanish -audio-swiss-german"])

    def test_tags_outside_the_allowed_list_are_refused_and_nothing_is_written(self):
        for tags in (["lang-es"], ["old-rare"], ["download-now"], ["audio-klingon"], "audio-german", [1]):
            w = World()
            self.assertEqual(post(w, {"action": "update", "requestId": 57, "tags": tags})[0], 400, tags)
            self.assertEqual(w.writes, [])

    def test_unknown_profile_is_refused(self):
        w = World()
        self.assertEqual(post(w, {"action": "update", "requestId": 57, "profileId": 99})[0], 400)
        self.assertEqual(w.writes, [])

    def test_can_wait_off_is_download_now_on(self):
        dn.SEARCHED.clear()
        w = World()
        status, out = post(w, {"action": "update", "requestId": 57, "canWait": False})
        self.assertEqual(status, 200)
        self.assertIn(("radarr", "PUT", "/movie/editor", {"movieIds": [54], "tags": [64], "applyTags": "add"}), w.writes)
        self.assertIn(("radarr", "POST", "/command", {"name": "MoviesSearch", "movieIds": [54]}), w.writes)

    def test_can_wait_on_removes_download_now(self):
        w = World(t=title(tags=(64,)))
        post(w, {"action": "update", "requestId": 57, "canWait": True})
        self.assertEqual(w.writes, [("radarr", "PUT", "/movie/editor", {"movieIds": [54], "tags": [64], "applyTags": "remove"})])

    def test_pending_request_is_changed_in_seerr(self):
        w = World(requests=[req(status=1, tags=[21])], t=title(tags=()))
        status, out = post(w, {"action": "update", "requestId": 57, "profileId": 13, "tags": ["audio-swiss-german"], "canWait": False})
        self.assertEqual(status, 200)
        self.assertEqual(w.writes, [])
        self.assertEqual(w.admin_calls, [("PUT", "/request/57", {"mediaType": "movie", "serverId": 0, "profileId": 13, "rootFolder": "/data/media/movies", "languageProfileId": None, "tags": [13, 64], "is4k": False})])

    def test_pending_request_profile_pick_is_pinned(self):
        w = World(requests=[req(status=1, tags=[21])], t=title(tags=()))
        post(w, {"action": "update", "requestId": 57, "profileId": 11})
        self.assertEqual(w.admin_calls[0][2]["tags"], [21, 66])

    def test_pending_pick_of_the_swiss_german_profile_is_not_pinned(self):
        w = World(requests=[req(status=1, tags=[66], profile=11)], t=title(tags=()))
        post(w, {"action": "update", "requestId": 57, "profileId": 13})
        self.assertEqual(w.admin_calls[0][2]["tags"], [])

    def test_pending_tag_change_keeps_the_pin(self):
        w = World(requests=[req(status=1, tags=[66], profile=11)], t=title(tags=()))
        post(w, {"action": "update", "requestId": 57, "tags": ["audio-german"]})
        self.assertEqual(w.admin_calls[0][2]["tags"], [14, 66])

    def test_pending_pin_tag_is_created_when_missing(self):
        w = World(requests=[req(status=1)], t=title(tags=()), tags=[t for t in TAGS if t["label"] != "profile-pinned"])
        post(w, {"action": "update", "requestId": 57, "profileId": 11})
        self.assertEqual(w.writes, [("radarr", "POST", "/tag", {"label": "profile-pinned"})])
        self.assertEqual(w.admin_calls[0][2]["tags"], [99])

    def test_approved_profile_change_leaves_the_pin_to_the_language_hook(self):
        w = World(t=title(tags=()))
        post(w, {"action": "update", "requestId": 57, "profileId": 11})
        self.assertEqual(w.writes, [("radarr", "PUT", "/movie/editor", {"movieIds": [54], "qualityProfileId": 11})])

    def test_pending_series_keeps_its_seasons(self):
        w = World(requests=[req(status=1, seasons=[1, 2])], kind="tv")
        post(w, {"type": "tv", "action": "update", "requestId": 57, "profileId": 11})
        self.assertEqual(w.admin_calls[0][2]["seasons"], [1, 2])
        self.assertEqual(w.admin_calls[0][2]["mediaType"], "tv")

    def test_requester_may_not_change_a_shared_title_or_a_friends_request(self):
        w = World(requests=[req(), req(rid=60, uid=5, name="friend")])
        self.assertEqual(post(w, {"action": "update", "requestId": 57, "profileId": 11})[0], 403)
        self.assertEqual(post(w, {"action": "update", "requestId": 60, "profileId": 11})[0], 403)
        self.assertEqual(post(w, {"action": "delete", "requestId": 60})[0], 403)
        self.assertEqual((w.writes, w.admin_calls), ([], []))

    def test_unknown_request_id_is_refused(self):
        self.assertEqual(post(World(), {"action": "update", "requestId": 999, "profileId": 11})[0], 403)

    def test_origin_and_json_are_required(self):
        w = World()
        self.assertEqual(post(w, {"action": "delete", "requestId": 57}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(post(w, {"action": "delete", "requestId": 57}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(w.admin_calls, [])

    def test_bad_action_is_400(self):
        self.assertEqual(post(World(), {"action": "rm -rf", "requestId": 57})[0], 400)


class Delete(unittest.TestCase):
    def test_sole_request_on_a_title_it_added_clears_everything(self):
        w = World()
        status, out = post(w, {"action": "delete", "requestId": 57})
        self.assertEqual((status, out["deleted"]), (200, "clear"))
        self.assertEqual(w.admin_calls, [("DELETE", "/media/69", None)])

    def test_shared_title_only_loses_this_request(self):
        w = World(me=ADMIN, requests=[req(), req(rid=60, uid=5, name="friend")])
        status, out = post(w, {"action": "delete", "requestId": 57})
        self.assertEqual((status, out["deleted"]), (200, "request-only"))
        self.assertEqual(w.admin_calls, [("DELETE", "/request/57", None)])

    def test_title_that_was_there_before_only_loses_the_request(self):
        w = World(t=title(added="2026-01-01T00:00:00Z"))
        post(w, {"action": "delete", "requestId": 57})
        self.assertEqual(w.admin_calls, [("DELETE", "/request/57", None)])

    def test_files_on_disk_are_listed_for_the_delete_warning(self):
        w = World(t=title(has_file=True) | {"movieFile": {"relativePath": "Tied Up (2023) [2160p].mkv", "size": 61000000000}})
        self.assertEqual(get(w)[1]["requests"][0]["files"], {"count": 1, "size": 61000000000, "name": "Tied Up (2023) [2160p].mkv"})
        self.assertIsNone(get(World())[1]["requests"][0]["files"])
        sw = World(kind="tv", t=title(has_file=False) | {"statistics": {"episodeFileCount": 12, "sizeOnDisk": 30000000000}})
        self.assertEqual(get(sw, "type=tv&tmdb=673")[1]["requests"][0]["files"], {"count": 12, "size": 30000000000, "name": None})

    def test_clearing_a_title_with_files_needs_the_explicit_files_choice(self):
        w = World(t=title(has_file=True) | {"movieFile": {"relativePath": "Tied Up (2023) [2160p].mkv", "size": 61000000000}})
        status, out = post(w, {"action": "delete", "requestId": 57})
        self.assertEqual((status, out["error"], out["files"]["size"]), (409, "files", 61000000000))
        self.assertEqual(w.admin_calls, [])
        status, out = post(w, {"action": "delete", "requestId": 57, "files": "keep"})
        self.assertEqual((status, out["deleted"]), (200, "request-only"))
        self.assertEqual(w.admin_calls, [("DELETE", "/request/57", None)])
        w2 = World(t=title(has_file=True))
        status, out = post(w2, {"action": "delete", "requestId": 57, "files": "delete"})
        self.assertEqual((status, out["deleted"]), (200, "clear"))
        self.assertEqual(w2.admin_calls, [("DELETE", "/media/69", None)])
        self.assertEqual(post(w2, {"action": "delete", "requestId": 57, "files": "yes"})[0], 400)

    def test_nothing_downloaded_keep_files_still_only_removes_the_request(self):
        w = World()
        post(w, {"action": "delete", "requestId": 57, "files": "keep"})
        self.assertEqual(w.admin_calls, [("DELETE", "/request/57", None)])

    def test_without_the_seerr_admin_key_nothing_changes(self):
        w = World()
        d = w.deps()
        d.seerr_admin = None
        status, out = dn.handle("POST", "/_download-now/request", {"Content-Type": "application/json", "Origin": ORIGIN, "Cookie": "connect.sid=s%3Aabc"}, json.dumps({"type": "movie", "tmdbId": 673, "action": "delete", "requestId": 57}).encode(), d)
        self.assertEqual(status, 502)


if __name__ == "__main__":
    unittest.main()
