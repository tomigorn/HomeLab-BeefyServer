"""Art for the trending library: posters with a state badge, collection posters, the library tile. Pillow only; badges are drawn shapes plus DejaVu text."""
import io
import math

from PIL import Image, ImageDraw, ImageFont

BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 600, 900
WHITE = (255, 255, 255)
RED = (200, 24, 24)
GREEN = (30, 150, 70)
BLUE = (30, 110, 200)
DARK = (20, 22, 28)


def font(size):
    return ImageFont.truetype(BOLD, size)


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


def draw_text_block(draw, text, box, size, fill=WHITE):
    x0, y0, x1, y1 = box
    while size > 14:
        fnt = font(size)
        lines = wrap(draw, text, fnt, x1 - x0)
        total = len(lines) * round(size * 1.25)
        if total <= y1 - y0 and all(draw.textlength(ln, font=fnt) <= x1 - x0 for ln in lines):
            break
        size -= 2
    y = y0 + (y1 - y0 - total) // 2
    for ln in lines:
        draw.text(((x0 + x1) // 2, y), ln, font=fnt, fill=fill, anchor="mt")
        y += round(size * 1.25)


def arrow_down(draw, cx, cy, s, fill=WHITE):
    t = s * 0.22
    draw.rectangle((cx - t, cy - s * 0.5, cx + t, cy + s * 0.05), fill=fill)
    draw.polygon([(cx - s * 0.5, cy), (cx + s * 0.5, cy), (cx, cy + s * 0.55)], fill=fill)
    draw.rectangle((cx - s * 0.5, cy + s * 0.72, cx + s * 0.5, cy + s * 0.72 + max(2, s * 0.12)), fill=fill)


def check(draw, cx, cy, s, fill=WHITE):
    pts = [(cx - s * 0.5, cy), (cx - s * 0.15, cy + s * 0.4), (cx + s * 0.5, cy - s * 0.4)]
    draw.line(pts, fill=fill, width=max(3, round(s * 0.22)), joint="curve")


def pill(draw, cx, cy, w, h, color):
    draw.rounded_rectangle((cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2), radius=h // 2, fill=color)


def badge_found(im):
    d = ImageDraw.Draw(im)
    cx, cy, r = 540, 60, 38
    d.ellipse((cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3), fill=(255, 255, 255, 60))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(15, 15, 18))
    arrow_down(d, cx, cy - 6, 30)


def badge_pill(im, color, label, icon):
    d = ImageDraw.Draw(im)
    fnt = font(36)
    tw = d.textlength(label, font=fnt)
    w = round(tw) + 110
    cx, cy = W // 2, 835
    pill(d, cx, cy, w, 68, color)
    ix = cx - w // 2 + 46
    if icon == "check":
        check(d, ix, cy, 26)
    else:
        arrow_down(d, ix, cy - 8, 24)
    d.text((ix + 28, cy), label, font=fnt, fill=WHITE, anchor="lm")


def badge_none(im):
    ang = math.degrees(math.atan2(H, W))
    band = 150
    d = ImageDraw.Draw(im)
    nx, ny = H / math.hypot(W, H), W / math.hypot(W, H)
    o = band / 2
    d.polygon([(-o * nx, H - o * ny), (o * nx, H + o * ny), (W + o * nx, o * ny), (W - o * nx, -o * ny)], fill=RED)
    layer = Image.new("RGBA", (1000, 160), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.text((500, 80), "NOT ABLE TO DOWNLOAD", font=font(46), fill=WHITE, anchor="mm")
    layer = layer.rotate(ang, expand=True, resample=Image.BICUBIC)
    im.paste(layer, ((W - layer.width) // 2, (H - layer.height) // 2), layer)


def title_poster(title):
    im = gradient((W, H), (45, 52, 72), (14, 16, 24))
    d = ImageDraw.Draw(im)
    draw_text_block(d, title, (50, 250, W - 50, 650), 64)
    return im


def base_image(base, title):
    if base:
        try:
            return cover(Image.open(io.BytesIO(base)).convert("RGB"), (W, H))
        except Exception:
            pass
    return title_poster(title)


def poster(base, badge, title):
    im = base_image(base, title)
    if badge == "found":
        badge_found(im)
    elif badge == "requested":
        badge_pill(im, GREEN, "Requested", "check")
    elif badge == "downloading":
        badge_pill(im, BLUE, "Downloading", "arrow")
    elif badge == "none":
        badge_none(im)
    return to_jpeg(im)


def fanart(base):
    if not base:
        return None
    try:
        return to_jpeg(Image.open(io.BytesIO(base)).convert("RGB"))
    except Exception:
        return None


def collection_poster(name, logo):
    im = gradient((W, H), (58, 40, 96), (16, 14, 30))
    d = ImageDraw.Draw(im)
    y_text = (330, 800)
    if logo:
        try:
            lg = Image.open(io.BytesIO(logo)).convert("RGBA")
            s = min(440 / lg.width, 200 / lg.height)
            lg = lg.resize((max(1, round(lg.width * s)), max(1, round(lg.height * s))), Image.LANCZOS)
            im.paste(lg, ((W - lg.width) // 2, 120 + (200 - lg.height) // 2), lg)
        except Exception:
            y_text = (200, 700)
    else:
        y_text = (200, 700)
    draw_text_block(d, name, (50, y_text[0], W - 50, y_text[1]), 64)
    return to_jpeg(im)


def library_tile(name):
    im = gradient((1280, 720), (58, 40, 96), (16, 14, 30))
    d = ImageDraw.Draw(im)
    draw_text_block(d, name, (100, 200, 1180, 520), 110)
    return to_jpeg(im)
