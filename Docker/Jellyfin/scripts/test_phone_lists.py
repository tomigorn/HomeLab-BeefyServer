import datetime
import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.parse

_spec = importlib.util.spec_from_file_location("phone_lists", pathlib.Path(__file__).with_name("phone-lists.py"))
pl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pl)

NOW = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.timezone.utc)


def ts(**ago):
    return (NOW - datetime.timedelta(**ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def req(state, users, updated=None, percent=0, title="T"):
    """users: {uid: request time}"""
    return {"state": state, "percent": percent, "title": title, "updated": updated or ts(minutes=1), "at": min(users.values()) if users else ts(minutes=1),
            "requestedBy": [{"user": u, "at": at} for u, at in users.items()], "phoneItem": "ph" if state == "ready" else None}


def sent(user, item, **ago):
    return {"at": ts(**ago), "user": user, "device": "d", "item": item, "phoneItem": "ph"}


class ReadyRowTest(unittest.TestCase):
    def test_only_my_requests_newest_first(self):
        items = {"a": req("downloading", {"u1": ts(hours=3)}, percent=40), "b": req("ready", {"u1": ts(hours=1)}), "c": req("ready", {"u2": ts(hours=1)})}
        row = pl.ready_row(items, [], "u1", NOW)
        self.assertEqual([(e["item"], e["kind"]) for e in row], [("b", "ready"), ("a", "preparing")])
        self.assertEqual(row[1]["percent"], 40)

    def test_my_own_request_time_orders_a_shared_title(self):
        items = {"a": req("ready", {"u2": ts(days=3), "u1": ts(minutes=5)}), "b": req("ready", {"u1": ts(hours=1)})}
        self.assertEqual([e["item"] for e in pl.ready_row(items, [], "u1", NOW)], ["a", "b"])

    def test_downloaded_after_my_request_moves_out(self):
        items = {"a": req("ready", {"u1": ts(hours=2)})}
        self.assertEqual(pl.ready_row(items, [sent("u1", "a", hours=1)], "u1", NOW), [])

    def test_someone_elses_download_or_an_older_one_does_not_count(self):
        items = {"a": req("ready", {"u1": ts(hours=2)})}
        self.assertEqual(len(pl.ready_row(items, [sent("u2", "a", hours=1), sent("u1", "a", hours=3)], "u1", NOW)), 1)

    def test_ready_gone_14_days_after_ready(self):
        old = {"a": req("ready", {"u1": ts(days=20)}, updated=ts(days=14, minutes=1))}
        fresh = {"a": req("ready", {"u1": ts(days=20)}, updated=ts(days=13))}
        self.assertEqual((pl.ready_row(old, [], "u1", NOW), len(pl.ready_row(fresh, [], "u1", NOW))), ([], 1))

    def test_nothing_or_failed_shown_two_days(self):
        for state in ("nothing", "failed", "not-needed"):
            shown = {"a": req(state, {"u1": ts(days=3)}, updated=ts(days=1))}
            gone = {"a": req(state, {"u1": ts(days=3)}, updated=ts(days=2, minutes=1))}
            self.assertEqual([e["kind"] for e in pl.ready_row(shown, [], "u1", NOW)], ["none"], state)
            self.assertEqual(pl.ready_row(gone, [], "u1", NOW), [], state)

    def test_preparing_states_stay_however_old(self):
        for state in ("searching", "downloading", "checking", "placing", "queued", "converting"):
            self.assertEqual([e["kind"] for e in pl.ready_row({"a": req(state, {"u1": ts(days=30)}, updated=ts(days=30))}, [], "u1", NOW)], ["preparing"])

    def test_unknown_state_or_broken_entry_skipped(self):
        items = {"a": req("weird", {"u1": ts(hours=1)}), "b": {"state": "ready"}, "c": "junk"}
        self.assertEqual(pl.ready_row(items, [], "u1", NOW), [])


EPS = {"e1": {"Type": "Episode", "SeriesId": "sh", "SeasonId": "s1", "SeriesName": "Show", "ParentIndexNumber": 1},
       "e2": {"Type": "Episode", "SeriesId": "sh", "SeasonId": "s1", "SeriesName": "Show", "ParentIndexNumber": 1},
       "e3": {"Type": "Episode", "SeriesId": "sh", "SeasonId": "s1", "SeriesName": "Show", "ParentIndexNumber": 1},
       "f1": {"Type": "Episode", "SeriesId": "sh", "SeasonId": "s2", "SeriesName": "Show", "ParentIndexNumber": 2},
       "g1": {"Type": "Episode", "SeriesId": "other", "SeasonId": "o1", "SeriesName": "Other", "ParentIndexNumber": 1},
       "m": {"Type": "Movie"}}


def entry(item, kind, minutes, state=None, percent=0):
    return {"item": item, "kind": kind, "state": state or {"ready": "ready", "none": "nothing"}.get(kind, "downloading"), "percent": percent, "title": item, "at": NOW - datetime.timedelta(minutes=minutes)}


class SeasonGroupTest(unittest.TestCase):
    def test_two_episodes_of_a_season_become_one_season_entry(self):
        out = pl.by_show([entry("e1", "preparing", 30, percent=40), entry("m", "ready", 20), entry("e2", "ready", 10)], EPS)
        self.assertEqual([(e["item"], e["kind"]) for e in out], [("s1", "preparing"), ("m", "ready")])
        self.assertEqual((out[0]["title"], pl.band_text(out[0]), out[0]["at"]), ("Show S01", "1 of 2 ready · Downloading 40 %", NOW - datetime.timedelta(minutes=10)))

    def test_all_ready_is_the_season_itself(self):
        self.assertEqual([(e["item"], e["kind"]) for e in pl.by_show([entry("e1", "ready", 5), entry("e2", "ready", 6)], EPS)], [("s1", "ready")])

    def test_nothing_smaller_is_counted(self):
        [e] = pl.by_show([entry("e1", "ready", 5), entry("e2", "ready", 6), entry("e3", "none", 7)], EPS)
        self.assertEqual((e["kind"], pl.band_text(e)), ("none", "2 of 3 ready · 1 no smaller version"))

    def test_the_slowest_episode_names_the_state(self):
        eps = [entry("e1", "preparing", 5, "checking"), entry("e2", "preparing", 6, percent=70), entry("e3", "preparing", 7, percent=30)]
        self.assertEqual(pl.band_text(pl.by_show(eps, EPS)[0]), "0 of 3 ready · Downloading 30 %")

    def test_single_episodes_of_other_series_stay_apart(self):
        out = pl.by_show([entry("e1", "ready", 5), entry("g1", "ready", 6), entry("m", "ready", 7)], EPS)
        self.assertEqual([e["item"] for e in out], ["e1", "g1", "m"])

    def test_episodes_of_several_seasons_become_one_series_entry(self):
        out = pl.by_show([entry("e1", "ready", 5), entry("g1", "ready", 6), entry("f1", "preparing", 7, percent=20)], EPS)
        self.assertEqual([(e["item"], e["kind"]) for e in out], [("sh", "preparing"), ("g1", "ready")])
        self.assertEqual((out[0]["title"], pl.band_text(out[0])), ("Show", "1 of 2 ready · Downloading 20 %"))

    def test_all_ready_across_seasons_is_the_series_itself(self):
        self.assertEqual([(e["item"], e["kind"]) for e in pl.by_show([entry("e1", "ready", 5), entry("f1", "ready", 6)], EPS)], [("sh", "ready")])

    def test_sent_episodes_of_one_season_fold_into_the_season_at_the_newest_place(self):
        self.assertEqual(pl.fold_shows(["m", "e2", "g1", "e1"], EPS), ["m", "s1", "g1"])

    def test_sent_episodes_of_several_seasons_fold_into_the_series(self):
        self.assertEqual(pl.fold_shows(["m", "e2", "f1", "e1"], EPS), ["m", "sh"])


class SentRowTest(unittest.TestCase):
    def test_mine_newest_first_once_per_title_14_days(self):
        recs = [sent("u1", "a", days=3), sent("u1", "b", days=1), sent("u1", "a", hours=2), sent("u2", "c", hours=1), sent("u1", "d", days=14, minutes=1)]
        self.assertEqual(pl.sent_row(recs, "u1", NOW), ["a", "b"])


class LabelTest(unittest.TestCase):
    def test_band_texts(self):
        cases = [("searching", 0, "en", "Searching…"), ("downloading", 42, "en", "Downloading 42 %"), ("checking", 100, "en", "Checking"),
                 ("placing", 100, "en", "Almost ready"), ("queued", 0, "en", "Converting soon"), ("converting", 37, "en", "Converting 37 %"), ("nothing", 0, "en", "No smaller version"),
                 ("failed", 0, "en", "No smaller version")]
        for state, pct, lang, want in cases:
            kind = "none" if state in ("nothing", "failed") else "preparing"
            self.assertEqual(pl.band_text({"kind": kind, "state": state, "percent": pct}), want)

    def test_language_from_the_display_language(self):
        self.assertEqual([pl.language(p) for p in ({"CustomPrefs": {"language": "de-CH"}}, {"CustomPrefs": {"language": "en-US"}}, {}, None)], ["de", "en", "en", "en"])


class BlockedTagsTest(unittest.TestCase):
    def test_blocks_other_owners_keeps_foreign_tags(self):
        have = ["kids", "hss-media-bar-u2", "hss-phone-u-gone"]
        self.assertEqual(pl.blocked_tags(have, "u1", {"u1", "u2"}), ["kids", "hss-media-bar-u2", "hss-phone-u-u2"])

    def test_existing_order_kept(self):
        have = ["hss-phone-u-u2", "kids", "hss-media-bar-u2"]
        self.assertEqual(pl.blocked_tags(have, "u1", {"u1", "u2", "u3"}), ["hss-phone-u-u2", "kids", "hss-media-bar-u2", "hss-phone-u-u3"])


def section(kind, order, coll=None, text=None):
    s = {"type": kind, "enabled": True, "order": order}
    if coll:
        s.update(kind="pluginDynamic", pluginSource="collections", type="none", pluginSection="collection", pluginAdditionalData=coll, pluginDisplayText=text, serverId=pl.SERVER_URL)
    return s


class HomeRowsTest(unittest.TestCase):
    BASE = [section("resume", 0), section("nextup", 1)]

    def test_rows_go_on_top_others_keep_their_order(self):
        out = pl.home_rows(self.BASE, [("s1", "📱 Ready for your phone"), ("s2", "📲 On your phone")])
        self.assertEqual([(s["type"], s.get("pluginAdditionalData"), s["order"]) for s in out], [("none", "s1", 0), ("none", "s2", 1), ("resume", None, 2), ("nextup", None, 3)])

    def test_old_rows_replaced_empty_removes_them(self):
        have = [section("x", 0, "old", "📲 Auf deinem Handy"), section("resume", 1), section("nextup", 2)]
        self.assertEqual([s["type"] for s in pl.home_rows(have, [])], ["resume", "nextup"])
        self.assertEqual([s.get("pluginAdditionalData") for s in pl.home_rows(have, [("s1", "📱 Bereit fürs Handy")])], ["s1", None, None])

    def test_a_row_under_its_old_name_is_replaced(self):
        have = [section("x", 0, "s1", "📱 Ready for your phone"), section("resume", 1)]
        out = pl.home_rows(have, [("s1", pl.NAMES["ready"]["en"])])
        self.assertEqual([s.get("pluginDisplayText") or s["type"] for s in out], ["📱 Your phone version requests", "resume"])

    def test_is_phone_row(self):
        self.assertTrue(pl.is_phone_row(section("x", 0, "id", "📱 Ready for your phone")))
        self.assertFalse(pl.is_phone_row(section("x", 0, "id", "Hidden Gems")))
        self.assertFalse(pl.is_phone_row(section("resume", 0)))


class FakeJellyfin:
    """Users u1 (admin), u2; titles a, b, c; BoxSets with tags, members, images; Moonfin settings per user."""

    def __init__(self):
        self.users = {"u1": {"IsAdministrator": True, "BlockedTags": ["kids"]}, "u2": {"IsAdministrator": False, "BlockedTags": []}}
        self.titles = {"a": "Movie", "b": "Episode", "c": "Movie", "e1": "Episode", "e2": "Episode", "s1": "Season"}
        self.sets = {}
        self.images = []
        self.moonfin = {"u1": {"syncEnabled": True, "mobile": {"homeSections": [section("resume", 0), section("nextup", 1)]}, "tv": {"homeSections": [section("resume", 0)]}},
                        "u2": {"syncEnabled": True, "mobile": {"homeSections": [section("resume", 0)]}}}
        self.prefs = {"u1": {"CustomPrefs": {"language": "de"}}}
        self.writes = []

    def add_set(self, name, tags, members=()):
        path = f"/config/data/collections/{name} [boxset]"
        sid = "set-" + hashlib.md5(path.encode()).hexdigest()[:8]
        self.sets.setdefault(sid, {"Id": sid, "Name": name, "Path": path, "Tags": list(tags), "Members": list(members), "Type": "BoxSet"})
        return sid

    def visible(self, uid, sid):
        return not set(self.sets[sid]["Tags"]) & set(self.users[uid]["BlockedTags"])

    def __call__(self, method, path, body=None):
        url = urllib.parse.urlsplit(path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        p = url.path
        if method != "GET":
            self.writes.append((method, p))
        if p == "/Users":
            return [{"Id": u, "Policy": dict(pol)} for u, pol in self.users.items()]
        if p.startswith("/Users/"):
            uid = p.split("/")[2]
            if method == "POST":
                self.users[uid] = dict(body)
                return None
            return {"Id": uid, "Policy": dict(self.users[uid])}
        if p.startswith("/DisplayPreferences/"):
            return self.prefs.get(q["userId"], {})
        if p.startswith("/Moonfin/Settings/"):
            uid = p.split("/")[3]
            if method == "POST":
                for k, v in body["settings"].items():
                    if isinstance(v, dict):
                        self.moonfin[uid].setdefault(k, {}).update(v)
                return None
            if uid not in self.moonfin:
                raise urllib.error.HTTPError(path, 404, "nf", None, None)
            return json.loads(json.dumps(self.moonfin[uid]))
        if p == "/Collections" and method == "POST":
            # like Jellyfin: the folder is "<name> [boxset]" and the id comes from that path, so the same name gives the same set
            sid = self.add_set(q["name"], [])
            m = self.sets[sid]["Members"]
            m += [i for i in q.get("ids", "").split(",") if i and i not in m]
            return {"Id": sid}
        if p.startswith("/Collections/"):
            sid, ids = p.split("/")[2], q["ids"].split(",")
            m = self.sets[sid]["Members"]
            if method == "POST":
                m += [i for i in ids if i not in m]
            else:
                self.sets[sid]["Members"] = [i for i in m if i not in ids]
            return None
        if p.startswith("/Items/") and method == "DELETE":
            del self.sets[p.split("/")[2]]
            return None
        if p.startswith("/Items/") and method == "POST":
            sid = p.split("/")[2]
            self.sets[sid].update({k: v for k, v in body.items() if k not in ("Members", "Path")})
            return None
        if p.startswith("/Items/"):
            return {k: v for k, v in self.sets[p.split("/")[2]].items() if k != "Members"}
        if p == "/Items" and "ParentId" in q:
            return {"Items": [{"Id": i} for i in self.sets[q["ParentId"]]["Members"]]}
        if p == "/Items" and "ids" in q:
            return {"Items": [{"Id": i, "Type": self.titles[i], "SeriesId": "ser", **({"SeasonId": "s1", "SeriesName": "Show", "ParentIndexNumber": 1} if i.startswith("e") else {})}
                              for i in q["ids"].split(",") if i in self.titles]}
        if p == "/Items" and q.get("IncludeItemTypes") == "BoxSet":
            return {"Items": [{"Id": s["Id"], "Tags": list(s["Tags"])} for s in self.sets.values() if q["Tags"] in s["Tags"]]}
        raise AssertionError(f"unexpected {method} {path}")

    def raw(self, method, path, data, content_type):
        if method == "POST":
            self.images.append(path)
            return b""
        return None

    def by_kind(self, uid, kind):
        return [s for s in self.sets.values() if OWNER_TAG(uid) in s["Tags"] and pl.KIND + kind in s["Tags"]]


def OWNER_TAG(uid):
    return pl.OWNER + uid


class FakeArt:
    def status_poster(self, base, text, icon, title=""):
        return text.encode()

    def status_thumb(self, back, title, text, icon):
        return text.encode()


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.box = pathlib.Path(self.tmp.name)
        self.state = self.box / "lists.json"
        self.jf = FakeJellyfin()
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def inputs(self, items, sent=()):
        (self.box / "state.json").write_text(json.dumps({"items": items}))
        (self.box / "sent.jsonl").write_text("".join(json.dumps(s) + "\n" for s in sent))

    def run_lists(self, *argv, now=NOW):
        return pl.run(list(argv), api=self.jf, raw=self.jf.raw, box=self.box, state_path=self.state, now=now, log=self.logs.append, art=FakeArt())

    def test_rows_only_for_the_asker_on_the_mobile_home(self):
        self.inputs({"a": req("downloading", {"u1": ts(hours=1)}, percent=40, title="Film A"), "c": req("ready", {"u1": ts(minutes=5)})}, [sent("u1", "b", hours=2)])
        self.assertEqual(self.run_lists(), 0)
        [ready], [shelf], [entry] = (self.jf.by_kind("u1", k) for k in ("ready", "sent", "entry"))
        self.assertEqual((ready["Name"], shelf["Name"]), ("📱 Deine angefragten Handy-Versionen", "📲 Auf deinem Handy"))
        self.assertEqual(ready["Members"], ["c", entry["Id"]])
        self.assertEqual((shelf["Members"], entry["Members"], entry["Name"]), (["b"], ["a"], "Film A"))
        self.assertTrue(entry["Overview"].startswith("Downloading 40 %"))
        self.assertEqual(sorted(self.jf.images), [f"/Items/{entry['Id']}/Images/Primary", f"/Items/{entry['Id']}/Images/Thumb"])
        mobile = self.jf.moonfin["u1"]["mobile"]["homeSections"]
        self.assertEqual([s.get("pluginAdditionalData") or s["type"] for s in mobile], [ready["Id"], shelf["Id"], "resume", "nextup"])
        self.assertEqual(len(self.jf.moonfin["u1"]["tv"]["homeSections"]), 1)
        self.assertEqual(self.jf.moonfin["u2"]["mobile"]["homeSections"], [section("resume", 0)])
        self.assertEqual(self.jf.users["u2"]["BlockedTags"], [OWNER_TAG("u1")])
        self.assertEqual(self.jf.users["u1"]["BlockedTags"], ["kids"])
        self.assertFalse(any(self.jf.visible("u2", s) for s in self.jf.sets))

    def test_second_run_changes_nothing_and_check_is_clean(self):
        self.inputs({"a": req("downloading", {"u1": ts(hours=1)}, percent=40)})
        self.run_lists()
        self.jf.writes.clear()
        self.state.unlink()
        self.assertEqual(self.run_lists(), 0)
        self.assertEqual(self.jf.writes, [])
        self.assertEqual(self.run_lists("--check"), 0)

    def test_progress_redraws_the_band_ready_drops_the_entry_download_moves_it(self):
        self.inputs({"a": req("downloading", {"u1": ts(hours=1)}, percent=40)})
        self.run_lists()
        self.inputs({"a": req("downloading", {"u1": ts(hours=1)}, percent=80)})
        self.run_lists(now=NOW + datetime.timedelta(minutes=2))
        self.assertEqual(len(self.jf.images), 4)
        self.inputs({"a": req("ready", {"u1": ts(hours=1)})})
        self.run_lists(now=NOW + datetime.timedelta(minutes=4))
        self.assertEqual(self.jf.by_kind("u1", "entry"), [])
        self.assertEqual(self.jf.by_kind("u1", "ready")[0]["Members"], ["a"])
        self.inputs({"a": req("ready", {"u1": ts(hours=1)})}, [sent("u1", "a", minutes=0)])
        self.run_lists(now=NOW + datetime.timedelta(minutes=6))
        self.assertEqual(self.jf.by_kind("u1", "ready")[0]["Members"], [])
        self.assertEqual(self.jf.by_kind("u1", "sent")[0]["Members"], ["a"])

    def test_a_season_is_one_card_from_request_to_phone(self):
        self.inputs({"e1": req("downloading", {"u1": ts(hours=1)}, percent=40), "e2": req("ready", {"u1": ts(hours=1)})})
        self.run_lists()
        [entry] = self.jf.by_kind("u1", "entry")
        self.assertEqual((entry["Name"], entry["Members"], self.jf.by_kind("u1", "ready")[0]["Members"]), ("Show S01", ["s1"], [entry["Id"]]))
        self.assertTrue(entry["Overview"].startswith("1 of 2 ready · Downloading 40 %"))
        self.inputs({"e1": req("ready", {"u1": ts(hours=1)}), "e2": req("ready", {"u1": ts(hours=1)})})
        self.run_lists(now=NOW + datetime.timedelta(minutes=2))
        self.assertEqual((self.jf.by_kind("u1", "entry"), self.jf.by_kind("u1", "ready")[0]["Members"]), ([], ["s1"]))
        self.inputs({"e1": req("ready", {"u1": ts(hours=1)}), "e2": req("ready", {"u1": ts(hours=1)})}, [sent("u1", "e1", minutes=0), sent("u1", "e2", minutes=0)])
        self.run_lists(now=NOW + datetime.timedelta(minutes=4))
        self.assertEqual((self.jf.by_kind("u1", "ready")[0]["Members"], self.jf.by_kind("u1", "sent")[0]["Members"]), ([], ["s1"]))
        self.assertEqual(self.run_lists("--check", now=NOW + datetime.timedelta(minutes=4)), 0, self.logs[-1])

    def test_check_needs_no_local_state(self):
        self.inputs({"a": req("downloading", {"u1": ts(hours=1)}, percent=40)})
        self.run_lists()
        self.state.unlink()
        self.assertEqual(self.run_lists("--check"), 0, self.logs[-1])

    def test_unchanged_inputs_skip_until_the_hourly_run(self):
        self.inputs({"a": req("ready", {"u1": ts(hours=1)})})
        self.run_lists()
        self.jf.writes.clear()
        self.run_lists(now=NOW + datetime.timedelta(minutes=10))
        self.assertEqual(self.logs[-1], "unchanged inputs, skipped")

    def test_renamed_rows_are_not_skipped(self):
        self.inputs({"a": req("ready", {"u1": ts(hours=1)})})
        self.run_lists()
        pl.NAMES["ready"]["de"], old = "📱 Neu", pl.NAMES["ready"]["de"]
        try:
            self.run_lists(now=NOW + datetime.timedelta(minutes=10))
        finally:
            pl.NAMES["ready"]["de"] = old
        self.assertEqual(self.jf.by_kind("u1", "ready")[0]["Name"], "📱 Neu")

    def test_unreadable_state_changes_nothing(self):
        (self.box / "state.json").write_text("{broken")
        self.assertEqual(self.run_lists(), 1)
        self.assertEqual(self.jf.writes, [])

    def test_check_reports_drift_without_writing(self):
        self.inputs({"c": req("ready", {"u1": ts(hours=1)})})
        self.assertEqual(self.run_lists("--check"), 1)
        self.assertEqual(self.jf.writes, [])

    def test_two_users_get_their_own_sets_at_the_same_time(self):
        # 2026-11-22: same row name = same Jellyfin folder = same id; the canary's set took over the owner's
        self.jf.prefs = {}
        self.inputs({"a": req("downloading", {"u1": ts(hours=1), "u2": ts(hours=1)}, percent=40, title="Film A"), "c": req("ready", {"u1": ts(minutes=5), "u2": ts(minutes=5)})},
                    [sent("u1", "b", hours=2), sent("u2", "b", hours=2)])
        self.assertEqual(self.run_lists(), 0, self.logs[-1])
        for kind in ("ready", "sent", "entry"):
            [mine], [theirs] = self.jf.by_kind("u1", kind), self.jf.by_kind("u2", kind)
            self.assertNotEqual(mine["Id"], theirs["Id"], kind)
            self.assertEqual(mine["Name"], theirs["Name"], kind)
            self.assertEqual(len([t for t in mine["Tags"] + theirs["Tags"] if t.startswith(pl.OWNER)]), 2, kind)
        self.assertEqual(self.jf.by_kind("u1", "sent")[0]["Name"], "📲 On your phone")
        self.assertEqual([self.jf.by_kind(u, "sent")[0]["Members"] for u in ("u1", "u2")], [["b"], ["b"]])
        self.assertFalse(any(self.jf.visible("u2", s["Id"]) for s in self.jf.sets.values() if pl.OWNER + "u1" in s["Tags"]))
        self.assertFalse(any(self.jf.visible("u1", s["Id"]) for s in self.jf.sets.values() if pl.OWNER + "u2" in s["Tags"]))
        self.assertEqual(self.run_lists("--check"), 0, self.logs[-1])

    def test_a_set_made_under_its_shown_name_stays_its_owners(self):
        # sets made before 2026-11-22 live in the folder of their shown name; a second user's new set must not land there
        self.jf.prefs = {}
        sid = self.jf.add_set("📲 On your phone", [pl.TAG, pl.OWNER + "u1", pl.KIND + "sent"], ["b"])
        self.inputs({}, [sent("u1", "b", hours=2), sent("u2", "c", hours=1)])
        self.assertEqual(self.run_lists(), 0, self.logs[-1])
        self.assertEqual((self.jf.by_kind("u1", "sent"), len(self.jf.by_kind("u2", "sent"))), ([self.jf.sets[sid]], 1))
        self.assertEqual(self.jf.sets[sid]["Members"], ["b"])
        self.assertEqual(self.jf.by_kind("u2", "sent")[0]["Members"], ["c"])

    def test_a_new_set_that_comes_back_as_someone_elses_is_not_taken_over(self):
        self.jf.prefs = {}
        self.inputs({}, [sent("u1", "b", hours=2), sent("u2", "c", hours=1)])
        real = self.jf.__call__
        self.jf.add_set("x", [pl.TAG, pl.OWNER + "u1", pl.KIND + "sent"], ["b"])
        def collide(method, path, body=None):
            if method == "POST" and path.startswith("/Collections?"):
                return {"Id": self.jf.by_kind("u1", "sent")[0]["Id"]}
            return real(method, path, body)
        self.run_lists_with(collide)
        self.assertEqual(self.jf.by_kind("u1", "sent")[0]["Members"], ["b"])
        self.assertEqual(self.jf.by_kind("u2", "sent"), [])
        self.assertTrue(any("not taken over" in line for line in self.logs), self.logs)

    def run_lists_with(self, api):
        return pl.run([], api=api, raw=self.jf.raw, box=self.box, state_path=self.state, now=NOW, log=self.logs.append, art=FakeArt())

    def test_gone_title_is_left_out(self):
        self.inputs({"zzz": req("ready", {"u1": ts(hours=1)})})
        self.run_lists()
        self.assertEqual(self.jf.sets, {})


class HintTest(unittest.TestCase):
    def test_the_hint_says_to_pick_a_smaller_quality(self):
        # owner 2026-11-22: Original = the full file, any smaller quality = the phone version
        self.assertIn("smaller quality", pl.HINT)
        self.assertNotIn("Original:", pl.HINT)


if __name__ == "__main__":
    unittest.main()
