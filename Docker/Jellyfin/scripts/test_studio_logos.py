import importlib.util
import io
import json
import pathlib
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location("studio_logos", pathlib.Path(__file__).with_name("studio-logos.py"))
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)


def pair(name, sid=None):
    return {"Name": name, "Id": sid or "id-" + name}


def movie(tmdb, studios):
    return {"Type": "Movie", "ProviderIds": {"Tmdb": tmdb}, "Studios": [pair(s) for s in studios]}


def show(tmdb, studios):
    return {"Type": "Series", "ProviderIds": {"Tmdb": tmdb}, "Studios": [pair(s) for s in studios]}


def studio(name, logo=None):
    return {"Name": name, "Id": "id-" + name, "ImageTags": {"Logo": logo} if logo else {}}


CACHE = {
    "movie:862": {"companies": [{"id": 3, "name": "Pixar", "logo": "/pixar.png"}, {"id": 2, "name": "Walt Disney Pictures", "logo": None}], "networks": []},
    "tv:76479": {"companies": [{"id": 20580, "name": "Amazon Studios", "logo": "/amz.png"}], "networks": [{"id": 1024, "name": "Prime Video", "logo": "/prime.png"}]},
    "tv:4546": {"companies": [{"id": 1, "name": "HBO", "logo": "/hbo-c.png"}], "networks": [{"id": 49, "name": "HBO", "logo": "/hbo.png"}]},
}


class Pure(unittest.TestCase):
    def test_tmdb_ref(self):
        self.assertEqual(sl.tmdb_ref(movie("862", [])), "movie:862")
        self.assertEqual(sl.tmdb_ref(show("4546", [])), "tv:4546")
        self.assertIsNone(sl.tmdb_ref({"Type": "Movie", "ProviderIds": {}}))

    def test_details_from_seerr(self):
        raw = {"productionCompanies": [{"id": 3, "name": "Pixar", "logoPath": "/p.png", "originCountry": "US"}], "networks": [{"id": 49, "name": "HBO", "logoPath": None}]}
        self.assertEqual(sl.details(raw), {"companies": [{"id": 3, "name": "Pixar", "logo": "/p.png"}], "networks": [{"id": 49, "name": "HBO", "logo": None}]})
        self.assertEqual(sl.details({}), {"companies": [], "networks": []})

    def test_networks_are_the_series_studios_tmdb_lists_as_networks(self):
        series = [show("76479", ["Prime Video", "Amazon Studios"]), show("4546", ["HBO", "Production Partners"]), show("999", ["Unknown TV"])]
        self.assertEqual(sl.network_ids(series, CACHE), ["id-HBO", "id-Prime Video"])

    def test_networks_row_keeps_only_well_known_networks_by_tmdb_id(self):
        cache = {**CACHE, "tv:1": {"companies": [], "networks": [{"id": 614, "name": "TOKYO MX", "logo": "/mx.png"}, {"id": 1112, "name": "Crunchyroll", "logo": "/cr.png"}]}}
        series = [show("1", ["TOKYO MX", "Crunchyroll"]), show("4546", ["HBO"])]
        self.assertEqual(sl.network_ids(series, cache), ["id-Crunchyroll", "id-HBO"])

    def test_well_known_networks_match_by_id_not_by_a_shared_name(self):
        self.assertTrue(sl.well_known({"id": 213, "name": "Netflix"}))
        self.assertTrue(sl.well_known({"id": 6783, "name": "Max"}))
        self.assertFalse(sl.well_known({"id": 505, "name": "max"}))
        self.assertFalse(sl.well_known({"id": 833, "name": "RTL"}))
        self.assertFalse(sl.well_known({"id": 57, "name": "Nippon TV"}))

    def test_a_network_without_id_falls_back_to_its_name(self):
        self.assertTrue(sl.well_known({"id": None, "name": "Netflix"}))
        self.assertFalse(sl.well_known({"name": "TOKYO MX"}))

    def test_well_known_list_covers_streamers_and_major_us_uk_de_ch_broadcasters(self):
        names = set(sl.WELL_KNOWN.values())
        for n in ["Netflix", "Prime Video", "Disney+", "Apple TV+", "HBO", "Max", "Hulu", "Paramount+", "Peacock", "Crunchyroll", "AMC", "FX", "BBC One", "Channel 4", "Sky Atlantic", "ZDF", "Das Erste", "ProSieben", "RTL", "SRF 1", "ARTE"]:
            self.assertIn(n, names)

    def test_small_networks_get_no_logo_job(self):
        cache = {"tv:1": {"companies": [], "networks": [{"id": 614, "name": "TOKYO MX", "logo": "/mx.png"}]}}
        items = [show("1", ["TOKYO MX"])]
        self.assertEqual(sl.logo_jobs([studio("TOKYO MX")], items, cache, sl.network_ids(items, cache)), [])

    def test_names_match_loosely(self):
        self.assertEqual(sl.norm("Apple TV+"), sl.norm("apple tv +"))
        self.assertNotEqual(sl.norm("HBO"), sl.norm("HBO Max"))

    def test_logo_jobs_movie_studios_and_networks_without_a_logo(self):
        items = [movie("862", ["Pixar", "Walt Disney Pictures"]), show("76479", ["Prime Video", "Amazon Studios"]), show("4546", ["HBO"])]
        studios = [studio("Pixar"), studio("Walt Disney Pictures"), studio("Prime Video"), studio("Amazon Studios"), studio("HBO", logo="t")]
        jobs = sl.logo_jobs(studios, items, CACHE, ["id-Prime Video", "id-HBO"])
        self.assertEqual(jobs, [("id-Pixar", "Pixar", "/pixar.png"), ("id-Prime Video", "Prime Video", "/prime.png")])

    def test_network_logo_comes_from_the_network_not_the_company(self):
        jobs = sl.logo_jobs([studio("HBO")], [show("4546", ["HBO"])], CACHE, ["id-HBO"])
        self.assertEqual(jobs, [("id-HBO", "HBO", "/hbo.png")])

    def test_logo_needs_see_through_background(self):
        from PIL import Image

        def png(mode, color):
            buf = io.BytesIO()
            Image.new(mode, (40, 20), color).save(buf, "PNG")
            return buf.getvalue()
        self.assertFalse(sl.see_through(png("RGB", (255, 255, 255))))
        self.assertFalse(sl.see_through(png("RGBA", (255, 255, 255, 255))))
        self.assertTrue(sl.see_through(png("RGBA", (255, 255, 255, 0))))
        self.assertFalse(sl.see_through(b"not an image"))

    def test_logo_jobs_skip_paths_known_to_be_boxes(self):
        jobs = sl.logo_jobs([studio("Pixar")], [movie("862", ["Pixar"])], {**CACHE, "opaque": ["/pixar.png"]}, [])
        self.assertEqual(jobs, [])

    def test_white_logo_url(self):
        self.assertEqual(sl.logo_url("/pixar.png"), "https://image.tmdb.org/t/p/w500_filter(duotone,ffffff,bababa)/pixar.png")

    def test_networks_script_is_plain_data(self):
        js = sl.networks_script(["b", "a"])
        self.assertEqual(js, 'window.HssNetworks = ["a", "b"];\n')


class FakeJellyfin:
    def __init__(self, items, studios, scripts=None):
        self.items, self.studios, self.posts, self.uploads = items, studios, [], []
        self.config = {"CustomJavaScripts": scripts or []}

    def api(self, method, path, body=None):
        if path == "/Plugins":
            return [{"Name": "JavaScript Injector", "Id": "js"}]
        if path.startswith("/Plugins/js/Configuration"):
            if method == "POST":
                self.posts.append(body)
                self.config = body
                return None
            return self.config
        if path.startswith("/Items?"):
            kind = "Movie" if "IncludeItemTypes=Movie" in path else "Series"
            return {"Items": [i for i in self.items if i["Type"] == kind]}
        if path.startswith("/Studios?"):
            return {"Items": self.studios}
        raise AssertionError(path)

    def upload(self, studio_id, data):
        self.uploads.append((studio_id, data))


class Main(unittest.TestCase):
    def run_main(self, jf, argv, seerr, cache_file):
        out = io.StringIO()
        code = sl.main(argv, api=jf.api, seerr=seerr, fetch=lambda url: b"PNG:" + url.encode(), upload=jf.upload, cache_file=cache_file, out=out, see_through=lambda d: True)
        return code, out.getvalue()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = pathlib.Path(self.tmp.name) / "c.json"
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def seerr(self, ref):
        self.calls.append(ref)
        kind, tmdb = ref.split(":")
        return {"productionCompanies": [{"id": 3, "name": "Pixar", "logoPath": "/pixar.png"}]} if kind == "movie" else {"networks": [{"id": 49, "name": "HBO", "logoPath": "/hbo.png"}]}

    def test_adds_logos_and_the_networks_entry_then_nothing_on_rerun(self):
        jf = FakeJellyfin([movie("862", ["Pixar"]), show("4546", ["HBO"])], [studio("Pixar"), studio("HBO")])
        code, text = self.run_main(jf, [], self.seerr, self.cache)
        self.assertEqual(code, 0)
        self.assertEqual([u[0] for u in jf.uploads], ["id-HBO", "id-Pixar"])
        self.assertEqual(jf.uploads[0][1], b"PNG:" + sl.logo_url("/hbo.png").encode())
        self.assertEqual(text.splitlines()[-1], "2 logos added, networks changed")
        entry = next(s for s in jf.config["CustomJavaScripts"] if s["Name"] == sl.NAME)
        self.assertEqual(entry["Script"], 'window.HssNetworks = ["id-HBO"];\n')
        self.assertTrue(entry["RequiresAuthentication"])
        self.assertEqual(sorted(json.loads(self.cache.read_text())), ["movie:862", "opaque", "tv:4546"])
        jf.studios = [studio("Pixar", "t1"), studio("HBO", "t2")]
        self.calls.clear()
        code, text = self.run_main(jf, [], self.seerr, self.cache)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(jf.posts), 1)
        self.assertEqual(text.splitlines()[-1], "0 logos added, networks unchanged")

    def test_dry_run_changes_nothing(self):
        jf = FakeJellyfin([movie("862", ["Pixar"])], [studio("Pixar")])
        code, text = self.run_main(jf, ["--dry-run"], self.seerr, self.cache)
        self.assertEqual(code, 0)
        self.assertEqual(jf.uploads, [])
        self.assertEqual(jf.posts, [])
        self.assertEqual(text.splitlines()[-1], "1 logos to add, networks would change")

    def test_a_failed_lookup_or_download_is_skipped_and_retried_next_run(self):
        jf = FakeJellyfin([movie("862", ["Pixar"]), movie("1", ["Other"])], [studio("Pixar"), studio("Other")])

        def flaky(ref):
            if ref == "movie:1":
                raise OSError("down")
            return self.seerr(ref)
        code, text = self.run_main(jf, [], flaky, self.cache)
        self.assertEqual(code, 0)
        self.assertNotIn("movie:1", json.loads(self.cache.read_text()))
        self.assertEqual([u[0] for u in jf.uploads], ["id-Pixar"])

    def test_a_logo_without_transparency_is_not_uploaded_and_remembered(self):
        jf = FakeJellyfin([movie("862", ["Pixar"])], [studio("Pixar")])
        out = io.StringIO()
        sl.main([], api=jf.api, seerr=self.seerr, fetch=lambda url: b"box", upload=jf.upload, cache_file=self.cache, out=out, see_through=lambda d: False)
        self.assertEqual(jf.uploads, [])
        self.assertEqual(json.loads(self.cache.read_text())["opaque"], ["/pixar.png"])
        self.assertTrue(out.getvalue().splitlines()[-1].startswith("0 logos added"))

    def test_unknown_arguments(self):
        jf = FakeJellyfin([], [])
        self.assertEqual(sl.main(["--nope"], api=jf.api, seerr=self.seerr, fetch=None, upload=None, cache_file=self.cache, out=io.StringIO()), 2)


if __name__ == "__main__":
    unittest.main()
