import importlib.util
import io
import pathlib
import unittest

HAVE_PIL = importlib.util.find_spec("PIL") is not None
if HAVE_PIL:
    from PIL import Image
    import trending_art as ta


def img(color=(90, 120, 160), size=(600, 900)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG", quality=95)
    return b.getvalue()


def load(data):
    return Image.open(io.BytesIO(data))


def mean(im, box):
    c = im.convert("RGB").crop(box)
    px = list(zip(*[iter(c.tobytes())] * 3))
    return tuple(sum(p[i] for p in px) / len(px) for i in range(3))


@unittest.skipUnless(HAVE_PIL, "needs Pillow (python3-pil), as test_studio_logos does")
class Art(unittest.TestCase):
    def test_poster_size_and_jpeg(self):
        for badge in (None, "found", "requested", "downloading", "none"):
            im = load(ta.poster(img(size=(500, 700)), badge, "Title"))
            self.assertEqual((im.format, im.size), ("JPEG", (600, 900)), badge)

    def test_none_badge_draws_red_banner(self):
        im = load(ta.poster(img(), "none", "Title"))
        r, g, b = mean(im, (270, 410, 330, 490))
        self.assertGreater(r, 150)
        self.assertLess(g, 80)
        self.assertLess(b, 80)

    def test_found_badge_only_top_right(self):
        base = Image.new("RGB", (600, 900), (90, 120, 160))
        im = load(ta.poster(img(), "found", "Title")).convert("RGB")
        for x, y in ((10, 10), (300, 10), (449, 100), (300, 450), (590, 500), (590, 890), (10, 890), (460, 230)):
            d = max(abs(a - c) for a, c in zip(im.getpixel((x, y)), base.getpixel((x, y))))
            self.assertLess(d, 12, (x, y))
        self.assertGreater(max(abs(a - c) for a, c in zip(im.getpixel((540, 55)), (90, 120, 160))), 30)

    def test_no_base_makes_title_poster(self):
        im = load(ta.poster(None, None, "A Very Long Movie Title That Wraps"))
        self.assertEqual(im.size, (600, 900))
        self.assertGreater(len(set(im.convert("L").tobytes())), 20)

    def test_collection_poster_size(self):
        self.assertEqual(load(ta.collection_poster("Netflix · Movies", None)).size, (600, 900))
        logo = io.BytesIO()
        Image.new("RGBA", (300, 100), (255, 255, 255, 255)).save(logo, "PNG")
        self.assertEqual(load(ta.collection_poster("Netflix · Movies", logo.getvalue())).size, (600, 900))

    def test_fanart_and_tile(self):
        self.assertIsNone(ta.fanart(None))
        self.assertIsNone(ta.fanart(b"not an image"))
        self.assertEqual(load(ta.fanart(img(size=(400, 300)))).format, "JPEG")
        self.assertEqual(load(ta.library_tile("Collections & Trending")).size, (1280, 720))


@unittest.skipUnless(HAVE_PIL, "needs Pillow (python3-pil), as test_studio_logos does")
class Clips(unittest.TestCase):
    def test_every_clip_frame_renders_at_1080p(self):
        spec = importlib.util.spec_from_file_location("trending_clips", pathlib.Path(__file__).with_name("trending-clips.py"))
        tc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tc)
        self.assertEqual(set(tc.TEXTS), {"now", "night", "unavailable", "requested", "coming"})
        for name in tc.TEXTS:
            self.assertEqual(tc.frame(name, 10).size, (1920, 1080), name)


if __name__ == "__main__":
    unittest.main()
