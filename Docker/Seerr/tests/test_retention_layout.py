#!/usr/bin/env python3
"""Storage retention UI (retention.js) in a real browser at 1280 and 390 px: the "♾️ Keep forever" box in the request dialog (below Can wait), the title page fact row with pill and toggle, the pills on request rows (kept, leaving on a date, leaving soon); everything inside the screen, no horizontal scrolling.
Stand-in Seerr markup with the real tip.js / can-wait.js / retention.js and invented data. Needs Playwright (skipped without it):
  sudo docker run --rm --network none -v $PWD:/w:ro -w /w seerr-bar-test:local python3 tests/test_retention_layout.py
Screenshots: add -e SHOT_DIR=/shots -v <dir>:/shots"""
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
HEAD = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{margin:0;background:#111827;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}main{padding:4rem 1rem 1rem}@media (min-width:1024px){main{margin-left:16rem}}
.card-field{display:flex;align-items:center;font-size:.875rem;margin:.25rem 0;min-width:0}.card-field-name{color:#9ca3af;margin-right:.5rem;flex-shrink:0}.badge{padding:0 .5rem;border-radius:9999px;background:rgba(34,197,94,.8);font-size:.75rem;line-height:1.25rem;white-space:nowrap}
.row{display:flex;gap:1rem;padding:1rem;margin:.5rem 0;border-radius:.75rem;background:#1f2937;overflow:hidden}.poster{width:4rem;height:6rem;flex-shrink:0;background:#374151;border-radius:.375rem}.row a{color:#fff;font-weight:700;text-decoration:none}
.media-facts{border:1px solid #374151;border-radius:.5rem;max-width:24rem;margin-left:auto;font-size:.875rem}.media-fact{display:flex;justify-content:space-between;gap:.75rem;padding:.5rem 1rem;border-bottom:1px solid #374151}.media-fact-value{text-align:right}
.dlg{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.6);padding:1rem}.dlg [role=dialog]{background:#1f2937;border-radius:.75rem;padding:1.5rem;width:100%;max-width:36rem;box-sizing:border-box}
.dlg button{margin:0 .25rem;padding:.4rem 1rem;border-radius:.375rem;background:#4f46e5;color:#fff;border:0}</style></head><body>
<a href="/discover/tv" style="display:none">Series</a>"""
SCRIPTS = '<script src="/_indexer-bar/tip.js"></script><script src="/_indexer-bar/can-wait.js"></script><script src="/_indexer-bar/retention.js"></script></body></html>'
DIALOG = """<div class="dlg"><div role="dialog" aria-modal="true"><div><span data-testid="modal-title">Request Movie</span><div style="font-size:1.25rem;font-weight:700">Invented Old Film</div></div>
<div style="margin-top:1rem;font-size:.875rem;color:#d1d5db">Quality profile and other request options.</div>
<div style="margin-top:1.25rem;display:flex;flex-direction:row-reverse;justify-content:center"><button data-testid="modal-ok-button">Request</button><button data-testid="modal-cancel-button">Cancel</button></div></div></div>"""
FACTS = """<div class="media-facts"><div class="media-fact"><span>Status</span><span class="media-fact-value">Released</span></div>
<div class="media-fact"><span>Release Date</span><span class="media-fact-value">3 Mar 2025</span></div><div class="media-fact"><span>Runtime</span><span class="media-fact-value">2h 4m</span></div></div>"""


def row(key, title):
    return f"""<div class="row"><div class="poster"></div><div style="min-width:0;flex:1"><a href="/{key}">{title}</a>
<div class="card-field"><span class="card-field-name">Requested</span><span>16 Nov 2026 by jane</span></div>
<div class="card-field"><span class="card-field-name">Status</span><span class="badge">Available</span></div></div></div>"""


PAGES = {
    "/movie/30": HEAD + "<main><h1>Invented Old Film</h1>" + FACTS + "</main>" + DIALOG + SCRIPTS,
    "/movie/10": HEAD + "<main><h1>Invented New Film</h1>" + FACTS + "</main>" + SCRIPTS,
    "/requests": HEAD + "<main><h1>Requests</h1>" + row("movie/10", "Invented New Film") + row("movie/11", "Invented Classic With A Rather Long Title") + row("tv/20", "Invented Show") + "</main>" + SCRIPTS,
}
TITLE_OLD = {"key": "movie/30", "known": False, "state": "keep", "keep": True, "source": "default", "default": True, "reasons": ["old"], "deleteOn": None, "canKeep": True, "canUnkeep": True, "deleteEnabled": False}
TITLE_NEW = {"key": "movie/10", "known": True, "state": "temporary", "keep": False, "source": "default", "default": False, "reasons": [], "deleteOn": "2027-02-14", "soon": False, "overdue": False,
             "imported": "2026-11-16", "ssdUntil": "2026-12-16", "hddFrom": "2026-12-16", "tier": "ssd", "canKeep": True, "canUnkeep": True, "deleteEnabled": False}
CARDS = {"titles": {"movie/10": {"state": "temporary", "deleteOn": "2027-02-14", "soon": False}, "movie/11": {"state": "keep", "source": "default"},
                    "tv/20": {"state": "temporary", "deleteOn": "2026-11-19", "soon": True}}, "deleteEnabled": False}


def serve(route):
    url = route.request.url.split("seerr.test", 1)[1]
    path = url.split("?")[0]
    if path in PAGES:
        return route.fulfill(status=200, body=PAGES[path], content_type="text/html")
    if path.startswith("/_indexer-bar/"):
        return route.fulfill(status=200, body=(WWW / path.rsplit("/", 1)[1]).read_text(), content_type="text/javascript")
    if path == "/_download-now/retention/title":
        return route.fulfill(status=200, body=json.dumps(TITLE_OLD if "tmdb=30" in url else TITLE_NEW), content_type="application/json")
    if path == "/_download-now/retention/titles":
        return route.fulfill(status=200, body=json.dumps(CARDS), content_type="application/json")
    route.fulfill(status=404, body="{}", content_type="application/json")


@unittest.skipIf(sync_playwright is None, "Playwright not installed")
class Layout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def shot(self, page, name):
        d = os.environ.get("SHOT_DIR")
        if d:
            page.screenshot(path=str(pathlib.Path(d, name)), full_page=True)

    def inside(self, page, sel, width):
        for box in [h.bounding_box() for h in page.query_selector_all(sel)]:
            self.assertIsNotNone(box, sel)
            self.assertGreaterEqual(box["x"], 0, sel)
            self.assertLessEqual(box["x"] + box["width"], width + 0.5, sel)

    def flow(self, width, mobile):
        page = self.browser.new_page(viewport={"width": width, "height": 900}, is_mobile=mobile, has_touch=mobile)
        page.route("http://seerr.test/**", serve)
        page.goto("http://seerr.test/movie/30")
        page.wait_for_selector("#hg-keep-forever input:checked")
        self.assertEqual(page.eval_on_selector("#hg-can-wait", "e => e.nextElementSibling.id"), "hg-keep-forever")
        self.inside(page, "#hg-keep-forever", width)
        self.shot(page, f"dialog-{width}.png")
        page.goto("http://seerr.test/movie/10")
        page.wait_for_selector("[data-hg-keep-fact] [data-hg-keep-toggle]")
        self.inside(page, "[data-hg-keep-fact]", width)
        self.shot(page, f"title-{width}.png")
        page.goto("http://seerr.test/requests")
        page.wait_for_function("document.querySelectorAll('[data-hg-keep-card]').length === 3")
        self.inside(page, "[data-hg-keep-card]", width)
        self.assertEqual(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), True)
        self.shot(page, f"requests-{width}.png")
        page.close()

    def test_desktop_1280(self):
        self.flow(1280, False)

    def test_phone_390(self):
        self.flow(390, True)


if __name__ == "__main__":
    unittest.main()
