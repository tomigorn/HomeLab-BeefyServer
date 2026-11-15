#!/usr/bin/env python3
"""Collections page (collections.js) in a real browser at 1280 and 390 px: search with a pick list, preview, the saved lists (own with Edit / Delete, another user's read only); no horizontal scrolling.
Stand-in Seerr layout with the real tip.js / collections.js and invented data. Needs Playwright (skipped without it):
  sudo docker run --rm --network none -v $PWD:/w:ro -w /w seerr-bar-test:local python3 tests/test_collections_layout.py
Screenshots: add -e SHOT_DIR=/shots -v <dir>:/shots; other answers (pick.json, ok.json, lists.json as the backend gives them): -e FIXTURES=/fix -v <dir>:/fix (posters need --network host)"""
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
<style>body{margin:0;background:#111827;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif}.side{display:none}
main{display:block;padding-top:4rem}@media (min-width:1024px){.side{display:block;position:fixed;inset:0 auto 0 0;width:16rem;box-sizing:border-box;background:#1f2937;padding:2rem 1rem}main{margin-left:16rem}}
.side a{display:block;color:#fff;padding:.5rem;text-decoration:none}</style></head><body>
<div class="side"><a href="/">Discover</a><a href="/discover/movies">Movies</a><a href="/discover/tv">Series</a><a href="/collections">Collections</a></div>
<main><div data-seerr-404>404 - Page Not Found</div></main>
<script src="/_indexer-bar/tip.js"></script><script src="/_indexer-bar/collections.js"></script></body></html>"""


def item(i, title, year, owned, kind="movie"):
    return {"k": kind, "id": i, "t": title, "y": year, "p": None, "o": owned}


DATA = {
    "pick": {"status": "pick", "picks": [{"term": 0, "field": "producer", "value": "Pat Example", "options": [
        {"id": 501, "name": "Pat Example", "photo": None, "known": ["Fake Film A", "Fake Show", "Another Film"]},
        {"id": 502, "name": "Pat Example", "photo": None, "known": ["A Game Show"]}]}]},
    "ok": {"status": "ok", "query": "producer:Pat Example", "items": [item(i, f"Invented Film {i}", 2026 - i, i % 3 == 0) for i in range(1, 13)], "count": 12, "total": 12, "owned": 4},
    "lists": {"me": {"id": 7, "admin": False, "create": True}, "limits": {"total": 20, "perUser": 5, "mine": 1, "all": 2}, "lists": [
        {"id": "user-pat-produced", "name": "Pat produced", "ownerName": "jane", "mine": True, "canEdit": True, "query": "producer:Pat Example", "sort": "release", "max": 50,
         "count": 12, "owned": 4, "items": [item(i, f"Invented Film {i}", 2026 - i, i % 3 == 0) for i in range(1, 13)]},
        {"id": "user-joe", "name": "Joe's heist films", "ownerName": "joe", "mine": False, "canEdit": False, "query": "keyword:heist genre:thriller", "sort": "popularity", "max": 50,
         "count": 3, "owned": 1, "items": [item(20 + i, f"Heist Story {i}", 2010 + i, i == 1) for i in range(1, 4)]}]},
}


def answers():
    d = dict(DATA)
    fix = os.environ.get("FIXTURES")
    if fix:
        for name in ("pick", "ok", "lists"):
            p = pathlib.Path(fix, name + ".json")
            if p.is_file():
                d[name] = json.loads(p.read_text())
    return d


def serve(route):
    url = route.request.url.split("seerr.test", 1)[1]
    path = url.split("?")[0]
    d = answers()
    if path == "/collections":
        return route.fulfill(status=200, body=PAGE, content_type="text/html")
    if path.startswith("/_indexer-bar/") and path.endswith(".js"):
        return route.fulfill(status=200, body=(WWW / path.rsplit("/", 1)[1]).read_text(), content_type="text/javascript")
    if path == "/api/v1/auth/me":
        return route.fulfill(status=200, body=json.dumps({"id": 7, "settings": {"locale": ""}}), content_type="application/json")
    if path == "/_download-now/collections/lists":
        return route.fulfill(status=200, body=json.dumps(d["lists"]), content_type="application/json")
    if path == "/_download-now/collections/preview":
        return route.fulfill(status=200, body=json.dumps(d["ok"] if "pick=" in url else d["pick"]), content_type="application/json")
    route.fulfill(status=404, body="{}", content_type="application/json")


SCROLL = "() => ({ doc: document.documentElement.scrollWidth, inner: innerWidth })"


@unittest.skipIf(sync_playwright is None, "Playwright not installed (run it in the Playwright image, see the docstring)")
class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def shot(self, page, name, **kw):
        d = os.environ.get("SHOT_DIR")
        if d:
            page.wait_for_timeout(600)
            page.screenshot(path=str(pathlib.Path(d, name)), **kw)

    def flow(self, width, mobile):
        ctx = self.browser.new_context(viewport={"width": width, "height": 900}, is_mobile=mobile, has_touch=mobile, device_scale_factor=2 if mobile else 1)
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        page.route("http://seerr.test/**", serve)
        page.goto("http://seerr.test/collections")
        page.wait_for_selector("#hg-collections [data-list]")
        self.assertEqual(page.title(), "Collections - Seerr")
        first = answers()["pick"]["picks"][0]
        page.fill("[data-field=name]", "My list")
        page.fill("[data-field=query]", f"{first['field']}:{first['value']}")
        page.click("[data-act=preview]")
        page.wait_for_selector("[data-pick]")
        s = page.evaluate(SCROLL)
        self.assertLessEqual(s["doc"], s["inner"], f"pick list scrolls sideways at {width}")
        self.shot(page, f"collections-1-pick-{width}.png", full_page=True)
        page.locator("[data-pick]").first.click()
        page.wait_for_selector("[data-preview] [data-title]")
        self.assertFalse(page.locator("[data-act=save]").is_disabled())
        s = page.evaluate(SCROLL)
        self.assertLessEqual(s["doc"], s["inner"], f"preview scrolls sideways at {width}")
        self.shot(page, f"collections-2-preview-{width}.png", full_page=True)
        own = page.locator("[data-list]").filter(has=page.locator("[data-act=edit]")).first
        own.scroll_into_view_if_needed()
        self.shot(page, f"collections-3-own-list-{width}.png", clip=own.bounding_box() and {**own.bounding_box(), "y": own.bounding_box()["y"] + page.evaluate("scrollY")} or None, full_page=True)
        other = page.locator("[data-list]").filter(has=page.locator("[data-readonly]")).first
        self.assertEqual(other.locator("[data-act=edit]").count(), 0)
        other.scroll_into_view_if_needed()
        self.shot(page, f"collections-4-other-list-{width}.png", clip=other.bounding_box() and {**other.bounding_box(), "y": other.bounding_box()["y"] + page.evaluate("scrollY")} or None, full_page=True)
        help_ = page.locator("[data-help]")
        help_.scroll_into_view_if_needed()
        if mobile:
            help_.tap()
        else:
            page.mouse.move(2, 2)
            help_.hover()
        page.wait_for_selector("#hg-tip", state="visible")
        box = page.locator("#hg-tip").bounding_box()
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["x"] + box["width"], width + 1)

    def test_desktop_1280(self):
        self.flow(1280, False)

    def test_phone_390(self):
        self.flow(390, True)


if __name__ == "__main__":
    unittest.main()
