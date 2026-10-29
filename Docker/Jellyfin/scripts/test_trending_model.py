import pathlib
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import popular  # noqa: E402
import trending_model as tm  # noqa: E402

NOW = "⬇ Download movie now"
NIGHT = "🌙 Download movie tonight · lower power cost"


def info(kind, tmdb, title, year, overview="An overview.", **kw):
    d = {"k": kind, "id": tmdb, "t": {"en": title, "de": title + " DE"}, "o": {"en": overview, "de": "Eine Übersicht."}, "y": year,
         "g": {"en": ["Drama", "Thriller"], "de": ["Drama", "Thriller"]}, "rt": 120, "ns": None, "cert": "16", "p": {"en": "/p.jpg", "de": "/p.jpg"},
         "b": "/b.jpg", "imdb": "tt0000001", "sc": {"imdb": 7.5, "rtc": 93, "rta": 84, "tmdb": 7.4},
         "cast": [{"n": "Jane Doe", "c": "Hero", "p": "/jane.jpg"}, {"n": "John Roe", "c": "", "p": None}],
         "v": [{"key": "aaaaaaaaaaa", "name": "Trailer", "type": "Trailer", "size": 1080, "lang": "en"}, {"key": "bbbbbbbbbbb", "name": "Teaser", "type": "Teaser", "size": 720, "lang": "de"}],
         "ref": f"{kind}/{tmdb}"}
    d.update(kw)
    return d


def item(kind, tmdb, rank, title, year=2026, o=False, f=None, x=None, season=None, d=None):
    return {"k": kind, "id": tmdb, "s": season, "r": rank, "y": year, "t": {"en": title, "de": title}, "p": {"en": "/p.jpg", "de": "/p.jpg"},
            "o": o, "f": f, "n": not o and not f, "c": "2026-10-29", **({"d": d} if d else {}), **({"x": x} if x else {})}


def unmatched(kind, rank, title):
    return {"k": kind, "id": None, "s": None, "r": rank, "y": None, "t": {"en": title, "de": title}, "p": {"en": None, "de": None},
            "o": False, "f": None, "n": True, "c": None, "u": True}


def row(rid, kind, name, items, logo=None, typ="service"):
    return {"id": rid, "kind": kind, "type": typ, "name": {"en": name, "de": name}, "logo": logo, "items": items}


def lists(movies=(), shows=(), rows=()):
    return {"updated": "2026-10-29T04:39:00", "week": "2026-10-20", "lists": "2026-10-27T04:39:00", "home": {"movies": list(movies), "shows": list(shows)}, "rows": list(rows)}


def coming(*entries):
    return {"updated": "2026-10-29T04:39:00", "items": [{"ref": f"{k}/{t}", "kind": k, "tmdb": t, "d": d, "date": date, "pop": 10.0} for k, t, d, date in entries]}


class CollectionSpecs(unittest.TestCase):
    def test_collection_specs_37_names_and_order(self):
        rows = []
        names = dict(popular.SERVICES)
        for rid, kind, src in popular.row_ids(popular.SERVICES, popular.GENRES):
            if rid.startswith("service-"):
                rows.append(row(rid, kind, names[src], [], logo=f"/logo{src}.png"))
            else:
                rows.append(row(rid, kind, src[1], [], typ="genre"))
        specs = tm.collection_specs(lists(rows=rows))
        self.assertEqual(len(specs), 37)
        self.assertEqual([s["name"] for s in specs][:4], ["Top 10 Switzerland · Movies", "Top 10 Switzerland · Shows", "Coming soon", "Netflix · Movies"])
        self.assertEqual(specs[0]["sort"], "01 Top 10 Switzerland · Movies")
        self.assertEqual([s["key"] for s in specs][:5], ["home-movies", "home-shows", "coming", "service-8-movie", "service-8-tv"])
        self.assertEqual(specs[4]["name"], "Netflix · Shows")
        self.assertEqual(specs[3]["logo"], "/logo8.png")
        self.assertIsNone(specs[0]["logo"])
        self.assertEqual(specs[-1]["name"], "Romance · Movies")
        self.assertEqual(specs[-1]["sort"], "37 Romance · Movies")


class Plan(unittest.TestCase):
    def test_owned_title_is_real_member(self):
        l = lists(movies=[item("movie", 121, 1, "Owned Film", o=True)])
        p = tm.plan(l, coming(), {}, {"movie/121": "jf121"}, {})
        self.assertEqual(p.placeholders, {})
        members = dict((s["key"], m) for s, m in p.collections)
        self.assertEqual(members["home-movies"], [("real", "jf121")])

    def test_owned_since_the_list_ran_is_real_member(self):
        l = lists(movies=[item("movie", 121, 1, "New Film", f="1080p", x="tok121")])
        p = tm.plan(l, coming(), {}, {"movie/121": "jf121"}, {})
        self.assertEqual(p.placeholders, {})
        self.assertEqual(dict((s["key"], m) for s, m in p.collections)["home-movies"], [("real", "jf121")])

    def test_art_refs_first_members_posters_and_backdrops(self):
        l = lists(movies=[item("movie", 1, 1, "One", d="d1"), item("movie", 2, 2, "Two", o=True), item("movie", 3, 3, "Three"), item("movie", 4, 4, "Four"), item("movie", 5, 5, "Five")])
        details = {"d1": info("movie", 1, "One", 2026, b="/b1.jpg")}
        p = tm.plan(l, coming(), details, {"movie/3": "jf3"}, {})
        self.assertEqual(p.art["home-movies"], [{"p": "/p.jpg", "b": "/b1.jpg"}, {"p": "/p.jpg", "b": None}, {"p": "/p.jpg", "b": None}, {"p": "/p.jpg", "b": None}])
        self.assertEqual(p.art["home-shows"], [])

    def test_title_in_two_lists_one_placeholder_sorted_by_first_list(self):
        l = lists(movies=[item("movie", 5, 3, "Shared Film", f="2160p", x="tok5")],
                  rows=[row("service-8-movie", "movie", "Netflix", [item("movie", 9, 1, "Other"), item("movie", 5, 7, "Shared Film", f="2160p", x="tok5")])])
        p = tm.plan(l, coming(), {}, {}, {})
        shared = [ph for ph in p.placeholders.values() if ph.key == "movie/5"]
        self.assertEqual(len(shared), 1)
        ph = shared[0]
        self.assertEqual(ph.sort_title, "01-03 Shared Film")
        members = dict((s["key"], m) for s, m in p.collections)
        self.assertEqual(members["home-movies"], [("ph", ph.folder)])
        self.assertEqual(members["service-8-movie"], [("ph", p.placeholders["Other (2026)"].folder), ("ph", ph.folder)])
        self.assertEqual(p.placeholders["Other (2026)"].sort_title, "04-01 Other")

    def test_states_and_files(self):
        l = lists(movies=[item("movie", 1, 1, "Found", f="2160p", x="tok1", d="d1"), item("movie", 2, 2, "Nothing"), item("movie", 3, 3, "Req Night", f="1080p", x="tok3"),
                          item("movie", 4, 4, "Downloading", f="1080p", x="tok4"), item("movie", 5, 5, "Declined", f="1080p", x="tok5")],
                  shows=[item("tv", 10, 1, "Show", f="1080p", x="tok10", season=2)])
        c = coming(("movie", 20, "d20", "2026-11-04"))
        details = {"d1": info("movie", 1, "Found", 2026), "d20": info("movie", 20, "Upcoming", 2026)}
        states = {"movie/3": ("requested", "night"), "movie/4": ("downloading", "now"), "movie/5": ("declined", None), "movie/1": (None, None)}
        p = tm.plan(l, c, details, {}, states)
        by = {ph.key: ph for ph in p.placeholders.values()}

        ph = by["movie/1"]
        self.assertEqual((ph.state, ph.badge, ph.mode, ph.x), ("found", "found", None, "tok1"))
        self.assertEqual(ph.files, {f"{ph.folder} - {NOW}.mp4": "now", f"{ph.folder} - {NIGHT}.mp4": "night"})
        self.assertEqual(ph.info, details["d1"])

        ph = by["tv/10"]
        self.assertEqual((ph.state, ph.badge, ph.season), ("found", "found", 2))
        self.assertEqual(ph.files, {f"{ph.folder} - ⬇ Download series now.mp4": "now", f"{ph.folder} - 🌙 Download series tonight · lower power cost.mp4": "night"})

        ph = by["movie/2"]
        self.assertEqual((ph.state, ph.badge, ph.x), ("none", "none", None))
        self.assertEqual(ph.files, {f"{ph.folder}.mp4": "unavailable"})

        ph = by["movie/3"]
        self.assertEqual((ph.state, ph.badge, ph.mode), ("requested", "requested", "night"))
        self.assertEqual(ph.files, {f"{ph.folder}.mp4": "requested"})

        ph = by["movie/4"]
        self.assertEqual((ph.state, ph.badge, ph.mode), ("downloading", "downloading", "now"))
        self.assertEqual(ph.files, {f"{ph.folder}.mp4": "requested"})

        ph = by["movie/5"]
        self.assertEqual((ph.state, ph.badge), ("none", "none"))
        self.assertEqual(ph.files, {f"{ph.folder}.mp4": "unavailable"})

        ph = by["movie/20"]
        self.assertEqual((ph.state, ph.badge, ph.title, ph.coming_date), ("coming", None, "Upcoming", "2026-11-04"))
        self.assertEqual(ph.files, {f"{ph.folder}.mp4": "coming"})
        self.assertEqual(ph.sort_title, "03-01 Upcoming")

    def test_popular_entry_wins_over_coming(self):
        l = lists(rows=[row("service-8-movie", "movie", "Netflix", [item("movie", 20, 2, "Upcoming", f="1080p", x="tok20")])])
        p = tm.plan(l, coming(("movie", 20, "d20", "2026-11-04")), {"d20": info("movie", 20, "Upcoming", 2026)}, {}, {})
        (ph,) = p.placeholders.values()
        self.assertEqual((ph.state, ph.x, ph.sort_title, ph.coming_date), ("found", "tok20", "03-01 Upcoming", "2026-11-04"))
        members = dict((s["key"], m) for s, m in p.collections)
        self.assertEqual(members["coming"], [("ph", ph.folder)])
        self.assertEqual(members["service-8-movie"], [("ph", ph.folder)])

    def test_coming_owned_is_real_and_without_details_skipped(self):
        c = coming(("tv", 30, "d30", "2026-11-04"), ("movie", 31, "missing", "2026-11-05"))
        p = tm.plan(lists(), c, {"d30": info("tv", 30, "Running Show", 2020)}, {"tv/30": "jf30"}, {})
        self.assertEqual(p.placeholders, {})
        self.assertEqual(dict((s["key"], m) for s, m in p.collections)["coming"], [("real", "jf30")])

    def test_unmatched_items_skipped(self):
        l = lists(shows=[unmatched("tv", 1, "Mystery"), item("tv", 2, 2, "Known")])
        p = tm.plan(l, coming(), {}, {}, {})
        self.assertEqual([ph.title for ph in p.placeholders.values()], ["Known"])
        self.assertEqual(len(dict((s["key"], m) for s, m in p.collections)["home-shows"]), 1)

    def test_collections_cover_every_spec_in_order(self):
        l = lists(rows=[row("genre-drama-movie", "movie", "Drama", [], typ="genre")])
        p = tm.plan(l, coming(), {}, {}, {})
        self.assertEqual([s["key"] for s, _ in p.collections], ["home-movies", "home-shows", "coming", "genre-drama-movie"])


class Nfo(unittest.TestCase):
    def ph(self, state="found", mode=None, d=None, date=None):
        l = lists(movies=[item("movie", 1, 4, "Film", f="1080p", x="tok", d="d1")])
        states = {"movie/1": (state, mode)} if state in ("requested", "downloading") else {}
        if state == "none":
            l["home"]["movies"][0].update(f=None, n=True, x=None)
        p = tm.plan(l, coming(("movie", 1, "d1", date or "2026-11-04")), {"d1": d or info("movie", 1, "Film", 2026, overview="English overview.")}, {}, states)
        return next(iter(p.placeholders.values()))

    def test_nfo_fields(self):
        root = ET.fromstring(tm.nfo(self.ph()).encode("utf-8"))
        self.assertEqual(root.tag, "movie")
        self.assertEqual(root.findtext("title"), "Film")
        self.assertEqual(root.findtext("sorttitle"), "01-04 Film")
        self.assertEqual(root.findtext("year"), "2026")
        self.assertEqual(root.findtext("plot"), tm.PREFIX["found"] + "\n\nEnglish overview.")
        self.assertEqual(tm.PREFIX["found"], "⬇ Not downloaded — choose a version (⋯ / Version) and press Play")
        self.assertEqual([g.text for g in root.findall("genre")], ["Drama", "Thriller"])
        self.assertEqual(root.findtext("rating"), "7.4")
        self.assertEqual(root.findtext("criticrating"), "93")
        self.assertEqual(root.findtext("mpaa"), "16")
        self.assertEqual([t.text for t in root.findall("trailer")], ["plugin://plugin.video.youtube/?action=play_video&videoid=aaaaaaaaaaa",
                                                                   "plugin://plugin.video.youtube/?action=play_video&videoid=bbbbbbbbbbb"])
        actors = root.findall("actor")
        self.assertEqual([(a.findtext("name"), a.findtext("role"), a.findtext("thumb")) for a in actors],
                         [("Jane Doe", "Hero", "https://image.tmdb.org/t/p/w185/jane.jpg"), ("John Roe", None, None)])
        self.assertIsNone(root.find("uniqueid"))

    def test_nfo_prefixes_per_state(self):
        plot = lambda ph: ET.fromstring(tm.nfo(ph).encode()).findtext("plot")
        self.assertTrue(plot(self.ph("requested", "night")).startswith("✓ Requested — downloading tonight\n\n"))
        self.assertTrue(plot(self.ph("requested", "now")).startswith("✓ Requested — downloading now\n\n"))
        self.assertTrue(plot(self.ph("downloading", None)).startswith("✓ Requested — downloading now\n\n"))
        self.assertTrue(plot(self.ph("none")).startswith("Not able to download yet — checked daily\n\n"))
        self.assertEqual(set(tm.PREFIX.values()), {"⬇ Not downloaded — choose a version (⋯ / Version) and press Play", "✓ Requested — downloading tonight",
                                                   "✓ Requested — downloading now", "Not able to download yet — checked daily"})

    def test_coming_plot_prefix(self):
        p = tm.plan(lists(), coming(("movie", 20, "d20", "2026-11-04")), {"d20": info("movie", 20, "Upcoming", 2026, overview="Soon.")}, {}, {})
        ph = next(iter(p.placeholders.values()))
        self.assertEqual(ET.fromstring(tm.nfo(ph).encode()).findtext("plot"), "Coming soon — 4 Nov 2026\n\nSoon.")

    def test_nfo_without_details(self):
        l = lists(movies=[item("movie", 1, 1, "Bare <&> Film", year=None)])
        ph = next(iter(tm.plan(l, coming(), {}, {}, {}).placeholders.values()))
        root = ET.fromstring(tm.nfo(ph).encode())
        self.assertEqual(root.findtext("title"), "Bare <&> Film")
        self.assertEqual(root.findtext("plot"), tm.PREFIX["none"])
        self.assertIsNone(root.find("year"))
        self.assertIsNone(root.find("rating"))
        self.assertTrue(tm.nfo(ph).startswith("<?xml"))


class FolderNames(unittest.TestCase):
    def test_folder_names_unique_and_safe(self):
        name = tm.folder_name("AC/DC: Live", 2024, set())
        self.assertFalse(set('/:*?"<>|') & set(name), name)
        self.assertTrue(name.endswith(" (2024)"))

        def names():
            l = lists(movies=[item("movie", 7, 1, "X", year=2024)], shows=[item("tv", 3, 1, "X", year=2024)])
            p = tm.plan(l, coming(), {}, {}, {})
            return {ph.key: ph.folder for ph in p.placeholders.values()}
        first = names()
        self.assertEqual(first, {"movie/7": "X (2024)", "tv/3": "X (2024) (Series)"})
        self.assertEqual(names(), first)

    def test_folder_name_collisions_and_edges(self):
        taken = set()
        self.assertEqual(tm.folder_name("X", 2024, taken), "X (2024)")
        self.assertEqual(tm.folder_name("x", 2024, taken), "x (2024) (2)")
        self.assertEqual(tm.folder_name("X", 2024, taken, kind="tv"), "X (2024) (Series)")
        self.assertEqual(tm.folder_name("X", 2024, taken, kind="tv"), "X (2024) (Series) (2)")
        self.assertEqual(tm.folder_name("...", None, set()), "Untitled")
        self.assertEqual(tm.folder_name("A\\B\tC.", None, set()), "A B C")
        long = tm.folder_name("é" * 300, 2024, set())
        self.assertLessEqual(len(long.encode()), tm.FOLDER_MAX_BYTES)
        self.assertTrue(long.endswith(" (2024)"))


if __name__ == "__main__":
    unittest.main()
