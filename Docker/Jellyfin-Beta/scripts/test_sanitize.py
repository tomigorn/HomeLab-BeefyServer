import json
import pathlib
import tempfile
import unittest

import sanitize

OPTS = "<LibraryOptions><EnableRealtimeMonitor>true</EnableRealtimeMonitor><SaveLocalMetadata>true</SaveLocalMetadata><EnableTrickplayImageExtraction>true</EnableTrickplayImageExtraction><Other>x</Other></LibraryOptions>"
HOOKS = '<?xml version="1.0" encoding="utf-8"?>\n<PluginConfiguration><Hooks><HookConfig><Name>Tracker</Name><Url>http://x/hook/s</Url></HookConfig></Hooks></PluginConfiguration>'


def tree(root):
    c = pathlib.Path(root)
    (c / "ScheduledTasks").mkdir(parents=True)
    (c / "ScheduledTasks" / "a.js").write_text('[{"Type":"DailyTrigger","TimeOfDayTicks":1}]')
    (c / "ScheduledTasks" / "b.js").write_text('[{"Type":"StartupTrigger"},{"Type":"IntervalTrigger","IntervalTicks":5}]')
    lib = c / "data" / "root" / "default" / "Movies"
    lib.mkdir(parents=True)
    (lib / "options.xml").write_text(OPTS)
    conf = c / "data" / "plugins" / "configurations"
    conf.mkdir(parents=True)
    (conf / "Jellyfin.Webhooks.xml").write_text(HOOKS)
    (conf / "Moonfin.Server.xml").write_text("<PluginConfiguration><SeerrEnabled>true</SeerrEnabled><SeerrUrl>http://jellyfin-popular:8099</SeerrUrl></PluginConfiguration>")
    (conf / "IntroSkipper.xml").write_text("<PluginConfiguration><AutoDetectIntros>true</AutoDetectIntros></PluginConfiguration>")
    (conf / "Jellyfin.Plugin.Oidc.xml").write_text("<PluginConfiguration><Enabled>true</Enabled><PasswordLoginMode>DisableForAllUsers</PasswordLoginMode><IssuerUrl>p</IssuerUrl></PluginConfiguration>")
    (c / "system.xml").write_text("<ServerConfiguration><ServerName>Prod</ServerName></ServerConfiguration>")
    (c / "branding.xml").write_text("<BrandingOptions><LoginDisclaimer>Need help?</LoginDisclaimer></BrandingOptions>")
    (c / "data" / "data").mkdir(parents=True)
    (c / "data" / "data" / "device.txt").write_text("prodid")
    return c


class SetValues(unittest.TestCase):
    def test_replaces_every_occurrence_and_keeps_the_rest(self):
        out = sanitize.set_values(OPTS, {"EnableRealtimeMonitor": "false", "SaveLocalMetadata": "false"})
        self.assertIn("<EnableRealtimeMonitor>false</EnableRealtimeMonitor>", out)
        self.assertIn("<SaveLocalMetadata>false</SaveLocalMetadata>", out)
        self.assertIn("<Other>x</Other>", out)

    def test_missing_tag_is_not_added(self):
        self.assertEqual(sanitize.set_values("<a><b>1</b></a>", {"c": "2"}), "<a><b>1</b></a>")


class Run(unittest.TestCase):
    def test_beta_gets_no_triggers_hooks_scans_or_seerr(self):
        with tempfile.TemporaryDirectory() as d:
            c = tree(d)
            saved = c / "server-id"
            self.assertEqual(sanitize.run(c, check=True, prod_id="prodid", id_file=saved), 1)
            self.assertEqual(sanitize.run(c, check=False, prod_id="prodid", id_file=saved), 0)
            self.assertEqual(saved.read_text(), (c / "data/data/device.txt").read_text())
            (c / "data/data/device.txt").write_text("prodid")
            sanitize.run(c, check=False, prod_id="prodid", id_file=saved)
            self.assertEqual(saved.read_text(), (c / "data/data/device.txt").read_text())
            new_id = (c / "data/data/device.txt").read_text()
            self.assertNotEqual(new_id, "prodid")
            self.assertRegex(new_id, "^[0-9a-f]{32}$")
            self.assertEqual(json.loads((c / "ScheduledTasks" / "a.js").read_text()), [])
            self.assertEqual(json.loads((c / "ScheduledTasks" / "b.js").read_text()), [{"Type": "StartupTrigger"}])
            opts = (c / "data/root/default/Movies/options.xml").read_text()
            self.assertNotIn(">true<", opts)
            conf = c / "data/plugins/configurations"
            self.assertNotIn("HookConfig", (conf / "Jellyfin.Webhooks.xml").read_text())
            self.assertIn("<SeerrEnabled>false</SeerrEnabled>", (conf / "Moonfin.Server.xml").read_text())
            self.assertIn("<AutoDetectIntros>false</AutoDetectIntros>", (conf / "IntroSkipper.xml").read_text())
            self.assertIn("<PasswordLoginMode>DisableForLinkedUsersOnly</PasswordLoginMode>", (conf / "Jellyfin.Plugin.Oidc.xml").read_text())
            self.assertIn("<ServerName>Holy Grail Beta</ServerName>", (c / "system.xml").read_text())
            self.assertIn("BETA", (c / "branding.xml").read_text())
            self.assertEqual(sanitize.run(c, check=True, prod_id="prodid", id_file=saved), 0)
            self.assertEqual((c / "data/data/device.txt").read_text(), new_id)

    def test_refuses_prod_config(self):
        with self.assertRaises(SystemExit):
            sanitize.run(pathlib.Path("/srv/appdata/jellyfin"), check=True)


if __name__ == "__main__":
    unittest.main()
