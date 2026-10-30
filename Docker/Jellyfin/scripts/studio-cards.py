#!/usr/bin/env python3
"""Primary images for the studios and networks of the Moonfin Studios row (studiosRowSelectedIds, moonbase.py).
Moonfin draws each studio as a 16:9 card from its Primary image (only the name without one). Each studio of the web's studio and network cards (injector entry "Home networks", studio-logos.py) gets a card like the web: its white Logo centred on a dark gradient, or the card name when it has no logo.
Uploaded only when the logo, name or design changed, or the Primary is no longer ours; a Primary that was not ours is kept first in ORIGINALS. State in STATE_FILE. Never prints API keys.
Usage: studio-cards.py [--dry-run | --check]  (--check exits 1 when a card studio has no Primary image; needs no state, so it also runs as root)"""
import base64
import hashlib
import io
import json
import pathlib
import re
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import jellyfin_api  # noqa: E402

WIDTH, HEIGHT = 1280, 720
DESIGN = "1"
ENTRY = "Home networks"
PLUGIN = "JavaScript Injector"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
STATE_FILE = pathlib.Path.home() / ".local" / "state" / "jellyfin-studio-cards.json"
ORIGINALS = pathlib.Path.home() / ".local" / "state" / "jellyfin-studio-cards" / "originals"
# Logo area as fractions of the card (left, top, right, bottom): inside the web card's inset, a bit smaller as Moonfin's card is large.
BOX = (0.18, 0.25, 0.82, 0.75)


def cards(script):
    """[(studio id, card name)] of the entry's studio cards, then its network cards."""
    out = []
    for var in ("HssStudios", "HssNetworks"):
        m = re.search(rf"^window\.{var} = (.*);$", script or "", re.M)
        for card in json.loads(m.group(1)) if m else []:
            out += [(i, card["name"]) for i in card.get("ids") or [] if i not in [o[0] for o in out]]
    return out


def fingerprint(logo_tag, name):
    return hashlib.sha256(json.dumps([DESIGN, logo_tag, name]).encode()).hexdigest()[:16]


def _background():
    from PIL import Image, ImageDraw, ImageFilter
    base = Image.new("RGB", (2, 2))
    base.putdata([(44, 51, 64), (30, 35, 45), (30, 35, 45), (16, 19, 25)])
    card = base.resize((WIDTH, HEIGHT), Image.BILINEAR)
    glow = Image.new("L", (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(glow).ellipse([WIDTH * 0.2, HEIGHT * 0.15, WIDTH * 0.8, HEIGHT * 0.85], fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    card = Image.composite(Image.new("RGB", card.size, (72, 82, 100)), card, glow)
    edge = Image.new("L", card.size, 0)
    ImageDraw.Draw(edge).rectangle([0, 0, WIDTH - 1, HEIGHT - 1], outline=31, width=3)
    return Image.composite(Image.new("RGB", card.size, (255, 255, 255)), card, edge)


def render(logo, name):
    """PNG bytes of a WIDTH x HEIGHT card: the logo (PNG/JPEG bytes) fitted into BOX, or the name when logo is None."""
    from PIL import Image, ImageDraw, ImageFont
    card = _background()
    left, top, right, bottom = (round(f * s) for f, s in zip(BOX, (WIDTH, HEIGHT, WIDTH, HEIGHT)))
    bw, bh = right - left, bottom - top
    if logo is not None:
        img = Image.open(io.BytesIO(logo)).convert("RGBA")
        bbox = img.getchannel("A").getbbox()
        if bbox:
            img = img.crop(bbox)
        scale = min(bw / img.width, bh / img.height)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
        card.paste(img, (left + (bw - img.width) // 2, top + (bh - img.height) // 2), img)
    else:
        size = 130
        font = ImageFont.truetype(FONT, size)
        while font.getlength(name) > bw and size > 40:
            size -= 6
            font = ImageFont.truetype(FONT, size)
        ImageDraw.Draw(card).text((WIDTH / 2, HEIGHT / 2), name, font=font, fill=(255, 255, 255), anchor="mm")
    buf = io.BytesIO()
    card.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _auth(req):
    req.add_header("Authorization", f'MediaBrowser Token="{jellyfin_api.KEY_FILE.read_text().strip()}"')
    return req


def _fetch(studio_id, kind):
    with urllib.request.urlopen(_auth(urllib.request.Request(f"{jellyfin_api.BASE}/Items/{studio_id}/Images/{kind}")), timeout=30) as resp:
        return resp.read()


def _upload(studio_id, data):
    req = _auth(urllib.request.Request(f"{jellyfin_api.BASE}/Items/{studio_id}/Images/Primary", data=base64.b64encode(data), method="POST"))
    req.add_header("Content-Type", "image/png")
    with urllib.request.urlopen(req, timeout=30):
        pass


def _entry(api):
    plugin = next((p["Id"] for p in api("GET", "/Plugins") or [] if p.get("Name") == PLUGIN), None)
    scripts = api("GET", f"/Plugins/{plugin}/Configuration").get("CustomJavaScripts") or [] if plugin else []
    return next((s.get("Script") or "" for s in scripts if s.get("Name") == ENTRY), None)


def _studios(api):
    return {s["Id"]: s for s in api("GET", "/Studios?Recursive=true&EnableImageTypes=Primary,Logo&ImageTypeLimit=1")["Items"]}


def main(argv, api=None, fetch=_fetch, upload=_upload, render=render, state_file=STATE_FILE, originals=ORIGINALS, out=sys.stdout):
    if any(a not in ("--dry-run", "--check") for a in argv):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if api is None:
        api = jellyfin_api.api
    readonly = bool(argv)
    try:
        state = json.loads(state_file.read_text())
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    studios = _studios(api)
    jobs, same, missing, total = [], 0, 0, 0
    for sid, name in cards(_entry(api)):
        st = studios.get(sid)
        if not st:
            continue
        tags = st.get("ImageTags") or {}
        total += 1
        missing += not tags.get("Primary")
        key = fingerprint(tags.get("Logo"), name)
        mine = state.get(sid) or {}
        if mine.get("key") == key and tags.get("Primary") and tags.get("Primary") == mine.get("tag"):
            same += 1
        else:
            jobs.append((sid, name, key, tags))
    if "--check" in argv:
        print(f"{missing} cards missing" if missing else f"ok {total} cards", file=out)
        return 1 if missing else 0
    if readonly:
        print(f"{len(jobs)} cards to upload, {same} unchanged", file=out)
        return 0
    done = 0
    for sid, name, key, tags in jobs:
        try:
            primary = tags.get("Primary")
            if primary and primary != (state.get(sid) or {}).get("tag") and not (originals / sid).exists():
                originals.mkdir(parents=True, exist_ok=True)
                (originals / sid).write_bytes(fetch(sid, "Primary"))
            logo = fetch(sid, "Logo") if tags.get("Logo") else None
            upload(sid, render(logo, name))
            state[sid] = {"key": key, "tag": None}
            done += 1
        except Exception as e:
            print(f"card failed: {name}: {type(e).__name__}", file=out)
    if done:
        fresh = _studios(api)
        for sid, entry in state.items():
            if entry.get("tag") is None:
                entry["tag"] = ((fresh.get(sid) or {}).get("ImageTags") or {}).get("Primary")
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
        tmp.replace(state_file)
    print(f"{done} cards uploaded, {same} unchanged", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
