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


    def test_new_on_the_server_is_the_last_30_days_newest_first(self):
        day = datetime.date(2026, 11, 17)
        items = [item("old", DateCreated="2026-09-01T00:00:00Z"), item("a", DateCreated="2026-11-01T00:00:00Z"), item("b", DateCreated="2026-11-10T00:00:00Z")]
        self.assertEqual(rows.new_on_server(items, day), ["b", "a"])


class PerUser(unittest.TestCase):
    def test_hidden_gems_unplayed(self):
        items = [item("a", CommunityRating=8.0), item("b", CommunityRating=7.6, UserData={"Played": True}), item("c", CommunityRating=7.0), item("d", CommunityRating=9.0)]
        self.assertEqual(sorted(rows.hidden_gems(items)), ["a", "d"])

    def test_watch_again_played_and_liked(self):
        items = [item("a", CommunityRating=7.5, UserData={"Played": True}), item("b", CommunityRating=5, UserData={"Played": True, "IsFavorite": True}),
                 item("c", CommunityRating=5, UserData={"Played": True}), item("d", CommunityRating=9)]
        self.assertEqual(sorted(rows.watch_again(items)), ["a", "b"])

    def test_watch_again_last_played_first(self):
        items = [item("a", CommunityRating=8, UserData={"Played": True, "LastPlayedDate": "2026-11-01T00:00:00Z"}),
                 item("b", CommunityRating=8, UserData={"Played": True, "LastPlayedDate": "2026-11-15T00:00:00Z"})]
        self.assertEqual(rows.watch_again(items), ["b", "a"])

    def test_top_picks_ranks_by_how_many_seeds_point_at_it(self):
        similar = {"s1": ["x", "y"], "s2": ["y", "z"]}
        items = [item("s1", UserData={"Played": True, "IsFavorite": True}), item("s2", UserData={"Played": True}), item("x"), item("y"), item("z", UserData={"Played": True})]
        self.assertEqual(rows.top_picks(items, lambda i: similar.get(i, [])), ["y", "x"])


class Shuffle(unittest.TestCase):
    IDS = [f"id{n:02d}" for n in range(40)]

    def test_same_seed_same_order_other_day_other_order(self):
        a = rows.shuffled(self.IDS, "2026-11-17:uhd")
        self.assertEqual(a, rows.shuffled(self.IDS, "2026-11-17:uhd"))
        self.assertNotEqual(a, rows.shuffled(self.IDS, "2026-11-18:uhd"))
        self.assertEqual(sorted(a), sorted(self.IDS))

    def test_not_alphabetical(self):
        self.assertNotEqual(rows.shuffled(self.IDS, "2026-11-17:uhd")[:5], self.IDS[:5])

    def test_seed_per_day_row_and_user(self):
        day = datetime.date(2026, 11, 17)
        self.assertEqual(rows.seed(day, "gems", "u1"), "2026-11-17:gems:u1")
        self.assertEqual(rows.seed(day, "uhd"), "2026-11-17:uhd:")

    def test_which_rows_keep_their_natural_order(self):
        self.assertEqual(rows.NATURAL, {"new", "toppicks", "watchagain"})


class Privacy(unittest.TestCase):
    def test_per_user_sets_of_disabled_users_stay_hidden_too(self):
        users = [{"Id": "a", "Name": "a", "Policy": {}}, {"Id": "b", "Name": "b", "Policy": {"IsDisabled": True}}, {"Id": "c", "Name": "beta-admin", "Policy": {}}]
        self.assertEqual(rows.blocked_for(users[0], users), {"beta-row-u-b", "beta-row-u-c"})
        self.assertEqual(rows.blocked_for(users[2], users), {"beta-row-u-a", "beta-row-u-b", "beta-row-u-c"})


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
        self.assertEqual(kinds[:4], ["resume", "favoritemovies", "favoriteseries", "latestmedia"])
        sets["new"], names["new"] = "id-new", "New on the Server"
        kinds = [s.get("pluginDisplayText") or s["type"] for s in rows.layout(sets, names, ["decade"], seasonal_on=False, server="https://b")]
        self.assertEqual(kinds[:4], ["resume", "favoritemovies", "favoriteseries", "New on the Server"])
        self.assertNotIn("nextup", kinds)
        self.assertNotIn("seasonal", kinds)
        self.assertEqual(kinds.index("binge"), kinds.index("truestory") + 1)
        dec = rows.layout(sets, names, ["decade", "dach", "under100"], seasonal_on=True, server="https://b")
        kinds = [s.get("pluginDisplayText") or s["type"] for s in dec]
        self.assertEqual(kinds.index("seasonal"), kinds.index("truestory") + 1)
        self.assertEqual([s["order"] for s in dec], list(range(len(dec))))


class Web(unittest.TestCase):
    def test_web_rows_follow_the_moonfin_order(self):
        names = dict(rows.NAMES, seasonal="Christmas", decade="Decade Night: 80s")
        out = rows.web_rows(["decade", "dach", "under100"], True, names)
        self.assertEqual([r["key"] for r in out][:2], ["favourites", "new"])
        self.assertEqual(out[0]["kind"], "favourites")
        out = out[2:]
        self.assertEqual([r["key"] for r in out], ["toppicks", "gems", "watchagain", "awards", "sagas", "truestory", "seasonal", "binge", "decade", "dach", "under100"])
        self.assertEqual(out[6]["name"], "Christmas")
        self.assertNotIn("seasonal", [r["key"] for r in rows.web_rows(["decade", "dach", "under100"], False, names)])


class Art(unittest.TestCase):
    def test_art_due_when_new_or_first_posters_changed(self):
        state = {"s1": rows.art_key(["a", "b", "c", "d"])}
        self.assertFalse(rows.art_due(state, "s1", ["a", "b", "c", "x"]))
        self.assertTrue(rows.art_due(state, "s1", ["b", "a", "c"]))
        self.assertTrue(rows.art_due(state, "s2", ["a"]))


if __name__ == "__main__":
    unittest.main()
