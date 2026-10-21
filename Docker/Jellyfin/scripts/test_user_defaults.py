import importlib.util
import json
import pathlib
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location("user_defaults", pathlib.Path(__file__).with_name("user-defaults.py"))
ud = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ud)

STOCK = {"PlayDefaultAudioTrack": True, "SubtitleLanguagePreference": "", "SubtitleMode": "Default",
         "RememberAudioSelections": True, "RememberSubtitleSelections": True, "HidePlayedInLatest": True}


def user(uid, **config):
    return {"Id": uid, "Name": uid, "Configuration": {**STOCK, **config}}


class DefaultsTest(unittest.TestCase):
    def test_household_defaults(self):
        self.assertEqual(ud.DEFAULTS, {"AudioLanguagePreference": "eng", "PlayDefaultAudioTrack": False,
                                       "SubtitleLanguagePreference": "", "SubtitleMode": "None",
                                       "RememberAudioSelections": True, "RememberSubtitleSelections": True})

    def test_new_user_gets_defaults_and_keeps_other_settings(self):
        cfg = ud.new_user_config(STOCK)
        self.assertEqual(cfg["AudioLanguagePreference"], "eng")
        self.assertFalse(cfg["PlayDefaultAudioTrack"])
        self.assertEqual(cfg["SubtitleMode"], "None")
        self.assertTrue(cfg["HidePlayedInLatest"])

    def test_adopted_user_keeps_choices_only_remember_turned_on(self):
        own = {**STOCK, "AudioLanguagePreference": "ger", "SubtitleMode": "Always", "RememberSubtitleSelections": False}
        cfg = ud.adopted_config(own)
        self.assertEqual(cfg, {**own, "RememberSubtitleSelections": True})

    def test_adopted_user_already_remembering_is_unchanged(self):
        self.assertEqual(ud.adopted_config(STOCK), STOCK)


class PlanTest(unittest.TestCase):
    def test_first_run_adopts_everyone(self):
        updates, handled = ud.plan([user("a", RememberAudioSelections=False), user("b")], None)
        self.assertEqual([u for u, _ in updates], ["a"])
        self.assertTrue(updates[0][1]["PlayDefaultAudioTrack"])
        self.assertEqual(handled, {"a", "b"})

    def test_later_run_sets_defaults_for_new_users_only(self):
        updates, handled = ud.plan([user("a"), user("new")], {"a"})
        self.assertEqual([u for u, _ in updates], ["new"])
        self.assertEqual(updates[0][1]["SubtitleMode"], "None")
        self.assertEqual(handled, {"a", "new"})

    def test_known_user_who_changed_settings_is_never_touched(self):
        updates, _ = ud.plan([user("a", SubtitleMode="Always", RememberAudioSelections=False)], {"a"})
        self.assertEqual(updates, [])

    def test_deleted_users_leave_the_state(self):
        _, handled = ud.plan([user("a")], {"a", "gone"})
        self.assertEqual(handled, {"a"})


class StateTest(unittest.TestCase):
    def test_missing_state_is_none_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "sub" / "state.json"
            self.assertIsNone(ud.load_state(path))
            ud.save_state(path, {"b", "a"})
            self.assertEqual(json.loads(path.read_text()), ["a", "b"])
            self.assertEqual(ud.load_state(path), {"a", "b"})


if __name__ == "__main__":
    unittest.main()
