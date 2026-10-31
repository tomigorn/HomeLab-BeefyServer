import importlib.util
import json
import pathlib
import sqlite3
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location("identity_map", pathlib.Path(__file__).with_name("identity-map.py"))
im = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(im)

ISSUER = "https://sso.holy-grail.ch/application/o/jellyfin"
JANE = "40e05f0a-9e96-4fd9-b225-eb1379dfcfa4"
GUEST = "11111111222233334444555555555555"


def link(uid, sub, issuer=ISSUER):
    return {"Issuer": issuer, "Subject": sub, "UserId": uid, "Email": "jane@example.com", "Username": "Jane Doe"}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = pathlib.Path(self.tmp.name, "db.sqlite3")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE user (id INTEGER PRIMARY KEY, email TEXT, jellyfinUserId TEXT)")
        con.execute("CREATE TABLE linked_accounts (id INTEGER PRIMARY KEY, provider TEXT, sub TEXT, username TEXT, userId INTEGER)")
        con.executemany("INSERT INTO user VALUES (?, ?, ?)", [(7, "jane@example.com", JANE.replace("-", "")), (5, "guest@example.com", None), (20, "jellyfin-popular", None)])
        con.executemany("INSERT INTO linked_accounts (provider, sub, username, userId) VALUES (?, ?, ?, ?)",
                        [("holy-grail", "sub-jane", "Jane Doe", 7), ("holy-grail", "sub-guest", "Guest Name", 5), ("elsewhere", "sub-x", "x", 5)])
        con.commit()
        con.close()
        self.out = pathlib.Path(self.tmp.name, "identity.json")

    def tearDown(self):
        self.tmp.cleanup()

    def con(self):
        return sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)


class Build(Fixture):
    def test_ids_and_subjects_only(self):
        m = im.build({"IdentityLinks": [link(JANE, "sub-jane"), link(GUEST, "sub-guest")], "ClientSecret": "s3cret"}, self.con())
        self.assertEqual(m, {"jellyfin": {JANE.replace("-", ""): "sub-jane", GUEST: "sub-guest"}, "seerr": {"7": "sub-jane", "5": "sub-guest"}})
        text = json.dumps(m)
        for leak in ("@", "s3cret", "Jane Doe", "Guest Name", "Username"):
            self.assertNotIn(leak, text)

    def test_other_issuer_and_conflicting_links_are_left_out(self):
        m = im.build({"IdentityLinks": [link(JANE, "sub-jane", issuer="https://other.example.com/o/x"), link(GUEST, "a"), link(GUEST, "b"), link(GUEST.upper(), "")]}, self.con())
        self.assertEqual(m["jellyfin"], {})

    def test_subject_of_two_jellyfin_users_is_left_out(self):
        m = im.build({"IdentityLinks": [link(JANE, "sub-guest"), link(GUEST, "sub-guest")]}, self.con())
        self.assertEqual(m["jellyfin"], {})

    def test_trailing_slash_issuer_counts(self):
        self.assertEqual(im.build({"IdentityLinks": [link(GUEST, "sub-guest", issuer=ISSUER + "/")]}, self.con())["jellyfin"], {GUEST: "sub-guest"})


class Write(Fixture):
    def test_written_once_then_only_on_change(self):
        m = {"jellyfin": {GUEST: "s"}, "seerr": {"5": "s"}}
        self.assertTrue(im.write_if_changed(self.out, m))
        self.assertEqual(oct(self.out.stat().st_mode & 0o777), "0o644")
        self.assertFalse(im.write_if_changed(self.out, m))
        self.assertTrue(im.write_if_changed(self.out, {"jellyfin": {}, "seerr": {}}))
        self.assertEqual(json.loads(self.out.read_text()), {"jellyfin": {}, "seerr": {}})


class Check(Fixture):
    def test_every_human_reached_by_jellyfin_link_or_subject(self):
        m = im.build({"IdentityLinks": [link(GUEST, "sub-guest")]}, self.con())
        self.assertEqual(im.problems(m, self.con(), {JANE.replace("-", ""), GUEST}), [])

    def test_human_reachable_only_by_name_is_a_problem(self):
        m = im.build({"IdentityLinks": []}, self.con())
        self.assertEqual(im.problems(m, self.con(), {JANE.replace("-", ""), GUEST}), [5])

    def test_jellyfin_link_to_a_gone_user_is_a_problem(self):
        m = im.build({"IdentityLinks": [link(GUEST, "sub-guest")]}, self.con())
        self.assertEqual(im.problems(m, self.con(), {GUEST}), [7])


if __name__ == "__main__":
    unittest.main()
