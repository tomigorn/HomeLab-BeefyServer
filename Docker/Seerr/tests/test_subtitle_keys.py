#!/usr/bin/env python3
"""scripts/subtitle-keys.py: picks the SubDL key and the OpenSubtitles login from Bazarr's config and the API key from Bazarr's code, writes them 0400.
Usage: python3 tests/test_subtitle_keys.py"""
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sk", ROOT / "scripts" / "subtitle-keys.py")
sk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sk)

CONFIG = """
general:
  enabled_providers: [subdl, opensubtitlescom]
subdl:
  ai_translate: false
  api_key: SUBDLKEY123
opensubtitlescom:
  password: PASSWORD123
  use_hash: true
  username: someone
"""
PROVIDERS = """
        'opensubtitlescom': {'username': settings.opensubtitlescom.username,
                             'password': settings.opensubtitlescom.password,
                             'include_machine_translated': settings.opensubtitlescom.include_machine_translated,
                             'api_key': 'OSKEY123'
                             },
        'napiprojekt': {'hash_only': settings.napiprojekt.hash_only,
                        'api_key': 'NOTTHISONE'},
"""


class Extract(unittest.TestCase):
    def test_picks_all_three(self):
        subdl, opensubs = sk.extract(CONFIG, PROVIDERS)
        self.assertEqual(subdl, "SUBDLKEY123")
        self.assertEqual(opensubs, {"username": "someone", "password": "PASSWORD123", "api_key": "OSKEY123"})

    def test_missing_parts_are_none(self):
        subdl, opensubs = sk.extract("general: {}\n", "nothing here")
        self.assertEqual((subdl, opensubs), (None, None))

    def test_written_read_only_for_the_owner(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "indexer-bar"
            names = sk.save(out, "SUBDLKEY123", {"username": "someone", "password": "PASSWORD123", "api_key": "OSKEY123"})
            names = sk.save(out, "NEWKEY", None)
            self.assertEqual(names, ["subdl.apikey"])
            self.assertEqual((out / "subdl.apikey").read_text(), "NEWKEY\n")
            self.assertEqual(json.loads((out / "opensubtitles.json").read_text())["api_key"], "OSKEY123")
            self.assertEqual(oct(out.stat().st_mode & 0o777), "0o700")
            for f in out.iterdir():
                self.assertEqual(oct(f.stat().st_mode & 0o777), "0o400", f.name)


if __name__ == "__main__":
    unittest.main()
