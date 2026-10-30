import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None

_spec = importlib.util.spec_from_file_location("studio_cards", pathlib.Path(__file__).with_name("studio-cards.py"))
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def white_logo(w=500, h=200):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([0, 0, w - 1, h - 1], fill=(255, 255, 255, 255))
    return png(img)


def bright(img, box):
    return sum(img.crop(box).convert("L").histogram()[201:])


ENTRY = 'window.HssStudios = [{"name": "Warner Bros.", "ids": ["wb1", "wb2"]}];\nwindow.HssNetworks = [{"name": "HBO", "ids": ["hbo"]}];\n'


@unittest.skipIf(Image is None, "needs Pillow")
class Render(unittest.TestCase):
    def test_card_is_16_by_9_png(self):
        img = Image.open(io.BytesIO(sc.render(white_logo(), "HBO")))
        self.assertEqual((img.format, img.size), ("PNG", (sc.WIDTH, sc.HEIGHT)))
        self.assertEqual(sc.WIDTH * 9, sc.HEIGHT * 16)

    def test_logo_is_centred_inside_the_safe_box_on_a_dark_card(self):
        img = Image.open(io.BytesIO(sc.render(white_logo(), "HBO")))
        w, h = img.size
        self.assertGreater(bright(img, (w // 2 - 20, h // 2 - 20, w // 2 + 20, h // 2 + 20)), 1500)
        self.assertEqual(bright(img, (0, 0, w, round(h * 0.2))), 0)
        self.assertEqual(bright(img, (0, round(h * 0.8), w, h)), 0)
        self.assertEqual(bright(img, (0, 0, round(w * 0.14), h)), 0)
        self.assertLess(max(img.convert("L").getpixel((5, 5)), img.convert("L").getpixel((w - 5, h - 5))), 80)

    def test_a_tall_logo_fits_the_box_height(self):
        img = Image.open(io.BytesIO(sc.render(white_logo(300, 600), "WB")))
        w, h = img.size
        self.assertEqual(bright(img, (0, 0, w, round(h * 0.2))), 0)
        self.assertGreater(bright(img, (w // 2 - 10, round(h * 0.25), w // 2 + 10, round(h * 0.3))), 100)

    def test_without_a_logo_the_name_is_written_large(self):
        img = Image.open(io.BytesIO(sc.render(None, "Pathé")))
        w, h = img.size
        self.assertGreater(bright(img, (round(w * 0.2), round(h * 0.35), round(w * 0.8), round(h * 0.65))), 2000)
        self.assertEqual(bright(img, (0, 0, w, round(h * 0.2))), 0)

    def test_same_input_same_bytes(self):
        self.assertEqual(sc.render(white_logo(), "HBO"), sc.render(white_logo(), "HBO"))


class Pure(unittest.TestCase):
    def test_cards_from_the_entry_studios_first(self):
        self.assertEqual(sc.cards(ENTRY), [("wb1", "Warner Bros."), ("wb2", "Warner Bros."), ("hbo", "HBO")])
        self.assertEqual(sc.cards(""), [])

    def test_fingerprint_follows_logo_name_and_design(self):
        a = sc.fingerprint("t1", "HBO")
        self.assertEqual(a, sc.fingerprint("t1", "HBO"))
        self.assertNotEqual(a, sc.fingerprint("t2", "HBO"))
        self.assertNotEqual(a, sc.fingerprint(None, "HBO"))
        self.assertNotEqual(a, sc.fingerprint("t1", "HBO Max"))


class FakeJellyfin:
    def __init__(self, studios, entry=ENTRY):
        self.studios = {s["Id"]: s for s in studios}
        self.entry, self.uploads, self.fetched, self.n = entry, [], [], 0

    def api(self, method, path, body=None):
        if path == "/Plugins":
            return [{"Name": "JavaScript Injector", "Id": "js"}]
        if path == "/Plugins/js/Configuration":
            return {"CustomJavaScripts": [{"Name": "Home networks", "Script": self.entry}] if self.entry is not None else []}
        if path.startswith("/Studios?"):
            assert "Recursive=true" in path, path
            return {"Items": list(self.studios.values())}
        raise AssertionError(path)

    def fetch(self, sid, kind):
        self.fetched.append((sid, kind))
        return b"IMG:" + kind.encode() + b":" + sid.encode()

    def upload(self, sid, data):
        if sid == "boom":
            raise OSError("down")
        self.uploads.append((sid, data))
        self.n += 1
        self.studios[sid]["ImageTags"]["Primary"] = f"p{self.n}"


def studio(sid, name, logo=None, primary=None):
    tags = {}
    if logo:
        tags["Logo"] = logo
    if primary:
        tags["Primary"] = primary
    return {"Id": sid, "Name": name, "ImageTags": tags}


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name) / "state.json"
        self.originals = pathlib.Path(self.tmp.name) / "originals"

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, jf, argv=()):
        out = io.StringIO()
        code = sc.main(list(argv), api=jf.api, fetch=jf.fetch, upload=jf.upload, render=lambda logo, name: b"CARD:" + name.encode() + b":" + (logo or b"-"),
                       state_file=self.state, originals=self.originals, out=out)
        return code, out.getvalue()

    def studios(self):
        return [studio("wb1", "Warner Bros. Pictures", "l1"), studio("wb2", "Warner Bros. Entertainment"), studio("hbo", "HBO", "l3"), studio("other", "Other", "l4")]

    def test_every_card_studio_gets_a_primary_then_nothing_on_rerun(self):
        jf = FakeJellyfin(self.studios())
        code, text = self.run_main(jf)
        self.assertEqual(code, 0)
        self.assertEqual(jf.uploads, [("wb1", b"CARD:Warner Bros.:IMG:Logo:wb1"), ("wb2", b"CARD:Warner Bros.:-"), ("hbo", b"CARD:HBO:IMG:Logo:hbo")])
        self.assertEqual(text.splitlines()[-1], "3 cards uploaded, 0 unchanged")
        jf.uploads.clear()
        code, text = self.run_main(jf)
        self.assertEqual((code, jf.uploads), (0, []))
        self.assertEqual(text.splitlines()[-1], "0 cards uploaded, 3 unchanged")

    def test_a_new_logo_redraws_the_card(self):
        jf = FakeJellyfin(self.studios())
        self.run_main(jf)
        jf.uploads.clear()
        jf.studios["wb2"]["ImageTags"]["Logo"] = "new"
        self.run_main(jf)
        self.assertEqual([u[0] for u in jf.uploads], ["wb2"])

    def test_a_primary_that_is_not_ours_is_kept_as_original_then_replaced(self):
        jf = FakeJellyfin([studio("wb1", "WB", "l1", primary="theirs"), studio("wb2", "WB2"), studio("hbo", "HBO")])
        self.run_main(jf)
        self.assertEqual((self.originals / "wb1").read_bytes(), b"IMG:Primary:wb1")
        self.assertEqual(sorted(p.name for p in self.originals.iterdir()), ["wb1"])
        self.assertIn("wb1", [u[0] for u in jf.uploads])

    def test_a_dropped_or_replaced_primary_is_put_back_and_the_first_original_kept(self):
        jf = FakeJellyfin([studio("wb1", "WB", "l1", primary="theirs"), studio("wb2", "WB2"), studio("hbo", "HBO")])
        self.run_main(jf)
        jf.uploads.clear()
        jf.studios["wb1"]["ImageTags"]["Primary"] = "refreshed"
        jf.studios["wb2"]["ImageTags"].pop("Primary")
        jf.fetch = lambda sid, kind: b"OTHER"
        self.run_main(jf)
        self.assertEqual(sorted(u[0] for u in jf.uploads), ["wb1", "wb2"])
        self.assertEqual((self.originals / "wb1").read_bytes(), b"IMG:Primary:wb1")

    def test_dry_run_and_check_change_nothing(self):
        jf = FakeJellyfin(self.studios())
        code, text = self.run_main(jf, ["--dry-run"])
        self.assertEqual((code, jf.uploads, self.state.exists()), (0, [], False))
        self.assertEqual(text.splitlines()[-1], "3 cards to upload, 0 unchanged")
        code, text = self.run_main(jf, ["--check"])
        self.assertEqual((code, jf.uploads, text.splitlines()[-1]), (1, [], "3 cards missing"))
        self.run_main(jf)
        code, text = self.run_main(jf, ["--check"])
        self.assertEqual((code, text.splitlines()[-1]), (0, "ok 3 cards"))

    def test_check_needs_no_state_only_a_primary_on_every_card_studio(self):
        jf = FakeJellyfin(self.studios())
        self.run_main(jf)
        self.state.unlink()
        self.assertEqual(self.run_main(jf, ["--check"])[0], 0)
        jf.studios["hbo"]["ImageTags"].pop("Primary")
        self.assertEqual(self.run_main(jf, ["--check"]), (1, "1 cards missing\n"))

    def test_a_failed_upload_is_logged_and_retried_next_run(self):
        jf = FakeJellyfin([studio("boom", "Boom"), studio("hbo", "HBO")], entry='window.HssStudios = [{"name": "Boom", "ids": ["boom"]}];\nwindow.HssNetworks = [{"name": "HBO", "ids": ["hbo"]}];\n')
        code, text = self.run_main(jf)
        self.assertEqual(code, 0)
        self.assertIn("card failed: Boom: OSError", text)
        self.assertNotIn("boom", json.loads(self.state.read_text()))
        self.assertEqual([u[0] for u in jf.uploads], ["hbo"])

    def test_a_card_id_jellyfin_no_longer_has_is_skipped(self):
        jf = FakeJellyfin([studio("hbo", "HBO")])
        code, text = self.run_main(jf)
        self.assertEqual([u[0] for u in jf.uploads], ["hbo"])

    def test_without_the_entry_nothing_happens(self):
        jf = FakeJellyfin(self.studios(), entry=None)
        code, text = self.run_main(jf)
        self.assertEqual((code, jf.uploads), (0, []))

    def test_unknown_arguments(self):
        self.assertEqual(self.run_main(FakeJellyfin([]), ["--nope"])[0], 2)


if __name__ == "__main__":
    unittest.main()
