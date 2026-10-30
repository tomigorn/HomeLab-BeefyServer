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


def logo_png(color):
    b = io.BytesIO()
    Image.new("RGBA", (200, 200), color).save(b, "PNG")
    return b.getvalue()


if HAVE_PIL:
    HOME1, HOME10 = ("home-movies", 1, None), ("home-movies", 10, None)
    HORROR3, COMEDY7 = ("genre-horror-movie", 3, None), ("genre-comedy-movie", 7, None)
    NETFLIX5 = ("service-8-movie", 5, logo_png((200, 10, 20, 255)))


def load(data):
    return Image.open(io.BytesIO(data))


def rgb_pixels(im):
    return list(zip(*[iter(im.convert("RGB").tobytes())] * 3))


def mean(im, box):
    px = rgb_pixels(im.crop(box))
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

    def test_hidden_gems_art_is_emerald_with_a_gem_mark(self):
        im = load(ta.collection_poster("gems", "Hidden Gems", None, [img((250, 250, 250))] * 3)).convert("RGB")
        r, g, b = mean(im, (560, 0, 600, 40))
        self.assertGreater(g, r + 40)
        self.assertGreater(min(mean(im, (270, 720, 330, 780))), 200)
        m = ta.mark("gems", None, 132)
        self.assertEqual(m.size, (132, 132))
        self.assertTrue(any(bb > 150 and bb > rr + 40 for rr, gg, bb in rgb_pixels(m.convert("RGB").crop((30, 30, 100, 100)))))
        self.assertEqual(load(ta.collection_backdrop("gems", "Hidden Gems", None, None, [img()])).size, (1920, 1080))

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

    def test_mark_stack_top_right_below_the_client_corner_and_clear_of_the_pill(self):
        base = Image.new("RGB", (600, 900), (90, 120, 160))
        for marks in ([HOME1], [HOME10, HORROR3], [HOME1, HORROR3, NETFLIX5, COMEDY7]):
            boxes = ta.mark_boxes([m[1] for m in marks])
            self.assertEqual(len(boxes), len(marks))
            for (x0, y0, x1, y1), below in zip(boxes, boxes[1:] + [None]):
                self.assertGreater(y0, 120)
                self.assertLessEqual(x1, 580)
                self.assertGreater(x0, 300)
                self.assertGreaterEqual(y1 - y0, 100)
                if below:
                    self.assertGreater(below[1], y1)
            self.assertLess(boxes[-1][3], 700)
            for badge in (None, "found", "downloading"):
                im = load(ta.poster(img(), badge, "Title", marks)).convert("RGB")
                for x, y in ((590, 10), (480, 60), (590, 120), (200, 450), (590, 890), (10, 890)):
                    d = max(abs(a - c) for a, c in zip(im.getpixel((x, y)), base.getpixel((x, y))))
                    self.assertLess(d, 12, (marks, badge, x, y))
                for x0, y0, x1, y1 in boxes:
                    self.assertGreater(len(set(rgb_pixels(im.crop((x0, y0, x1, y1))))), 30, (marks, badge))
                if badge:
                    self.assertLess(sum(im.getpixel((30, 60))), sum(base.getpixel((30, 60))) - 60, badge)

    def test_more_than_four_marks_add_a_plus_pill(self):
        marks = [HOME1, HORROR3, NETFLIX5, COMEDY7, ("genre-drama-movie", 9, None), ("genre-action-movie", 10, None)]
        self.assertEqual(ta.shown_marks(marks), (marks[:4], 2))
        self.assertEqual(ta.shown_marks(marks[:4]), (marks[:4], 0))
        x0, y0, x1, y1 = ta.plus_box(2, 4)
        self.assertGreater(y0, ta.mark_boxes([1, 3, 5, 7])[-1][3])
        self.assertLess(y1, 760)
        im = load(ta.poster(img((0, 0, 0)), None, "Title", marks)).convert("L")
        self.assertGreater(im.crop((x0, y0, x1, y1)).getextrema()[1], 200)
        self.assertNotEqual(ta.poster(img(), None, "Title", marks), ta.poster(img(), None, "Title", marks[:4]))

    def test_mark_icons_differ_per_list_and_genres_are_colour_emoji(self):
        icons = {k: ta.mark_icon(k, logo, 80) for k, _, logo in (HOME1, HORROR3, NETFLIX5, COMEDY7)}
        for k, ic in icons.items():
            self.assertEqual(ic.size, (80, 80), k)
            self.assertGreater(ic.getchannel("A").getextrema()[1], 200, k)
        self.assertEqual(len({ic.tobytes() for ic in icons.values()}), 4)
        self.assertEqual(set(ta.MARK_EMOJI), {"action", "animation", "comedy", "documentary", "drama", "family", "horror", "romance", "scifi", "thriller"})
        self.assertEqual(ta.mark_icon("genre-horror-movie", None, 80).tobytes(), ta.mark_icon("genre-horror-tv", None, 80).tobytes())
        comedy = ta.mark_icon("genre-comedy-movie", None, 80).convert("RGB")
        self.assertTrue(any(r > 200 and g > 150 and b < 90 for r, g, b in rgb_pixels(comedy)))
        netflix = ta.mark_icon("service-8-movie", None, 80).convert("RGB")
        self.assertTrue(any(r > 150 and g < 60 and b < 60 for r, g, b in rgb_pixels(netflix)))

    def test_mark_number_large_enough_for_a_tv_row(self):
        im = load(ta.poster(img((0, 0, 0)), None, "Title", [HOME1])).convert("L")
        x0, y0, x1, y1 = ta.mark_boxes([1])[0]
        rows = [y for y in range(y0, y1) if any(im.getpixel((x, y)) > 200 for x in range(x0 + ta.MARK_ICON + 20, x1))]
        self.assertGreaterEqual(rows[-1] - rows[0], 52)

    def test_mark_pill_widens_with_the_number_and_no_marks_leave_the_poster_unchanged(self):
        self.assertGreater(ta.mark_boxes([1])[0][0] - ta.mark_boxes([10])[0][0], 10)
        self.assertEqual(ta.mark_boxes([1])[0][2], ta.mark_boxes([10])[0][2])
        for badge in (None, "found", "none"):
            self.assertEqual(ta.poster(img(), badge, "Title", []), ta.poster(img(), badge, "Title"))

    def test_unavailable_title_poster_keeps_the_title_clear_of_the_banner_and_marks(self):
        title = "A Very Long Unmatched Movie Title That Wraps Around"
        for marks in ([], [HOME1, HORROR3, NETFLIX5, COMEDY7, HOME10]):
            im = load(ta.poster(None, "none", title, marks)).convert("RGB")
            x0, y0, x1, y1 = ta.title_box("none", len(marks))
            boxes = ta.mark_boxes([m[1] for m in marks[:4]]) + ([ta.plus_box(len(marks) - 4, 4)] if len(marks) > 4 else [])
            for bx0, by0, bx1, by1 in boxes:
                self.assertTrue(bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1)
            px = rgb_pixels(im.crop((x0, y0, x1, y1)))
            self.assertEqual(sum(1 for r, g, b in px if r > 150 and g < 80 and b < 80), 0, marks)
            self.assertGreater(sum(1 for p in px if min(p) > 230), 500, marks)
            red = sum(1 for r, g, b in rgb_pixels(im) if r > 150 and g < 80 and b < 80)
            self.assertGreater(red, 40000, marks)

    def test_marked_unavailable_poster_keeps_the_band_off_the_marks_and_the_title_art(self):
        for marks in ([HOME1], [HOME10, HORROR3, NETFLIX5, COMEDY7]):
            im = load(ta.poster(img(), "none", "Title", marks)).convert("RGB")
            for x0, y0, x1, y1 in ta.mark_boxes([m[1] for m in marks]):
                px = rgb_pixels(im.crop((x0 + ta.MARK_ICON + 20, y0, x1, y1)))
                self.assertEqual(sum(1 for r, g, b in px if r > 150 and g < 80 and b < 80), 0, marks)
            self.assertGreater(sum(1 for r, g, b in rgb_pixels(im) if r > 150 and g < 80 and b < 80), 40000, marks)
            px = rgb_pixels(im.crop((170, 720, 580, 880)))
            self.assertEqual(sum(1 for r, g, b in px if r > 150 and g < 80 and b < 80), 0, marks)

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
