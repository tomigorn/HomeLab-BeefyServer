"""The "Not downloaded yet" band in a colour of its own poster: the complement of the poster's dominant colour above the band, kept vivid but not neon, black or white text by WCAG contrast (>= 4.5:1), a thin separator line on top.
BAND_STYLE: "complement" (this) or "classic" (prod's dark band with the amber edge, image left as it is)."""
import colorsys
import io

from PIL import Image, ImageDraw, ImageFont

VERSION = 2  # bump when the colour rule changes: cached web colours are recomputed
TEXT = "Not downloaded yet"
SAT = (0.55, 0.80)
LIGHT = (0.38, 0.58)
LINE = 4
FONT = "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Bold.ttf"


def to_bytes(im, quality=88):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def dominant(im, band_h):
    """Most common colour (5-colour quantisation) of the lower third above the band."""
    w, h = im.size
    region = im.convert("RGB").crop((0, max(0, h - band_h - h // 3), w, h - band_h)).resize((96, 48))
    q = region.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    count, idx = max(q.getcolors())
    return tuple(pal[idx * 3:idx * 3 + 3])


def complement(rgb):
    h, l, s = colorsys.rgb_to_hls(*[c / 255 for c in rgb])
    h = (h + 0.5) % 1.0
    s = min(max(s, SAT[0]), SAT[1])
    l = min(max(l, LIGHT[0]), LIGHT[1])
    return tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, l, s))


def luminance(rgb):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_colour(bg):
    black, white = (0, 0, 0), (255, 255, 255)
    return black if contrast(black, bg) >= contrast(white, bg) else white


def colours(im, band_h):
    """(band background, text) for this poster or thumb."""
    bg = complement(dominant(im, band_h))
    fg = text_colour(bg)
    if contrast(fg, bg) < 4.5:
        # Mid tones: black text on a lighter band keeps the colour vivid; darkening for white text turns it muddy.
        fg = (0, 0, 0)
        h, l, s = colorsys.rgb_to_hls(*[c / 255 for c in bg])
        while contrast(fg, bg) < 4.5 and l < 0.95:
            l += 0.02
            bg = tuple(round(c * 255) for c in colorsys.hls_to_rgb(h, l, s))
    return bg, fg


def hex_(rgb):
    return "#%02x%02x%02x" % rgb


def font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def fitted(text, room, biggest):
    for size in range(biggest, 20, -2):
        f = font(size)
        if f.getlength(text) <= room:
            return f
    return font(20)


def render(data, band_h, style="complement"):
    """The poster or thumb with its bottom band_h pixels redrawn as the complement band (covers the classic band completely)."""
    if style == "classic":
        return data
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    bg, fg = colours(im, band_h)
    d = ImageDraw.Draw(im)
    y0 = h - band_h
    d.rectangle((0, y0, w, h), fill=bg)
    line = tuple(round(f * 0.55 + b * 0.45) for f, b in zip(fg, bg))
    d.rectangle((0, y0, w, y0 + LINE - 1), fill=line)
    cy = y0 + LINE + (band_h - LINE) // 2
    r = round(band_h * 0.27)
    cx = 24 + r
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fg)
    s = round(r * 1.18)
    sh, hw = s * 0.28, s * 0.5
    d.polygon([(cx - sh / 2, cy - hw), (cx + sh / 2, cy - hw), (cx + sh / 2, cy), (cx + hw * 0.9, cy), (cx, cy + hw), (cx - hw * 0.9, cy), (cx - sh / 2, cy)], fill=bg)
    d.text((cx + r + 18, cy + 1), TEXT, font=fitted(TEXT, w - cx - r - 18 - 24, round(band_h * 0.55)), fill=fg, anchor="lm")
    return to_bytes(im)
