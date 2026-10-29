#!/usr/bin/env python3
"""Countdown and notice clips for the trending placeholders: now, night, unavailable, requested, coming (10 s, 1920x1080, 30 fps, silent AAC).
Frames are drawn with Pillow, encoded with the Jellyfin image's ffmpeg in a throwaway container (the jellyfin container is untouched), then moved into the clips directory.
Usage: trending-clips.py [--force]"""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

CLIPS_DIR = pathlib.Path("/srv/appdata/jellyfin/trending/clips")
CACHE = pathlib.Path.home() / ".cache" / "trending-clips"
SIZE = (1920, 1080)
SECONDS = 10
TEXTS = {
    "now": ("Download now", "Download starts when this clip ends · press Back to cancel", "Der Download startet, wenn dieser Clip endet · Zurück zum Abbrechen", "arrow", True),
    "night": ("Download tonight · lower power cost", "Download starts when this clip ends · press Back to cancel", "Der Download startet, wenn dieser Clip endet · Zurück zum Abbrechen", "moon", True),
    "unavailable": ("Not able to download yet", "Not able to download yet — checked daily", "Noch nicht herunterladbar — wird täglich geprüft", "cross", False),
    "requested": ("Requested", "Requested — it appears in the library when downloaded", "Angefragt — erscheint nach dem Download in der Bibliothek", "check", False),
    "coming": ("Coming soon", "Coming soon — not released yet", "Bald verfügbar — noch nicht erschienen", "clock", False),
}
NIGHT_HEADER_DE = "Heute Nacht herunterladen · günstigerer Strom"
ICON_COLOR = {"arrow": (30, 110, 200), "moon": (90, 80, 190), "cross": (200, 24, 24), "check": (30, 150, 70), "clock": (200, 140, 20)}


def icon(art, draw, kind, cx, cy, r):
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ICON_COLOR[kind])
    s = r * 0.9
    if kind == "arrow":
        art.arrow_down(draw, cx, cy - s * 0.12, s)
    elif kind == "check":
        art.check(draw, cx, cy, s)
    elif kind == "cross":
        w = round(r * 0.2)
        draw.line((cx - s * 0.45, cy - s * 0.45, cx + s * 0.45, cy + s * 0.45), fill=art.WHITE, width=w)
        draw.line((cx - s * 0.45, cy + s * 0.45, cx + s * 0.45, cy - s * 0.45), fill=art.WHITE, width=w)
    elif kind == "moon":
        draw.ellipse((cx - s * 0.5, cy - s * 0.5, cx + s * 0.5, cy + s * 0.5), fill=art.WHITE)
        draw.ellipse((cx - s * 0.2, cy - s * 0.65, cx + s * 0.8, cy + s * 0.35), fill=ICON_COLOR["moon"])
    elif kind == "clock":
        draw.ellipse((cx - s * 0.55, cy - s * 0.55, cx + s * 0.55, cy + s * 0.55), outline=art.WHITE, width=max(3, round(r * 0.1)))
        draw.line((cx, cy, cx, cy - s * 0.35), fill=art.WHITE, width=max(3, round(r * 0.1)))
        draw.line((cx, cy, cx + s * 0.28, cy + s * 0.12), fill=art.WHITE, width=max(3, round(r * 0.1)))


def frame(name, left):
    from PIL import ImageDraw

    import trending_art as art

    header, en, de, kind, countdown = TEXTS[name]
    im = art.gradient(SIZE, (28, 32, 48), (10, 11, 18))
    d = ImageDraw.Draw(im)
    size = 84
    while d.textlength(header, font=art.font(size)) + 190 > 1760:
        size -= 4
    fh = art.font(size)
    tw = d.textlength(header, font=fh)
    x0 = (SIZE[0] - (tw + 190)) / 2
    icon(art, d, kind, x0 + 70, 250, 70)
    d.text((x0 + 190, 250), header, font=fh, fill=art.WHITE, anchor="lm")
    if name == "night":
        d.text((SIZE[0] // 2, 345), NIGHT_HEADER_DE, font=art.font(44), fill=(170, 175, 200), anchor="mm")
    if countdown:
        d.text((SIZE[0] // 2, 640), str(left), font=art.font(300), fill=art.WHITE, anchor="mm")
    ty = 880 if countdown else 700
    d.text((SIZE[0] // 2, ty), en, font=art.font(46), fill=art.WHITE, anchor="mm")
    d.text((SIZE[0] // 2, ty + 70), de, font=art.font(40), fill=(170, 175, 200), anchor="mm")
    return im


def render_frames(name, tmp):
    for i in range(SECONDS):
        frame(name, SECONDS - i).save(tmp / f"f{i + 1:02d}.png")


def jellyfin_image():
    return subprocess.run(["sudo", "docker", "inspect", "-f", "{{.Config.Image}}", "jellyfin"], check=True, capture_output=True, text=True).stdout.strip()


def encode(image, tmp):
    subprocess.run(["sudo", "docker", "run", "--rm", "--entrypoint", "/usr/lib/jellyfin-ffmpeg/ffmpeg", "-u", f"{os.getuid()}:{os.getgid()}", "-v", f"{tmp}:/w", image,
                    "-y", "-loglevel", "error", "-framerate", "1", "-i", "/w/f%02d.png", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(SECONDS), "-r", "30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart", "/w/out.mp4"], check=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--force", action="store_true", help="rebuild clips that already exist")
    args = ap.parse_args(argv)
    todo = [n for n in TEXTS if args.force or not (CLIPS_DIR / f"{n}.mp4").exists()]
    if not todo:
        print("all clips exist")
        return 0
    CACHE.mkdir(parents=True, exist_ok=True)
    image = jellyfin_image()
    for name in todo:
        tmp = pathlib.Path(tempfile.mkdtemp(dir=CACHE))
        try:
            render_frames(name, tmp)
            encode(image, tmp)
            target = CLIPS_DIR / f"{name}.mp4"
            staged = CLIPS_DIR / f".{name}.mp4.new"
            shutil.copyfile(tmp / "out.mp4", staged)
            os.chmod(staged, 0o644)
            os.replace(staged, target)
            print(f"wrote {target}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
