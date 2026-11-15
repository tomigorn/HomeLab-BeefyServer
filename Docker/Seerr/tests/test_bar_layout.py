#!/usr/bin/env python3
"""Bar popup layout in a real browser: no horizontal scrolling and names fully visible at phone widths (320-430 px), names and values side by side on desktop; chip hints (tip.js) open on hover / tap inside the screen.
Stand-in page with the real tip.js/bar.js/bar.css and tests/fixtures/usage-sample.json. Needs Playwright (skipped without it):
  sudo docker run --rm --network none -v $PWD:/w:ro -w /w mcr.microsoft.com/playwright/python:v1.49.0-noble python3 tests/test_bar_layout.py
Screenshots: add -e SHOT_DIR=/shots -v <dir>:/shots"""
import datetime as dt
import json
import os
import pathlib
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
WWW = ROOT / "indexer-bar" / "www"
PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/bar.css"></head><body style="margin:0;background:#111827;color:#fff;font-family:sans-serif">
<p style="padding:40px 16px">Seerr</p><script src="/tip.js"></script><script src="/bar.js" data-ib-data="/usage.json"></script></body></html>"""


def usage():
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    text = (ROOT / "tests" / "fixtures" / "usage-sample.json").read_text()
    return text.replace("GENERATED", now.isoformat().replace("+00:00", "Z")).replace("RESET", (now + dt.timedelta(hours=3)).isoformat().replace("+00:00", "Z"))


def serve(route):
    path = route.request.url.split("bar.test", 1)[1].split("?")[0]
    files = {"/": (PAGE, "text/html"), "/bar.js": ((WWW / "bar.js").read_text(), "text/javascript"), "/tip.js": ((WWW / "tip.js").read_text(), "text/javascript"),
             "/bar.css": ((WWW / "bar.css").read_text(), "text/css"), "/usage.json": (usage(), "application/json")}
    if path in files:
        route.fulfill(status=200, body=files[path][0], content_type=files[path][1])
    else:
        route.fulfill(status=404, body="")


GEOMETRY = """() => {
  const p = document.querySelector('#indexer-bar .ib-panel'), pr = p.getBoundingClientRect();
  const names = [...p.querySelectorAll('.ib-name')].map((n) => { const r = n.getBoundingClientRect(); return { text: n.textContent, left: r.left, right: r.right }; });
  const rows = [...p.querySelectorAll('tr')].filter((tr) => tr.cells.length === 2).map((tr) => ({ text: tr.cells[0].textContent, top0: tr.cells[0].getBoundingClientRect().top, top1: tr.cells[1].getBoundingClientRect().top }));
  return { scrollWidth: p.scrollWidth, clientWidth: p.clientWidth, docScroll: document.documentElement.scrollWidth, inner: innerWidth,
           left: pr.left, right: pr.right, width: pr.width, names, rows };
}"""


@unittest.skipIf(sync_playwright is None, "Playwright not installed (run it in the Playwright image, see the docstring)")
class Popup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def open(self, width, height=900, mobile=True):
        ctx = self.browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile, device_scale_factor=2 if mobile else 1)
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        page.route("http://bar.test/**", serve)
        page.goto("http://bar.test/")
        page.click("#indexer-bar .ib-more")
        page.wait_for_selector("#indexer-bar .ib-panel .ib-star")
        return page

    def hint(self, width, selector, mobile, height=900):
        ctx = self.browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile, device_scale_factor=2 if mobile else 1)
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        page.route("http://bar.test/**", serve)
        page.goto("http://bar.test/")
        chip = page.locator(selector)
        chip.scroll_into_view_if_needed()
        chip.tap() if mobile else chip.hover()
        page.wait_for_selector("#hg-tip", state="visible")
        return page, page.evaluate("""() => { const r = document.getElementById('hg-tip').getBoundingClientRect();
          return { left: r.left, right: r.right, top: r.top, inner: innerWidth, title: document.querySelector('#hg-tip .hg-tip-title').textContent, seps: document.querySelectorAll('#hg-tip .hg-tip-sep').length }; }""")

    def test_chip_hints_open_inside_the_screen(self):
        for width, mobile in ((390, True), (1280, False)):
            for selector, title in (('[data-ib-disk="SSD"]', "SSD — fast tier, new downloads"), ('[data-ib-subtitle="subdl"]', "SubDL")):
                with self.subTest(width=width, chip=selector):
                    _, b = self.hint(width, selector, mobile)
                    self.assertEqual(b["title"], title)
                    self.assertEqual(b["seps"], 1)
                    self.assertGreaterEqual(b["left"], 0)
                    self.assertLessEqual(b["right"], b["inner"])

    def test_phone_widths_have_no_horizontal_scroll(self):
        for width in (320, 390, 430):
            with self.subTest(width=width):
                g = self.open(width).evaluate(GEOMETRY)
                self.assertLessEqual(g["scrollWidth"], g["clientWidth"])
                self.assertLessEqual(g["docScroll"], g["inner"])
                self.assertGreaterEqual(g["left"], 0)
                self.assertLessEqual(g["right"], g["inner"])

    def test_phone_names_are_fully_inside_the_popup(self):
        g = self.open(390).evaluate(GEOMETRY)
        self.assertGreater(len(g["names"]), 15)
        for n in g["names"]:
            self.assertGreaterEqual(n["left"], g["left"], n["text"])
            self.assertLessEqual(n["right"], g["right"], n["text"])

    def test_desktop_keeps_names_and_values_side_by_side(self):
        g = self.open(1280, mobile=False).evaluate(GEOMETRY)
        self.assertEqual(g["width"], 440)
        self.assertLessEqual(g["scrollWidth"], g["clientWidth"])
        self.assertTrue(g["rows"])
        for r in g["rows"]:
            self.assertAlmostEqual(r["top0"], r["top1"], delta=12, msg=r["text"])

    def test_screenshots(self):
        shots = os.environ.get("SHOT_DIR")
        if not shots:
            self.skipTest("SHOT_DIR not set")
        self.open(390, height=1800).screenshot(path=f"{shots}/popup-390.png")
        self.open(1280, height=1400, mobile=False).screenshot(path=f"{shots}/popup-1280.png")
        for width, mobile in ((390, True), (1280, False)):
            for name, selector in (("storage", '[data-ib-disk="SSD"]'), ("subtitle", '[data-ib-subtitle="subdl"]')):
                self.hint(width, selector, mobile, height=420 if mobile else 360)[0].screenshot(path=f"{shots}/hint-{name}-{width}.png")


if __name__ == "__main__":
    unittest.main()
