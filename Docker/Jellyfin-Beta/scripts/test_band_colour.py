import colorsys
import io
import unittest

from PIL import Image

import band_colour as bc


def solid(rgb, size=(600, 900)):
    return Image.new("RGB", size, rgb)


class Colour(unittest.TestCase):
    def test_complement_turns_the_hue_half_way(self):
        h, _, _ = colorsys.rgb_to_hls(*[c / 255 for c in bc.complement((200, 30, 30))])
        self.assertAlmostEqual(h, 0.5, delta=0.02)

    def test_complement_is_vivid_but_not_neon_even_for_grey(self):
        for src in ((128, 128, 128), (5, 5, 5), (250, 250, 250), (255, 0, 0)):
            _, l, s = colorsys.rgb_to_hls(*[c / 255 for c in bc.complement(src)])
            self.assertTrue(bc.LIGHT[0] - 0.01 <= l <= bc.LIGHT[1] + 0.01, (src, l))
            self.assertLessEqual(s, bc.SAT[1] + 0.01)

    def test_text_is_black_or_white_with_contrast_4_5(self):
        for bg in ((255, 255, 0), (20, 20, 120), (0, 160, 160), (200, 60, 200), (128, 128, 128)):
            fg = bc.text_colour(bg)
            self.assertIn(fg, ((0, 0, 0), (255, 255, 255)))
            self.assertGreaterEqual(bc.contrast(fg, bg), 4.5, bg)

    def test_dominant_reads_the_part_above_the_band(self):
        im = solid((10, 200, 10))
        im.paste((255, 255, 255), (0, 900 - 128, 600, 900))
        self.assertEqual(bc.dominant(im, 128), (10, 200, 10))


    def test_mid_tones_get_black_text_on_a_lighter_band_not_a_muddy_one(self):
        bg, fg = bc.colours(solid((40, 60, 160)), 128)
        _, l, s = colorsys.rgb_to_hls(*[c / 255 for c in bg])
        self.assertGreaterEqual(bc.contrast(fg, bg), 4.5)
        self.assertGreaterEqual(s, 0.5)
        self.assertGreaterEqual(l, 0.38)


class Render(unittest.TestCase):
    def test_band_covers_the_old_band_in_the_complement(self):
        im = solid((200, 30, 30))
        im.paste((8, 8, 12), (0, 900 - 128, 600, 900))
        out = Image.open(io.BytesIO(bc.render(bc.to_bytes(im), 128)))
        bg = bc.complement((200, 30, 30))
        px = out.convert("RGB").getpixel((590, 900 - 20))
        self.assertTrue(all(abs(a - b) < 12 for a, b in zip(px, bg)), (px, bg))
        self.assertTrue(all(abs(a - b) < 12 for a, b in zip(out.convert("RGB").getpixel((300, 300)), (200, 30, 30))))

    def test_classic_style_leaves_the_image(self):
        data = bc.to_bytes(solid((1, 2, 3)))
        self.assertEqual(bc.render(data, 128, style="classic"), data)


if __name__ == "__main__":
    unittest.main()
