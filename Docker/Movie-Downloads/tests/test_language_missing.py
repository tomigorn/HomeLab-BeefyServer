#!/usr/bin/env python3
"""scripts/language-missing.py: which tagged titles count as "language not found", when they are due for the one notification, the state across runs and the textfile output, against fake Radarr/Sonarr.
Usage: python3 tests/test_language_missing.py"""
import importlib.util
import pathlib
import re
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("lm", ROOT / "scripts" / "language-missing.py")
lm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lm)
LANGS = lm.load_languages(ROOT / "scripts" / "languages.tsv", ["gsw"])
DAY = 86400
T0 = 1_800_000_000
ADDED = "2027-01-05T08:00:00Z"
ADDED_TS = 1_800_000_000 - 10 * DAY

RING = {"id": 21, "title": "The Ring Thing", "year": 2004, "monitored": True, "hasFile": False, "tags": [13], "added": ADDED, "lastSearchTime": ADDED}
WILDER = {"id": 9, "title": "Wilder", "year": 2017, "monitored": True, "tags": [13], "added": ADDED}


class FakeApp:
    def __init__(self, kind, titles, queue=(), blocklist=(), missing=(), fail=False):
        self.kind, self.titles, self.queue, self.blocklist, self.missing, self.fail = kind, titles, list(queue), list(blocklist), list(missing), fail

    def __call__(self, method, path, query=None, body=None):
        if self.fail:
            raise lm.ApiError("down")
        assert method == "GET", (method, path)
        if path == "/tag":
            return [{"id": 13, "label": "audio-swiss-german"}, {"id": 14, "label": "audio-german"}]
        if path in ("/movie", "/series"):
            return self.titles
        if path == "/queue":
            return {"totalRecords": len(self.queue), "records": self.queue}
        if path == "/blocklist":
            return {"totalRecords": len(self.blocklist), "records": self.blocklist}
        if path == "/wanted/missing":
            return {"totalRecords": len(self.missing), "records": self.missing}
        raise AssertionError(path)


def radarr(*titles, **kw):
    return FakeApp("movie", list(titles), **kw)


def sonarr(*titles, **kw):
    return FakeApp("series", list(titles), **kw)


def ep(i, series=9, searched=True):
    return {"id": i, "seriesId": series, "lastSearchTime": ADDED if searched else None}


class Watch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.state, self.prom = self.dir / "state.json", self.dir / "media-language.prom"

    def tearDown(self):
        self.tmp.cleanup()

    def run_lm(self, apps, now=T0, days=7):
        return lm.Watch(apps, LANGS, self.state, self.prom, days=days).run(now)

    def series(self):
        out = {}
        for line in self.prom.read_text().splitlines():
            m = re.match(r'^(media_language_missing_(since|notify)_timestamp_seconds)\{(.*)\} (\S+)$', line)
            if m:
                labels = dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', m[3]))
                out.setdefault((labels["app"], labels["title"], labels["failed"]), {})[m[2]] = float(m[4])
        return out

    def test_first_run_dates_a_title_from_when_it_was_added(self):
        self.assertEqual(self.run_lm({"radarr": radarr(RING)}), 0)
        self.assertEqual(self.series(), {("radarr", "The Ring Thing (2004)", "0"): {"since": ADDED_TS, "notify": ADDED_TS + 7 * DAY}})
        self.assertIn('language="Swiss German"', self.prom.read_text())

    def test_a_title_seen_first_later_is_dated_from_then(self):
        self.run_lm({"radarr": radarr()})
        self.run_lm({"radarr": radarr(RING)}, now=T0 + DAY)
        self.assertEqual(self.series(), {("radarr", "The Ring Thing (2004)", "0"): {"since": T0 + DAY, "notify": T0 + 8 * DAY}})

    def test_the_date_is_kept_across_runs(self):
        self.run_lm({"radarr": radarr(RING)})
        self.run_lm({"radarr": radarr(RING)}, now=T0 + 3 * DAY)
        self.assertEqual(self.series()[("radarr", "The Ring Thing (2004)", "0")]["since"], ADDED_TS)

    def test_not_counted_with_a_file_unmonitored_untagged_never_searched_or_downloading(self):
        for app in (radarr(dict(RING, hasFile=True)), radarr(dict(RING, monitored=False)), radarr(dict(RING, tags=[14])),
                    radarr(dict(RING, lastSearchTime=None)), radarr(RING, queue=[{"movieId": 21}])):
            self.run_lm({"radarr": app})
            self.assertEqual(self.series(), {})

    def test_a_download_does_not_reset_the_date(self):
        self.run_lm({"radarr": radarr(RING)})
        self.run_lm({"radarr": radarr(RING, queue=[{"movieId": 21}])}, now=T0 + DAY)
        self.run_lm({"radarr": radarr(RING)}, now=T0 + 2 * DAY)
        self.assertEqual(self.series()[("radarr", "The Ring Thing (2004)", "0")]["since"], ADDED_TS)

    def test_a_file_resets_the_title(self):
        self.run_lm({"radarr": radarr(RING)})
        self.run_lm({"radarr": radarr(dict(RING, hasFile=True))}, now=T0 + DAY)
        self.run_lm({"radarr": radarr(RING)}, now=T0 + 2 * DAY)
        self.assertEqual(self.series()[("radarr", "The Ring Thing (2004)", "0")]["since"], T0 + 2 * DAY)

    def test_a_failed_grab_is_a_new_state_notified_at_once(self):
        self.run_lm({"radarr": radarr(RING)})
        self.run_lm({"radarr": radarr(RING, blocklist=[{"movieId": 21}])}, now=T0 + 20 * DAY)
        self.assertEqual(self.series(), {("radarr", "The Ring Thing (2004)", "1"): {"since": ADDED_TS, "notify": T0 + 20 * DAY}})
        self.run_lm({"radarr": radarr(RING, blocklist=[{"movieId": 21}])}, now=T0 + 21 * DAY)
        self.assertEqual(self.series()[("radarr", "The Ring Thing (2004)", "1")]["notify"], T0 + 20 * DAY)

    def test_a_failure_before_the_wait_is_over_is_notified_with_the_wait(self):
        recent = dict(RING, added="2027-01-13T08:00:00Z")
        self.run_lm({"radarr": radarr(recent)})
        self.run_lm({"radarr": radarr(recent, blocklist=[{"movieId": 21}, {"movieId": 5}])}, now=T0 + DAY)
        self.assertEqual(self.series(), {("radarr", "The Ring Thing (2004)", "1"): {"since": T0 - 2 * DAY, "notify": T0 + 5 * DAY}})

    def test_series_with_missing_searched_episodes(self):
        self.run_lm({"sonarr": sonarr(WILDER, missing=[ep(101), ep(102, searched=False), ep(201, series=4)])})
        self.assertEqual(self.series(), {("sonarr", "Wilder (2017)", "0"): {"since": ADDED_TS, "notify": ADDED_TS + 7 * DAY}})

    def test_series_not_counted_without_missing_or_searched_episodes_or_while_downloading(self):
        for app in (sonarr(WILDER), sonarr(WILDER, missing=[ep(101, searched=False)]), sonarr(dict(WILDER, monitored=False), missing=[ep(101)]),
                    sonarr(WILDER, missing=[ep(101)], queue=[{"seriesId": 9}])):
            self.run_lm({"sonarr": app})
            self.assertEqual(self.series(), {})

    def test_series_failures_count_from_its_blocklist(self):
        self.run_lm({"sonarr": sonarr(WILDER, missing=[ep(101)], blocklist=[{"seriesId": 9}, {"seriesId": 9}, {"seriesId": 4}])})
        self.assertEqual(list(self.series()), [("sonarr", "Wilder (2017)", "2")])

    def test_both_apps_and_a_failing_app_keeps_its_old_lines(self):
        self.run_lm({"radarr": radarr(RING), "sonarr": sonarr(WILDER, missing=[ep(101)])})
        self.assertEqual(len(self.series()), 2)
        self.assertEqual(self.run_lm({"radarr": radarr(RING), "sonarr": sonarr(fail=True)}, now=T0 + DAY), 1)
        self.assertEqual(len(self.series()), 2)

    def test_output_is_valid_exposition_with_escaped_titles(self):
        self.run_lm({"radarr": radarr(dict(RING, title='Say "Hi" \\ now'))})
        text = self.prom.read_text()
        self.assertIn('title="Say \\"Hi\\" \\\\ now (2004)"', text)
        names = [l.split()[2] for l in text.splitlines() if l.startswith("# TYPE")]
        self.assertEqual(len(names), len(set(names)))
        self.assertRegex(text, r"(?m)^media_language_missing_last_run_timestamp_seconds 1800000000$")

    def test_deleted_titles_leave_the_state(self):
        self.run_lm({"radarr": radarr(RING)})
        self.run_lm({"radarr": radarr()}, now=T0 + DAY)
        self.run_lm({"radarr": radarr(RING)}, now=T0 + 2 * DAY)
        self.assertEqual(self.series()[("radarr", "The Ring Thing (2004)", "0")]["since"], T0 + 2 * DAY)


if __name__ == "__main__":
    unittest.main()
