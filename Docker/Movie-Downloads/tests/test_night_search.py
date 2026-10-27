#!/usr/bin/env python3
"""scripts/night-search.py: date windows, monitored filters, rotation, Treasure Maps budget, season-vs-episode batching, against fake Radarr/Sonarr.
Usage: python3 tests/test_night_search.py"""
import datetime
import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ns", ROOT / "scripts" / "night-search.py")
ns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ns)

NOW = datetime.datetime(2026, 10, 27, 3, 17, tzinfo=datetime.timezone.utc)


def ago(days=0, hours=0):
    return (NOW - datetime.timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ep(i, series=1, season=1, aired=2, **kw):
    e = {"id": i, "seriesId": series, "seasonNumber": season, "episodeNumber": i, "monitored": True, "hasFile": False,
         "airDateUtc": ago(days=aired), "lastSearchTime": None,
         "series": {"id": series, "title": f"S{series}", "monitored": True, "seasons": [{"seasonNumber": season, "monitored": True}]}}
    e.update(kw)
    return e


def movie(i, digital=None, physical=None, **kw):
    m = {"id": i, "title": f"M{i}", "monitored": True, "hasFile": False, "isAvailable": True, "lastSearchTime": None}
    if digital is not None:
        m["digitalRelease"] = ago(days=digital)
    if physical is not None:
        m["physicalRelease"] = ago(days=physical)
    m.update(kw)
    return m


class NewEpisodes(unittest.TestCase):
    def ids(self, eps, queued=()):
        return [e["id"] for e in ns.new_episodes(eps, NOW, days=14, min_gap_hours=20, queued=set(queued))]

    def test_window_is_the_last_14_days_aired_only(self):
        self.assertEqual(self.ids([ep(1, aired=2), ep(2, aired=13), ep(3, aired=15), ep(4, aired=-1)]), [1, 2])

    def test_skips_files_unmonitored_episode_series_and_season(self):
        eps = [ep(1, hasFile=True), ep(2, monitored=False), ep(3), ep(4), ep(5, season=2)]
        eps[3]["series"]["monitored"] = False
        eps[4]["series"]["seasons"] = [{"seasonNumber": 2, "monitored": False}]
        self.assertEqual(self.ids(eps), [3])

    def test_skips_queued_and_searched_within_the_gap(self):
        eps = [ep(1), ep(2, lastSearchTime=ago(hours=3)), ep(3, lastSearchTime=ago(hours=30)), ep(4)]
        self.assertEqual(self.ids(eps, queued=[4]), [1, 3])

    def test_least_recently_searched_first_never_searched_first(self):
        eps = [ep(1, lastSearchTime=ago(days=2)), ep(2), ep(3, lastSearchTime=ago(days=5))]
        self.assertEqual(self.ids(eps), [2, 3, 1])


class NewFilms(unittest.TestCase):
    def ids(self, ms, queued=()):
        return [m["id"] for m in ns.new_films(ms, NOW, days=60, min_gap_hours=20, queued=set(queued))]

    def test_released_digital_or_physical_within_60_days(self):
        ms = [movie(1, digital=10), movie(2, physical=59), movie(3, digital=61), movie(4, digital=-3), movie(5), movie(6, digital=90, physical=20)]
        self.assertEqual(self.ids(ms), [1, 2, 6])

    def test_needs_monitored_available_and_no_file(self):
        ms = [movie(1, digital=5, hasFile=True), movie(2, digital=5, monitored=False), movie(3, digital=5, isAvailable=False), movie(4, digital=5)]
        self.assertEqual(self.ids(ms), [4])

    def test_skips_queued_and_recently_searched_rotates(self):
        ms = [movie(1, digital=5, lastSearchTime=ago(days=3)), movie(2, digital=5), movie(3, digital=5, lastSearchTime=ago(hours=2)), movie(4, digital=5)]
        self.assertEqual(self.ids(ms, queued=[4]), [2, 1])


class Batching(unittest.TestCase):
    def test_single_episode_is_an_episode_search(self):
        u = ns.episode_units([ep(1)], season_min=3, cost_episode=1, cost_season=3)
        self.assertEqual([(x.command, x.body, x.cost) for x in u], [("EpisodeSearch", {"episodeIds": [1]}, 1)])

    def test_two_of_one_season_stay_one_episode_command(self):
        u = ns.episode_units([ep(1), ep(2)], season_min=3, cost_episode=1, cost_season=3)
        self.assertEqual([(x.command, x.body, x.cost) for x in u], [("EpisodeSearch", {"episodeIds": [1, 2]}, 2)])

    def test_three_of_one_season_become_a_season_search(self):
        u = ns.episode_units([ep(1), ep(2), ep(3)], season_min=3, cost_episode=1, cost_season=3)
        self.assertEqual([(x.command, x.body, x.cost) for x in u], [("SeasonSearch", {"seriesId": 1, "seasonNumber": 1}, 3)])

    def test_seasons_and_series_are_separate_units(self):
        eps = [ep(1), ep(2), ep(3), ep(4, season=2), ep(5, series=2)]
        u = ns.episode_units(eps, season_min=3, cost_episode=1, cost_season=3)
        self.assertEqual([(x.command, x.cost) for x in u], [("SeasonSearch", 3), ("EpisodeSearch", 1), ("EpisodeSearch", 1)])

    def test_no_season_search_when_disabled(self):
        u = ns.episode_units([ep(1), ep(2), ep(3)], season_min=None, cost_episode=1, cost_season=3)
        self.assertEqual([(x.command, x.cost) for x in u], [("EpisodeSearch", 3)])


class Budget(unittest.TestCase):
    def usage(self, used, limit=100, age=60):
        gen = (NOW - datetime.timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"ok": True, "generated": gen, "indexers": [{"name": "Treasure Maps", "searches": {"used": used, "limit": limit}}]}

    def test_cap_when_plenty_left(self):
        self.assertEqual(ns.tm_budget(self.usage(10), NOW, cap=30, pct=70, fallback=10), 30)

    def test_pct_ceiling_minus_used(self):
        self.assertEqual(ns.tm_budget(self.usage(55), NOW, cap=30, pct=70, fallback=10), 15)

    def test_zero_when_above_ceiling(self):
        self.assertEqual(ns.tm_budget(self.usage(80), NOW, cap=30, pct=70, fallback=10), 0)

    def test_no_search_limit_gives_the_cap(self):
        self.assertEqual(ns.tm_budget(self.usage(5000, limit=None), NOW, cap=200, pct=70, fallback=10), 200)

    def test_stale_or_missing_bar_uses_fallback(self):
        self.assertEqual(ns.tm_budget(self.usage(10, age=1200), NOW, cap=30, pct=70, fallback=10), 10)
        self.assertEqual(ns.tm_budget(None, NOW, cap=30, pct=70, fallback=10), 10)
        self.assertEqual(ns.tm_budget({"ok": True, "generated": ago(), "indexers": []}, NOW, cap=30, pct=70, fallback=10), 10)


class Unit(unittest.TestCase):
    def u(self, cost, name="x"):
        return ns.Unit(name, "EpisodeSearch", {"episodeIds": [1]}, cost, name)

    def test_applies_budget_in_order_and_reports_skipped(self):
        units = [self.u(3, "a"), self.u(3, "b"), self.u(3, "c")]
        chosen, skipped = ns.apply_budget(units, 7)
        self.assertEqual(([x.label for x in chosen], [x.label for x in skipped]), (["a", "b"], ["c"]))

    def test_cheaper_later_unit_still_fits(self):
        chosen, skipped = ns.apply_budget([self.u(3, "a"), self.u(5, "b"), self.u(1, "c")], 4)
        self.assertEqual(([x.label for x in chosen], [x.label for x in skipped]), (["a", "c"], ["b"]))

    def test_zero_budget_searches_nothing(self):
        chosen, skipped = ns.apply_budget([self.u(1)], 0)
        self.assertEqual((chosen, len(skipped)), ([], 1))


class Fake:
    def __init__(self):
        self.posts, self.gets = {"radarr": [], "sonarr": []}, []
        self.movies = [movie(1, digital=5), movie(2, digital=100)]
        self.cutoff_movies = [movie(10), movie(11), movie(12), movie(13), movie(14), movie(15), movie(16)]
        self.episodes = [ep(1), ep(2), ep(3)]
        self.cutoff_episodes = [ep(20, season=4), ep(21, season=5), ep(22, season=6, series=3), ep(23, season=7, series=4), ep(24, season=8, series=5), ep(25, season=9, series=6)]
        self.polls = 0

    def api(self, app):
        def call(method, path, body=None):
            self.gets.append((app, method, path))
            if method == "POST":
                self.posts[app].append(body)
                return {"id": len(self.posts[app])}
            if path.startswith("/command/"):
                self.polls += 1
                return {"status": "completed" if self.polls > 1 else "started"}
            if path.startswith("/queue"):
                return {"records": []}
            if app == "radarr" and path == "/movie":
                return self.movies
            if app == "radarr" and path.startswith("/wanted/cutoff"):
                return {"records": self.cutoff_movies}
            if app == "sonarr" and path.startswith("/calendar"):
                return self.episodes
            if app == "sonarr" and path.startswith("/wanted/cutoff"):
                return {"records": self.cutoff_episodes}
            raise AssertionError((app, method, path))
        return call


class Run(unittest.TestCase):
    cfg = dict(cap=30, pct=70, fallback=10, new_ep_days=14, new_film_days=60, min_gap_hours=20, season_min=3,
               cutoff_movies=5, cutoff_episodes=5, cost_movie=1, cost_episode=1, cost_season=3, wait_max=60, poll=0)
    usage = {"ok": True, "generated": ago(), "indexers": [{"name": "Treasure Maps", "searches": {"used": 10, "limit": 100}}]}

    def go(self, f, dry=False, **over):
        lines = []
        rc = ns.run(NOW, f.api("radarr"), f.api("sonarr"), self.usage, {**self.cfg, **over}, dry_run=dry, log=lines.append, sleep=lambda s: None)
        return rc, lines

    def test_full_night_commands_in_order(self):
        f = Fake()
        rc, lines = self.go(f)
        self.assertEqual(rc, 0)
        self.assertEqual(f.posts["sonarr"][0], {"name": "SeasonSearch", "seriesId": 1, "seasonNumber": 1})
        self.assertEqual(f.posts["radarr"][0], {"name": "MoviesSearch", "movieIds": [1]})
        self.assertEqual([b["movieIds"] for b in f.posts["radarr"][1:]], [[10], [11], [12], [13], [14]])
        total = sum(len(b["episodeIds"]) for b in f.posts["sonarr"] if "episodeIds" in b)
        self.assertEqual(total, 5)
        self.assertTrue(any("TM estimate" in l for l in lines))

    def test_dry_run_posts_nothing(self):
        f = Fake()
        rc, lines = self.go(f, dry=True)
        self.assertEqual((f.posts["radarr"], f.posts["sonarr"]), ([], []))
        self.assertTrue(any(l.startswith("would search") for l in lines))

    def test_budget_exhausted_logs_skipped(self):
        f = Fake()
        rc, lines = self.go(f, fallback=0, cap=4, dry=True)
        self.assertTrue(any(l.startswith("skipped") for l in lines))
        self.assertIn("budget 4", " ".join(lines))

    def test_cutoff_excludes_titles_already_chosen_as_new(self):
        f = Fake()
        f.cutoff_movies = [movie(1, digital=5), movie(10)]
        rc, lines = self.go(f)
        self.assertEqual(f.posts["radarr"][1], {"name": "MoviesSearch", "movieIds": [10]})

    def test_waits_for_commands_to_complete(self):
        f = Fake()
        self.go(f)
        self.assertGreater(f.polls, 0)

    def test_api_error_on_one_app_does_not_stop_the_other(self):
        f = Fake()
        good = f.api("radarr")

        def broken(method, path, body=None):
            raise ns.ApiError("sonarr down")
        lines = []
        rc = ns.run(NOW, good, broken, self.usage, self.cfg, dry_run=False, log=lines.append, sleep=lambda s: None)
        self.assertEqual(rc, 1)
        self.assertTrue(f.posts["radarr"])


if __name__ == "__main__":
    unittest.main()
