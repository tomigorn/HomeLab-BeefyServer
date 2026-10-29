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

    def test_badges_are_top_left_pills_clear_of_the_client_corner(self):
        base = Image.new("RGB", (600, 900), (90, 120, 160))
        for badge in ("found", "requested", "downloading"):
            im = load(ta.poster(img(), badge, "Title")).convert("RGB")
            for x, y in ((590, 10), (480, 60), (590, 120), (300, 450), (590, 890), (10, 890)):
                d = max(abs(a - c) for a, c in zip(im.getpixel((x, y)), base.getpixel((x, y))))
                self.assertLess(d, 12, (badge, x, y))
            self.assertLess(sum(im.getpixel((30, 60))), sum(base.getpixel((30, 60))) - 60, badge)

    def test_no_base_makes_title_poster(self):
        im = load(ta.poster(None, None, "A Very Long Movie Title That Wraps"))
        self.assertEqual(im.size, (600, 900))
        self.assertGreater(len(set(im.convert("L").tobytes())), 20)

    def test_collection_poster_size_and_service_colour(self):
        logo = io.BytesIO()
        Image.new("RGBA", (300, 300), (255, 255, 255, 255)).save(logo, "PNG")
        posters = [img((250, 250, 250)), img((0, 200, 0)), img((0, 0, 200)), img((200, 200, 0))]
        nf = load(ta.collection_poster("service-8-movie", "Netflix · Movies", logo.getvalue(), posters))
        pv = load(ta.collection_poster("service-119-tv", "Prime Video · Shows", None, posters[:1]))
        bare = load(ta.collection_poster("genre-drama-movie", "Drama · Movies", None, []))
        for im in (nf, pv, bare):
            self.assertEqual((im.format, im.size), ("JPEG", (600, 900)))
        r, g, b = mean(nf, (560, 0, 600, 40))
        self.assertGreater(r, g + 40)
        r, g, b = mean(pv, (560, 0, 600, 40))
        self.assertGreater(b, r + 40)
        self.assertNotEqual(mean(nf, (560, 0, 600, 40)), mean(bare, (560, 0, 600, 40)))

    def test_collection_poster_fans_the_member_posters(self):
        im = load(ta.collection_poster("coming", "Coming soon", None, [img((250, 250, 250))]))
        self.assertGreater(min(mean(im, (270, 720, 330, 780))), 200)
        empty = load(ta.collection_poster("coming", "Coming soon", None, []))
        self.assertLess(min(mean(empty, (270, 720, 330, 780))), 200)

    def test_collection_poster_keeps_the_top_right_corner_plain(self):
        im = load(ta.collection_poster("genre-thriller-tv", "Thriller & Crime · Shows", None, [img()] * 4)).convert("L").crop((480, 0, 600, 120))
        self.assertLess(max(im.getextrema()) - min(im.getextrema()), 40)

    def test_collection_backdrop(self):
        logo = io.BytesIO()
        Image.new("RGBA", (300, 300), (255, 255, 255, 255)).save(logo, "PNG")
        bright = img((240, 240, 240), (1280, 720))
        im = load(ta.collection_backdrop("service-8-movie", "Netflix · Movies", logo.getvalue(), bright, [img()]))
        self.assertEqual((im.format, im.size), ("JPEG", (1920, 1080)))
        self.assertLess(sum(mean(im, (60, 700, 400, 1000))), 3 * 160)
        self.assertGreater(sum(mean(im, (1600, 400, 1900, 600))), 3 * 120)
        self.assertEqual(load(ta.collection_backdrop("coming", "Coming soon", None, None, [])).size, (1920, 1080))
        self.assertEqual(load(ta.collection_backdrop("coming", "Coming soon", None, b"not an image", [img()])).size, (1920, 1080))

    def test_fanart_and_tile(self):
        self.assertIsNone(ta.fanart(None))
        self.assertIsNone(ta.fanart(b"not an image"))
        self.assertEqual(load(ta.fanart(img(size=(400, 300)))).format, "JPEG")
        self.assertEqual(load(ta.library_tile("Collections & Trending")).size, (1280, 720))
        tile = load(ta.library_tile("Collections & Trending", [img((250, 250, 250))] * 5))
        self.assertEqual(tile.size, (1280, 720))
        self.assertGreater(min(mean(tile, (900, 330, 960, 390))), 200)


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
