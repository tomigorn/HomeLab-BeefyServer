import datetime
import unittest

import rows

MIN = 600_000_000


def item(iid, **kw):
    base = {"Id": iid, "Name": iid, "Type": "Movie", "Tags": [], "Genres": [], "ProductionLocations": [], "People": [], "RunTimeTicks": 120 * MIN,
            "CommunityRating": 6.0, "OfficialRating": "", "ProductionYear": 2000, "DateCreated": "2026-01-01T00:00:00Z", "ProviderIds": {}, "UserData": {}}
    base.update(kw)
    return base


class Global(unittest.TestCase):
    def test_true_story_by_tag(self):
        items = [item("a", Tags=["based on true story"]), item("b", Tags=["Biography"]), item("c")]
        self.assertEqual(rows.true_story(items), ["a", "b"])

    def test_binge_series_with_two_seasons_or_miniseries(self):
        items = [item("s1", Type="Series", ChildCount=2), item("s2", Type="Series", ChildCount=5), item("s3", Type="Series", ChildCount=4, Tags=["miniseries"]), item("m")]
        self.assertEqual(rows.binge(items), ["s1", "s3"])

    def test_under_100_minutes_movies_only(self):
        items = [item("a", RunTimeTicks=95 * MIN), item("b", RunTimeTicks=100 * MIN), item("s", Type="Series", RunTimeTicks=40 * MIN)]
        self.assertEqual(rows.under_100(items), ["a"])

    def test_uhd_hdr_needs_both(self):
        hdr = [{"Type": "Video", "Width": 3840, "VideoRange": "HDR"}]
        items = [item("a", MediaStreams=hdr), item("b", MediaStreams=[{"Type": "Video", "Width": 3840, "VideoRange": "SDR"}]), item("c", MediaStreams=[{"Type": "Video", "Width": 1920, "VideoRange": "HDR"}])]
        self.assertEqual(rows.uhd_hdr(items), ["a"])

    def test_dach_by_country(self):
        items = [item("a", ProductionLocations=["Switzerland"]), item("b", ProductionLocations=["Austria", "France"]), item("c", ProductionLocations=["France"])]
        self.assertEqual(rows.by_country(items, rows.DACH), ["a", "b"])

    def test_animation_for_grown_ups_drops_family_and_kids_ratings(self):
        items = [item("a", Genres=["Animation", "Drama"], OfficialRating="R"), item("b", Genres=["Animation", "Family"]), item("c", Genres=["Animation"], OfficialRating="G"), item("d")]
        self.assertEqual(rows.grown_up_animation(items), ["a"])

    def test_decade(self):
        items = [item("a", ProductionYear=1985), item("b", ProductionYear=1999), item("c", ProductionYear=2001)]
        self.assertEqual(rows.decade(items, 1980), ["a"])

    def test_awards_by_tmdb_id_or_tag(self):
        items = [item("a", ProviderIds={"Tmdb": "1"}), item("b", Tags=["award winner"]), item("c", ProviderIds={"Tmdb": "2"})]
        self.assertEqual(rows.awards(items, {"1"}), ["a", "b"])

    def test_seasonal_tags(self):
        items = [item("x", Tags=["christmas"]), item("h", Tags=["halloween"])]
        self.assertEqual(rows.seasonal(items, 12)[1], ["x"])
        self.assertEqual(rows.seasonal(items, 10)[1], ["h"])
        self.assertIsNone(rows.seasonal(items, 5))


class PerUser(unittest.TestCase):
    def test_hidden_gems_unplayed_best_first(self):
        items = [item("a", CommunityRating=8.0), item("b", CommunityRating=7.6, UserData={"Played": True}), item("c", CommunityRating=7.0), item("d", CommunityRating=9.0)]
        self.assertEqual(rows.hidden_gems(items), ["d", "a"])

    def test_watch_again_played_and_liked(self):
        items = [item("a", CommunityRating=7.5, UserData={"Played": True}), item("b", CommunityRating=5, UserData={"Played": True, "IsFavorite": True}),
                 item("c", CommunityRating=5, UserData={"Played": True}), item("d", CommunityRating=9)]
        self.assertEqual(sorted(rows.watch_again(items)), ["a", "b"])

    def test_top_picks_ranks_by_how_many_seeds_point_at_it(self):
        similar = {"s1": ["x", "y"], "s2": ["y", "z"]}
        items = [item("s1", UserData={"Played": True, "IsFavorite": True}), item("s2", UserData={"Played": True}), item("x"), item("y"), item("z", UserData={"Played": True})]
        self.assertEqual(rows.top_picks(items, lambda i: similar.get(i, [])), ["y", "x"])


class Rotation(unittest.TestCase):
    def test_three_rotating_rows_change_each_month(self):
        jan = rows.rotating(datetime.date(2027, 1, 5))
        feb = rows.rotating(datetime.date(2027, 2, 5))
        self.assertEqual(len(jan), 3)
        self.assertNotEqual(jan, feb)
        self.assertTrue(set(jan) <= set(rows.ROTATING))

    def test_pick_person_rotates_among_people_with_enough_titles(self):
        people = {"A": ["1", "2", "3"], "B": ["4", "5", "6"], "C": ["7"]}
        picks = {rows.pick_person(people, 3, datetime.date(2027, m, 1))[0] for m in range(1, 5)}
        self.assertEqual(picks, {"A", "B"})


class Layout(unittest.TestCase):
    def test_order_seasonal_hidden_out_of_season(self):
        sets = {k: f"id-{k}" for k in ("gems", "watchagain", "toppicks", "awards", "sagas", "truestory", "binge", "seasonal", "decade", "dach", "under100")}
        names = {k: k for k in sets}
        may = rows.layout(sets, names, ["decade", "dach", "under100"], seasonal_on=False, server="https://b")
        kinds = [s.get("pluginDisplayText") or s["type"] for s in may]
        self.assertEqual(kinds[:5], ["resume", "nextup", "favoritemovies", "favoriteseries", "latestmedia"])
        self.assertNotIn("seasonal", kinds)
        self.assertEqual(kinds.index("binge"), kinds.index("truestory") + 1)
        dec = rows.layout(sets, names, ["decade", "dach", "under100"], seasonal_on=True, server="https://b")
        kinds = [s.get("pluginDisplayText") or s["type"] for s in dec]
        self.assertEqual(kinds.index("seasonal"), kinds.index("truestory") + 1)
        self.assertEqual([s["order"] for s in dec], list(range(len(dec))))


if __name__ == "__main__":
    unittest.main()
