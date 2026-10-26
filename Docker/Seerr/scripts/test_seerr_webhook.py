import importlib.util
import json
import pathlib
import unittest

_spec = importlib.util.spec_from_file_location("seerr_webhook", pathlib.Path(__file__).with_name("seerr-webhook.py"))
sw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sw)


class TemplateTest(unittest.TestCase):
    def test_rendered_template_is_floppy_payload(self):
        rendered = sw.template('s"1')
        for field in sw.FIELDS:
            rendered = rendered.replace("{{%s}}" % field, f"v-{field}")
        self.assertEqual(json.loads(rendered), {"secret": 's"1', "notification_type": "v-notification_type", "media_type": "v-media_type", "media_tmdbid": "v-media_tmdbid", "media_status": "v-media_status", "requestedBy_username": "v-requestedBy_username"})

    def test_body_sends_template_text(self):
        b = sw.body("s")
        self.assertEqual(b["options"]["jsonPayload"], sw.template("s"))
        self.assertEqual(b["options"]["webhookUrl"], sw.URL)
        self.assertTrue(b["enabled"])
        self.assertEqual(b["types"], 2 | 4 | 8 | 128)

    def test_target_is_floppy_on_beefy(self):
        self.assertEqual(sw.URL, "http://192.168.1.102:8012/webhook/seerr/global/")


if __name__ == "__main__":
    unittest.main()
