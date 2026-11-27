#!/usr/bin/env python3
"""My library (my-library.js) and the title page rows (retention.js) in a real browser at 1280 and 390 px: the list with every state, a request waiting for my OK, the bulk confirm (bin now vs request), the title page Fan favourite row and the Delete early confirm with its plan; everything inside the screen, no horizontal scrolling.
Stand-in Seerr markup with the real scripts and invented data. Needs Playwright (skipped without it):
  sudo docker run --rm --network none -v $PWD:/w:ro -w /w seerr-bar-test:local python3 tests/test_my_library_layout.py
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
<style>body{margin:0;background:#111827;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}main{padding:4rem 0 1rem}@media (min-width:1024px){main{margin-left:16rem}}
.media-facts{border:1px solid #374151;border-radius:.5rem;max-width:24rem;margin:0 1rem 0 auto;font-size:.875rem}.media-fact{display:flex;justify-content:space-between;gap:.75rem;padding:.5rem 1rem;border-bottom:1px solid #374151}.media-fact-value{text-align:right}</style></head><body>
<a href="/discover/tv" style="display:none">Series</a>"""
LIB_SCRIPTS = '<script src="/_indexer-bar/tip.js"></script><script src="/_indexer-bar/my-library.js"></script></body></html>'
TITLE_SCRIPTS = '<script src="/_indexer-bar/tip.js"></script><script src="/_indexer-bar/retention.js"></script></body></html>'
FACTS = """<div class="media-facts"><div class="media-fact"><span>Status</span><span class="media-fact-value">Released</span></div>
<div class="media-fact"><span>Release Date</span><span class="media-fact-value">3 Mar 2025</span></div><div class="media-fact"><span>Runtime</span><span class="media-fact-value">2h 4m</span></div></div>"""
PAGES = {
    "/my-library": HEAD + "<main><div>404 - Page Not Found</div></main>" + LIB_SCRIPTS,
    "/movie/10": HEAD + "<main><h1 style='padding:0 1rem'>Invented New Film</h1>" + FACTS + "</main>" + TITLE_SCRIPTS,
}


def title(key, t, delete=None, **kw):
    out = {"key": key, "kind": key.split("/")[0], "tmdb": int(key.split("/")[1]), "t": t, "y": 2025, "size": 2**33, "tier": "ssd", "deleteOn": "2027-02-15", "source": "default",
           "reasons": [], "why": "due", "postponed": None, "state": "temporary", "marks": [], "canMark": True, "canUnmark": False, "items": [],
           "del": delete or {"allowed": True, "reason": None, "plan": []}, "mine": False}
    out.update(kw)
    return out


LIBRARY = {"group": True, "deleteEnabled": True, "mineKnown": True, "quota": {"delete": {"used": 6, "max": 10}, "marks": {"used": 2, "max": 5}},
           "asked": [{"key": "movie/12", "kind": "movie", "t": "Invented Favourite", "y": 2024, "item": {"id": "p1", "state": "pending", "approver": True, "byName": "Joe",
                      "expires": "2026-12-04T10:00:00+01:00", "asks": [{"reason": "fan", "names": ["Me"], "answer": None}]}}],
           "titles": [
               title("movie/10", "Invented New Film", mine=True),
               title("movie/11", "Invented Film With A Really Quite Remarkably Long Title For Phones", state="keep", source="fan", deleteOn=None,
                     marks=[{"name": "Jane", "mine": False}], delete={"allowed": True, "reason": None, "plan": [{"reason": "fan", "names": ["Jane"]}]}),
               title("movie/12", "Invented Favourite", y=2024, state="keep", source="fan", deleteOn=None, marks=[{"name": "Me", "mine": True}], canMark=False, canUnmark=True),
               title("movie/13", "Invented Classic", y=1980, state="keep", reasons=["old", "goated"], deleteOn=None, canMark=False, delete={"allowed": False, "reason": "kept", "plan": []}),
               title("tv/20", "Invented Show", state="soon", deleteOn="2026-11-30", tier="split"),
               title("movie/14", "Invented Gone Film", state="bin", size=None, tier=None, deleteOn=None, source=None, canMark=False,
                     items=[{"id": "b1", "state": "deleted", "season": None, "byName": "Me", "mine": True, "approver": False, "asks": [], "binUntil": "2026-12-03T10:00:00+01:00", "canRestore": True}],
                     delete={"allowed": False, "reason": "bin", "plan": []}),
           ]}
TITLE = {"key": "movie/10", "known": True, "state": "temporary", "keep": False, "source": "default", "default": False, "reasons": [], "deleteOn": "2027-02-14", "soon": False, "overdue": False,
         "imported": "2026-11-16", "ssdUntil": "2026-12-16", "hddFrom": "2026-12-16", "tier": "ssd", "lastPlayed": None, "anchorBy": "import", "canMark": True, "canUnmark": False,
         "marks": [{"name": "Jane", "mine": False}], "markQuota": {"used": 2, "max": 5}, "deleteEnabled": True}
DSTATE = {"key": "movie/10", "allowed": True, "reason": None, "quota": {"used": 6, "max": 10}, "seasons": [], "queued": [], "items": [], "plan": [{"reason": "fan", "names": ["Jane"]}]}


def serve(route):
    url = route.request.url.split("seerr.test", 1)[1]
    path = url.split("?")[0]
    if path in PAGES:
        return route.fulfill(status=200, body=PAGES[path], content_type="text/html")
    if path.startswith("/_indexer-bar/"):
        return route.fulfill(status=200, body=(WWW / path.rsplit("/", 1)[1]).read_text(), content_type="text/javascript")
    if path == "/_download-now/library/list":
        return route.fulfill(status=200, body=json.dumps(LIBRARY), content_type="application/json")
    if path == "/_download-now/retention/title":
        return route.fulfill(status=200, body=json.dumps(TITLE), content_type="application/json")
    if path == "/_download-now/delete-early/state":
        return route.fulfill(status=200, body=json.dumps(DSTATE), content_type="application/json")
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

    def shot(self, page, name, sel=None):
        d = os.environ.get("SHOT_DIR")
        if d:
            (page.locator(sel).first.screenshot if sel else lambda path: page.screenshot(path=path, full_page=True))(path=str(pathlib.Path(d, name)))

    def inside(self, page, sel, width):
        boxes = [h.bounding_box() for h in page.query_selector_all(sel)]
        self.assertTrue(boxes, sel)
        for box in boxes:
            self.assertIsNotNone(box, sel)
            self.assertGreaterEqual(box["x"], 0, sel)
            self.assertLessEqual(box["x"] + box["width"], width + 0.5, sel)

    def no_scroll(self, page):
        self.assertEqual(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), True)

    def flow(self, width, mobile):
        page = self.browser.new_page(viewport={"width": width, "height": 900}, is_mobile=mobile, has_touch=mobile, device_scale_factor=2 if os.environ.get("SHOT_DIR") else 1)
        page.route("http://seerr.test/**", serve)
        page.goto("http://seerr.test/my-library")
        page.wait_for_selector("#hg-library [data-hg-l-row]")
        for sel in ("#hg-library [data-hg-l-row]", "#hg-library [data-hg-l-state]", "#hg-library .hg-l-btn", "[data-hg-l-asked]", "[data-hg-l-search]"):
            self.inside(page, sel, width)
        self.no_scroll(page)
        self.shot(page, f"library-{width}.png")
        self.shot(page, f"asked-{width}.png", "[data-hg-l-asked]")
        page.click('[data-hg-l-row="movie/10"] input[type=checkbox]')
        page.click('[data-hg-l-row="movie/11"] input[type=checkbox]')
        page.click("[data-hg-l-bulk]")
        page.wait_for_selector("[data-hg-l-confirm]")
        self.inside(page, "[data-hg-l-confirm]", width)
        self.inside(page, "[data-hg-l-confirm] li", width)
        self.assertIn("Invented New Film", page.inner_text("[data-hg-l-now]"))
        self.assertIn("Jane", page.inner_text("[data-hg-l-ask]"))
        self.no_scroll(page)
        self.shot(page, f"confirm-{width}.png", "[data-hg-l-confirm]")
        page.goto("http://seerr.test/movie/10")
        page.wait_for_selector("[data-hg-keep-fact] [data-hg-keep-toggle]")
        page.wait_for_selector("[data-hg-delete-start]")
        page.click("[data-hg-delete-start]")
        page.wait_for_selector("[data-hg-delete-plan]")
        self.assertIn("Jane", page.inner_text("[data-hg-delete-plan]"))
        for sel in ("[data-hg-keep-fact]", "[data-hg-keep-toggle]", "[data-hg-delete-fact]", ".hg-delete-confirm"):
            self.inside(page, sel, width)
        self.no_scroll(page)
        self.shot(page, f"title-{width}.png")
        page.close()

    def test_desktop_1280(self):
        self.flow(1280, False)

    def test_phone_390(self):
        self.flow(390, True)


if __name__ == "__main__":
    unittest.main()
