"""Art for the trending library: posters with a state badge, collection posters and backdrops, the library tile. Pillow only; icons are drawn shapes."""
import io
import math
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
W, H = 600, 900
BW, BH = 1920, 1080
WHITE = (255, 255, 255)
RED = (200, 24, 24)
GREEN = (34, 160, 80)
BLUE = (37, 110, 230)
DARK = (20, 22, 28)
PILL = (10, 10, 14, 205)
FLAG_RED = (218, 41, 28)
RANK_TOP, RANK_H, RANK_FLAG, RANK_FONT = 132, 84, 56, 48
SERVICES = {
    "8": ("#c20a14", "#1a0002"),
    "119": ("#0091c9", "#03182a"),
    "337": ("#1b3fd0", "#060b2e"),
    "350": ("#5b6472", "#0b0b0d"),
    "210": ("#0a6cd6", "#2a0a55"),
    "691": ("#d6143e", "#2e030d"),
    "531": ("#0a5cff", "#00113a"),
    "150": ("#2b6cf0", "#071840"),
}
GENRES = {
    "action": ("#ea580c", "#300e03"),
    "animation": ("#16a34a", "#04240f"),
    "comedy": ("#d29a06", "#3a1d04"),
    "documentary": ("#0d9488", "#032826"),
    "drama": ("#9333ea", "#250b52"),
    "family": ("#e0457f", "#430b26"),
    "horror": ("#8b1a1a", "#080000"),
    "romance": ("#e11d48", "#380512"),
    "scifi": ("#0891b2", "#062a42"),
    "thriller": ("#55657a", "#0a0f1c"),
}
HOME = ("#d52b1e", "#3a0505")
COMING = ("#0284c7", "#1b1846")
OTHER = ("#6d4bb8", "#140f2a")
TILE = ("#3b2f8f", "#0a0918")


def font(size, path=None):
    try:
        return ImageFont.truetype(path or SANS, size)
    except OSError:
        return ImageFont.truetype(BOLD, size)


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def to_jpeg(im, quality=88):
    out = io.BytesIO()
    im.convert("RGB").save(out, "JPEG", quality=quality)
    return out.getvalue()


def cover(im, size):
    w, h = size
    s = max(w / im.width, h / im.height)
    im = im.resize((max(w, round(im.width * s)), max(h, round(im.height * s))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def gradient(size, top, bottom):
    w, h = size
    col = Image.new("RGB", (1, h))
    col.putdata([tuple(round(a + (b - a) * y / max(h - 1, 1)) for a, b in zip(top, bottom)) for y in range(h)])
    return col.resize((w, h))


def diagonal(size, start, end):
    """Gradient from the top left (start) to the bottom right (end)."""
    w, h = size
    vert = Image.linear_gradient("L").resize((w, h))
    horiz = Image.linear_gradient("L").rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT).resize((w, h))
    mask = Image.blend(horiz, vert, 0.68)
    return Image.composite(Image.new("RGB", size, end), Image.new("RGB", size, start), mask)


def palette(key):
    m = re.match(r"service-(\d+)-", key or "")
    if m:
        return tuple(map(rgb, SERVICES.get(m.group(1), OTHER)))
    m = re.match(r"genre-([a-z]+)-", key or "")
    if m:
        return tuple(map(rgb, GENRES.get(m.group(1), OTHER)))
    if (key or "").startswith("home-"):
        return tuple(map(rgb, HOME))
    if key == "coming":
        return tuple(map(rgb, COMING))
    return tuple(map(rgb, OTHER))


def headline(key, name):
    """(big, small) text of a collection: "Netflix · Movies" → ("Netflix", "MOVIES"), "Top 10 Switzerland · Shows" → ("Top 10", "SWITZERLAND · SHOWS")."""
    head, _, kind = name.partition(" · ")
    if (key or "").startswith("home-") and head.startswith("Top 10"):
        rest = head[len("Top 10"):].strip()
        return "Top 10", " · ".join(x for x in (rest, kind) if x).upper()
    return head, kind.upper()


def wrap(draw, text, fnt, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=fnt) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def fit(draw, text, width, size, smallest, max_lines):
    while True:
        fnt = font(size)
        lines = wrap(draw, text, fnt, width)
        if size <= smallest or (len(lines) <= max_lines and all(draw.textlength(ln, font=fnt) <= width for ln in lines)):
            return fnt, lines, size
        size -= 2


def draw_text_block(draw, text, box, size, fill=WHITE):
    x0, y0, x1, y1 = box
    while size > 14:
        fnt = font(size)
        lines = wrap(draw, text, fnt, x1 - x0)
        total = len(lines) * round(size * 1.2)
        if total <= y1 - y0 and all(draw.textlength(ln, font=fnt) <= x1 - x0 for ln in lines):
            break
        size -= 2
    y = y0 + (y1 - y0 - total) // 2
    for ln in lines:
        draw.text(((x0 + x1) // 2, y), ln, font=fnt, fill=fill, anchor="mt")
        y += round(size * 1.2)


def tracked(draw, xy, text, fnt, fill, spacing):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + spacing
    return x


def shadowed_text(im, xy, lines, fnt, line_h, fill=WHITE, blur=10, alpha=170):
    shadow = Image.new("L", im.size, 0)
    sd = ImageDraw.Draw(shadow)
    x, y = xy
    for i, ln in enumerate(lines):
        sd.text((x, y + 4 + i * line_h), ln, font=fnt, fill=alpha)
    im.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(blur)))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((x, y + i * line_h), ln, font=fnt, fill=fill)


def decode(data):
    if not data:
        return None
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None


def rounded(im, radius):
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=radius, fill=255)
    out = im.convert("RGBA")
    out.putalpha(mask)
    return out


def card(poster, w, h, angle, radius=12):
    """A rounded poster card with a soft drop shadow, rotated; RGBA with room for the shadow."""
    pad = 40
    face = rounded(cover(poster, (w, h)), radius)
    layer = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("L", layer.size, 0)
    ImageDraw.Draw(sh).rounded_rectangle((pad, pad + 14, pad + w, pad + h + 14), radius=radius, fill=190)
    layer.putalpha(sh.filter(ImageFilter.GaussianBlur(16)))
    layer = Image.alpha_composite(layer, Image.new("RGBA", layer.size, (0, 0, 0, 0)))
    layer.paste(face, (pad, pad), face)
    ImageDraw.Draw(layer).rounded_rectangle((pad, pad, pad + w - 1, pad + h - 1), radius=radius, outline=(255, 255, 255, 70), width=2)
    return layer.rotate(angle, expand=True, resample=Image.BICUBIC)


def fan(im, posters, cx, top, w):
    """Up to three posters fanned around cx: the first in front, the second left, the third right."""
    pics = [p for p in (decode(b) for b in posters) if p][:3]
    if not pics:
        return
    h = round(w * 1.5)
    sw = round(w * 0.84)
    sh = round(sw * 1.5)
    slots = {1: [(0, 0, 0, w, h)], 2: [(-w * 0.32, 26, 8, sw, sh), (w * 0.2, 0, -3, w, h)], 3: [(-w * 0.55, 40, 9, sw, sh), (w * 0.55, 40, -9, sw, sh), (0, 0, 0, w, h)]}[len(pics)]
    order = {1: [0], 2: [1, 0], 3: [1, 2, 0]}[len(pics)]
    for (dx, dy, ang, cw, ch), i in zip(slots, order):
        c = card(pics[i], cw, ch, ang)
        im.paste(c, (round(cx + dx - c.width / 2), round(top + dy + ch / 2 - c.height / 2)), c)


def mark(key, logo, size):
    """The service logo, a Swiss cross for Top 10, a calendar for Coming soon; None for genres."""
    if logo:
        lg = decode_rgba(logo)
        if lg is not None:
            tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            lg = cover(lg, (size, size)) if abs(lg.width / lg.height - 1) < 0.15 else contain_on(lg, size)
            tile.paste(lg, (0, 0), lg)
            return rounded(tile, round(size * 0.2))
    s = size
    tile = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    if (key or "").startswith("home-"):
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=round(s * 0.2), fill=(218, 41, 28, 255))
        a, b = s * 0.19, s * 0.4
        d.rectangle((s / 2 - a / 2 * 1.0, s / 2 - b, s / 2 + a / 2, s / 2 + b), fill=WHITE)
        d.rectangle((s / 2 - b, s / 2 - a / 2, s / 2 + b, s / 2 + a / 2), fill=WHITE)
        return tile
    if key == "coming":
        d.rounded_rectangle((0, 0, s - 1, s - 1), radius=round(s * 0.2), fill=(255, 255, 255, 255))
        d.rounded_rectangle((0, 0, s - 1, s * 0.3), radius=round(s * 0.2), fill=(2, 132, 199, 255))
        d.rectangle((0, s * 0.18, s - 1, s * 0.3), fill=(2, 132, 199, 255))
        for i in range(3):
            for j in range(3):
                x, y = s * (0.2 + 0.24 * j), s * (0.42 + 0.17 * i)
                d.rounded_rectangle((x, y, x + s * 0.14, y + s * 0.1), radius=3, fill=(2, 132, 199, 255) if (i, j) == (1, 1) else (190, 200, 215, 255))
        return tile
    return None


def decode_rgba(data):
    try:
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None


def contain_on(lg, size):
    s = min(size * 0.82 / lg.width, size * 0.82 / lg.height)
    lg = lg.resize((max(1, round(lg.width * s)), max(1, round(lg.height * s))), Image.LANCZOS)
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    tile.paste(lg, ((size - lg.width) // 2, (size - lg.height) // 2), lg)
    return tile


def paste_mark(im, m, xy):
    if m is None:
        return
    sh = Image.new("L", im.size, 0)
    x, y = xy
    ImageDraw.Draw(sh).rounded_rectangle((x, y + 8, x + m.width, y + m.height + 8), radius=round(m.width * 0.2), fill=150)
    im.paste((0, 0, 0), (0, 0), sh.filter(ImageFilter.GaussianBlur(12)))
    im.paste(m, xy, m)


def heading(im, key, name, logo, x, y, width, big, mark_size):
    """Mark, big name and the small kind line from (x, y); returns the y below it."""
    d = ImageDraw.Draw(im)
    head, small = headline(key, name)
    m = mark(key, logo, mark_size)
    if m is not None:
        paste_mark(im, m, (x, y))
        y += mark_size + round(big * 0.3)
    elif small:
        sf = font(round(big * 0.36))
        tracked(d, (x, y), small, sf, (236, 236, 240), round(big * 0.05))
        y += round(big * 0.7)
        small = ""
    fnt, lines, size = fit(d, head, width, big, round(big * 0.55), 2)
    line_h = round(size * 1.04)
    shadowed_text(im, (x - 3, y), lines, fnt, line_h)
    y += line_h * len(lines)
    if small:
        sf = font(round(big * 0.36))
        y += round(big * 0.1)
        tracked(ImageDraw.Draw(im), (x, y), small, sf, (236, 236, 240), round(big * 0.05))
        y += round(big * 0.42)
    return y


def collection_poster(key, name, logo, posters):
    """2:3 poster of a list: its colour, the logo, the name large and the first posters fanned below. The top right stays plain for the client's corner badge."""
    c1, c2 = palette(key)
    im = diagonal((W, H), c1, c2)
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((-200, -260, 520, 380), fill=38)
    im.paste((255, 255, 255), (0, 0), glow.filter(ImageFilter.GaussianBlur(90)))
    bottom = heading(im, key, name, logo, 46, 46 if mark(key, logo, 10) is not None else 64, W - 92, 100, 132)
    fan(im, posters, W // 2, min(max(430, bottom + 34), 520), 268)
    return to_jpeg(im, 90)


def collection_backdrop(key, name, logo, backdrop, posters):
    """16:9 backdrop of a list: the first title's backdrop, darkened towards the left, with the logo and name; the list colour and fanned posters when there is no backdrop."""
    c1, c2 = palette(key)
    base = decode(backdrop)
    if base is not None:
        im = cover(base, (BW, BH))
        im = Image.blend(im, Image.new("RGB", im.size, (0, 0, 0)), 0.12)
    else:
        im = diagonal((BW, BH), c1, c2)
        fan(im, posters, 1440, 250, 420)
    shade = Image.new("L", (BW, 1))
    shade.putdata([max(0, round(245 * (1 - x / 1300) ** 1.4)) if x < 1300 else 0 for x in range(BW)])
    im.paste(c2, (0, 0), shade.resize((BW, BH)))
    low = Image.new("L", (1, BH))
    low.putdata([max(0, round(200 * ((y - 560) / 520) ** 1.5)) if y > 560 else 0 for y in range(BH)])
    im.paste((0, 0, 0), (0, 0), low.resize((BW, BH)))
    heading(im, key, name, logo, 120, 130 if mark(key, logo, 10) is not None else 180, 900, 128, 168)
    return to_jpeg(im, 88)


def arrow_down(draw, cx, cy, s, fill=WHITE):
    t = s * 0.2
    draw.rectangle((cx - t, cy - s * 0.5, cx + t, cy + s * 0.05), fill=fill)
    draw.polygon([(cx - s * 0.5, cy - s * 0.02), (cx + s * 0.5, cy - s * 0.02), (cx, cy + s * 0.5)], fill=fill)


def check(draw, cx, cy, s, fill=WHITE):
    pts = [(cx - s * 0.45, cy + s * 0.02), (cx - s * 0.12, cy + s * 0.35), (cx + s * 0.48, cy - s * 0.35)]
    draw.line(pts, fill=fill, width=max(3, round(s * 0.2)), joint="curve")


def badge_pill(im, label, icon, ring, glyph):
    """Top-left label on a dark translucent pill: a coloured circle with the icon, then the text."""
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fnt = font(44)
    h, x, y = 84, 22, 22
    r = 30
    w = 14 + 2 * r + 16 + round(d.textlength(label, font=fnt)) + 28
    d.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=PILL)
    cx, cy = x + 14 + r + 2, y + h // 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ring)
    if icon == "check":
        check(d, cx, cy, 34, glyph)
    else:
        arrow_down(d, cx, cy - 2, 34, glyph)
    d.text((cx + r + 16, cy + 1), label, font=fnt, fill=WHITE, anchor="lm")
    return Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")


def badge_none(im, p0=(0, H), p1=(W, 0), size=50):
    """Red band along the line p0 to p1 with NOT ABLE TO DOWNLOAD; the default runs corner to corner."""
    ang = math.degrees(math.atan2(p0[1] - p1[1], p1[0] - p0[0]))
    band = 150
    d = ImageDraw.Draw(im)
    ln = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    nx, ny = (p0[1] - p1[1]) / ln, (p1[0] - p0[0]) / ln
    o = band / 2
    d.polygon([(p0[0] - o * nx, p0[1] - o * ny), (p0[0] + o * nx, p0[1] + o * ny), (p1[0] + o * nx, p1[1] + o * ny), (p1[0] - o * nx, p1[1] - o * ny)], fill=RED)
    for s in (-1, 1):
        e = o * s
        d.line([(p0[0] - e * nx, p0[1] - e * ny), (p1[0] - e * nx, p1[1] - e * ny)], fill=(255, 255, 255), width=4)
    layer = Image.new("RGBA", (1000, 160), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.text((500, 80), "NOT ABLE TO DOWNLOAD", font=font(size), fill=WHITE, anchor="mm")
    layer = layer.rotate(ang, expand=True, resample=Image.BICUBIC)
    cx, cy = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    im.paste(layer, (int(2 * cx - layer.width) // 2, int(2 * cy - layer.height) // 2), layer)
    return im


# Low and flat band: keeps the title of a title-only poster and the rank badge clear.
LOW_BAND = ((-60, 846), (660, 534))


def rank_box(rank):
    """Box of the rank badge: top right, below the corner where clients draw their played/unplayed marker, clear of the top-left pill."""
    w = 14 + RANK_FLAG + 14 + round(font(RANK_FONT).getlength(f"#{rank}")) + 26
    return (W - 22 - w, RANK_TOP, W - 22, RANK_TOP + RANK_H)


def flag_box(rank):
    x0, y0, _, y1 = rank_box(rank)
    fy = (y0 + y1 - RANK_FLAG) // 2
    return (x0 + 14, fy, x0 + 14 + RANK_FLAG, fy + RANK_FLAG)


def swiss_flag(d, box):
    x0, y0, x1, _ = box
    u = (x1 - x0) / 32
    cx, cy = x0 + 16 * u, y0 + 16 * u
    d.rounded_rectangle(box, radius=round(4 * u), fill=FLAG_RED, outline=WHITE, width=2)
    d.rectangle((cx - 3 * u, cy - 10 * u, cx + 3 * u, cy + 10 * u), fill=WHITE)
    d.rectangle((cx - 10 * u, cy - 3 * u, cx + 10 * u, cy + 3 * u), fill=WHITE)


def rank_badge(im, rank):
    """Swiss flag and #rank on a dark translucent pill, the rank in a Top 10 Switzerland list."""
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = rank_box(rank)
    d.rounded_rectangle((x0, y0, x1, y1), radius=RANK_H // 2, fill=PILL)
    fb = flag_box(rank)
    swiss_flag(d, fb)
    d.text((fb[2] + 14, (y0 + y1) // 2 + 1), f"#{rank}", font=font(RANK_FONT), fill=WHITE, anchor="lm")
    return Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")


def title_box(badge, rank):
    if badge != "none":
        return (50, 250, W - 50, 650)
    return (50, rank_box(rank)[3] + 24 if rank else 70, W - 50, 480)


def title_poster(title, box=(50, 250, W - 50, 650)):
    im = gradient((W, H), (45, 52, 72), (14, 16, 24))
    d = ImageDraw.Draw(im)
    draw_text_block(d, title, box, 64)
    return im


def poster(base, badge, title, rank=None):
    pic = decode(base)
    im = cover(pic, (W, H)) if pic is not None else title_poster(title, title_box(badge, rank))
    if badge == "found":
        im = badge_pill(im, "Download", "arrow", (255, 255, 255), (16, 16, 20))
    elif badge == "requested":
        im = badge_pill(im, "Requested", "check", GREEN, WHITE)
    elif badge == "downloading":
        im = badge_pill(im, "Downloading", "arrow", BLUE, WHITE)
    elif badge == "none":
        im = badge_none(im) if pic is not None and not rank else badge_none(im, *LOW_BAND, size=44)
    if rank:
        im = rank_badge(im, rank)
    return to_jpeg(im)


def fanart(base):
    if not base:
        return None
    try:
        return to_jpeg(Image.open(io.BytesIO(base)).convert("RGB"))
    except Exception:
        return None


def library_tile(name, posters=()):
    """16:9 tile of the Collections & Trending library: the name on the left, the top posters fanned on the right."""
    im = diagonal((1280, 720), rgb(TILE[0]), rgb(TILE[1]))
    pics = [p for p in posters if decode(p) is not None]
    d = ImageDraw.Draw(im)
    if not pics:
        draw_text_block(d, name, (100, 200, 1180, 520), 110)
        return to_jpeg(im)
    glow = Image.new("L", im.size, 0)
    ImageDraw.Draw(glow).ellipse((620, 60, 1280, 700), fill=90)
    im.paste((255, 255, 255), (0, 0), glow.filter(ImageFilter.GaussianBlur(120)))
    fan(im, pics, 940, 140, 290)
    fnt, lines, size = fit(d, name, 560, 104, 60, 3)
    line_h = round(size * 1.06)
    shadowed_text(im, (80, (720 - line_h * len(lines)) // 2), lines, fnt, line_h)
    return to_jpeg(im)
