#!/usr/bin/env python3
"""Builds the planned Moonfin home rows in the beta: one collection per row (tags beta-row, beta-row-<key>), per-user rows as one collection per user (tag beta-row-u-<userId>, hidden from everyone else by BlockedTags, like prod's "For you"), then every user's Moonfin home layout.
Fixed rows: Continue Watching, Favourites (built in), New on the Server (last 30 days), Top Picks for You, Hidden Gems, Watch Again (per user), Awards Season, Sagas & Franchises, Based on a True Story, Christmas (Dec) / Halloween (Oct), Binge in a Weekend.
Rotating rows: 3 of ROTATING per month, Director Spotlight and Starring pick a new person each month. Out of season the seasonal row is left out, so Binge moves up.
Order: New on the Server newest first, Top Picks by relevance, Watch Again last played first; every other row shuffled once a day (seed: date, row, user for per-user rows), the same on every device.
Usage: rows.py [--dry-run] [--month YYYY-MM | --date YYYY-MM-DD] [--counts]  (--counts prints titles per row, changes nothing; --month/--date simulate a day, run again without them to reset)"""
import collections
import base64
import datetime
import hashlib
import importlib.util
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

from beta_api import api, api_raw

HERE = pathlib.Path(__file__).resolve().parent
SERVER = "https://jellyfin-beta.holy-grail.ch"
TRENDING_PATH = "/config/trending/"
SIZE = 30
AWARDS_CACHE = pathlib.Path.home() / ".cache" / "jellyfin-beta-awards.json"
ART_STATE = pathlib.Path.home() / ".local" / "state" / "jellyfin-beta-rows-art.json"
WEB_ENTRY = "Beta rows"
# Wikidata: Academy Award for Best Picture, Golden Globe Best Motion Picture (Drama, Musical or Comedy), Palme d'Or; won (P166) or nominated (P1411).
AWARDS = ("Q102427", "Q1011509", "Q670282", "Q179808")
TRUE_STORY = {"based on true story", "based on real events", "biography", "true crime", "based on real person"}
CHRISTMAS = {"christmas", "christmas party", "santa claus", "christmas eve", "christmas tree", "holiday"}
HALLOWEEN = {"halloween", "haunted house", "ghost", "witch", "zombie", "vampire", "slasher"}
MIND = {"mind bending", "mindfuck", "twist ending", "nonlinear timeline", "time loop", "dream", "parallel world", "alternate reality", "time travel", "surrealism", "psychological thriller"}
KIDS_RATINGS = {"G", "PG", "TV-Y", "TV-Y7", "TV-G", "TV-PG", "0", "6", "FSK-0", "FSK-6", "U"}
DACH = {"Switzerland", "Germany", "Austria"}
WORLD = {"South Korea", "Japan", "France", "Sweden", "Denmark", "Norway", "Finland", "Iceland"}
ROTATING = ("director", "actor", "world", "dach", "mind", "decade", "animation", "under100", "uhd")
NAMES = {"toppicks": "Top Picks for You", "gems": "Hidden Gems", "watchagain": "Watch Again", "awards": "Awards Season", "sagas": "Sagas & Franchises",
         "truestory": "Based on a True Story", "binge": "Binge in a Weekend", "world": "World Cinema", "dach": "Made in Switzerland & DACH",
         "mind": "Mind-Benders", "animation": "Animation for Grown-ups", "under100": "Under 100 Minutes", "uhd": "4K HDR Showcase"}
USER_ROWS = ("toppicks", "gems", "watchagain")
# Rows with an order of their own (newest, relevance, last played); every other row is shuffled once a day.
NATURAL = {"new", "toppicks", "watchagain"}
BETA_ADMIN = "beta-admin"  # local test account: no rows of its own, sees no one's


def tags(i):
    return {t.lower() for t in i.get("Tags") or []}


def ids(items):
    return [i["Id"] for i in items]


def movies(items):
    return [i for i in items if i["Type"] == "Movie"]


def new_on_server(items, day):
    """Added in the last 30 days, newest first."""
    since = (day - datetime.timedelta(days=30)).isoformat()
    fresh = [i for i in items if (i.get("DateCreated") or "")[:10] >= since]
    return ids(sorted(fresh, key=lambda i: i.get("DateCreated") or "", reverse=True))


def true_story(items):
    return ids(i for i in items if tags(i) & TRUE_STORY)


def binge(items):
    return ids(i for i in items if i["Type"] == "Series" and ((i.get("ChildCount") or 99) <= 2 or tags(i) & {"miniseries", "limited series"}))


def under_100(items):
    return ids(i for i in movies(items) if 0 < (i.get("RunTimeTicks") or 0) < 100 * 600_000_000)


def uhd_hdr(items):
    def ok(i):
        v = next((s for s in i.get("MediaStreams") or [] if s.get("Type") == "Video"), {})
        return (v.get("Width") or 0) >= 3800 and (v.get("VideoRange") or "SDR") != "SDR"
    return ids(i for i in movies(items) if ok(i))


def by_country(items, countries):
    return ids(i for i in items if set(i.get("ProductionLocations") or []) & countries)


def grown_up_animation(items):
    return ids(i for i in items if "Animation" in (i.get("Genres") or []) and not {"Family", "Kids", "Children"} & set(i.get("Genres") or [])
               and (i.get("OfficialRating") or "") not in KIDS_RATINGS)


def decade(items, start):
    return ids(i for i in movies(items) if start <= (i.get("ProductionYear") or 0) < start + 10)


def mind(items):
    return ids(i for i in items if tags(i) & MIND)


def awards(items, tmdb_ids):
    return ids(i for i in items if (i.get("ProviderIds") or {}).get("Tmdb") in tmdb_ids or "award winner" in tags(i))


def seasonal(items, month):
    """(name, ids) in December / October, None otherwise."""
    if month == 12:
        return "Christmas", ids(i for i in items if tags(i) & CHRISTMAS)
    if month == 10:
        return "Halloween", ids(i for i in items if tags(i) & HALLOWEEN or ("Horror" in (i.get("Genres") or []) and i["Type"] == "Movie"))
    return None


def seed(day, key, owner=None):
    """Same order all day, on every device; per-user rows per user."""
    return f"{day.isoformat()}:{key}:{owner or ''}"


def shuffled(member_ids, seed_text):
    """A stable shuffle: each id ranked by a hash of the seed and the id."""
    return sorted(member_ids, key=lambda i: hashlib.sha256(f"{seed_text}:{i}".encode()).hexdigest())


def ordered(key, member_ids, day, owner=None):
    return member_ids if key in NATURAL else shuffled(member_ids, seed(day, key, owner))


def played(i):
    return bool((i.get("UserData") or {}).get("Played"))


def hidden_gems(items):
    return ids(i for i in items if (i.get("CommunityRating") or 0) >= 7.5 and not played(i))


def watch_again(items):
    """Played and liked (rating 7+ or ♥), last played first."""
    again = [i for i in items if played(i) and ((i.get("CommunityRating") or 0) >= 7 or (i.get("UserData") or {}).get("IsFavorite"))]
    return ids(sorted(again, key=lambda i: (i.get("UserData") or {}).get("LastPlayedDate") or "", reverse=True))


def top_picks(items, similar, seeds_max=15):
    """Unwatched titles similar to what the user liked or watched, the most often suggested first; liked seeds count double."""
    by_id = {i["Id"]: i for i in items}
    seeds = [i for i in items if played(i) or (i.get("UserData") or {}).get("IsFavorite")]
    seeds.sort(key=lambda i: (not (i.get("UserData") or {}).get("IsFavorite"), (i.get("UserData") or {}).get("LastPlayedDate") or ""), reverse=False)
    score = collections.Counter()
    first = {}
    for n, s in enumerate(seeds[:seeds_max]):
        weight = 2 if (s.get("UserData") or {}).get("IsFavorite") else 1
        for sid in similar(s["Id"]):
            if sid in by_id and not played(by_id[sid]) and sid not in {x["Id"] for x in seeds}:
                score[sid] += weight
                first.setdefault(sid, (n, len(first)))
    return sorted(score, key=lambda k: (-score[k], first[k]))


def rotating(day):
    n = (day.year * 12 + day.month) * 3
    return [ROTATING[(n + k) % len(ROTATING)] for k in range(3)]


def pick_person(people, minimum, day):
    """(name, ids) for this month's person among those with at least minimum titles, None without one."""
    names = sorted(p for p, t in people.items() if len(t) >= minimum)
    if not names:
        return None
    name = names[(day.year * 12 + day.month) % len(names)]
    return name, people[name]


def people_of(items, kind):
    out = collections.defaultdict(list)
    for i in items:
        for p in i.get("People") or []:
            if p.get("Type") == kind and i["Id"] not in out[p["Name"]]:
                out[p["Name"]].append(i["Id"])
    return out


def builtin(kind):
    return {"type": kind, "enabled": True}


def collection(sid, name, server):
    return {"kind": "pluginDynamic", "pluginSource": "collections", "type": "none", "enabled": True,
            "serverId": server, "pluginSection": "collection", "pluginAdditionalData": sid, "pluginDisplayText": name}


def order_keys(rotation, seasonal_on):
    """Rows 4-20 in home order: the seasonal row only in season (Binge follows it), then this month's rotating rows."""
    return ["toppicks", "gems", "watchagain", "awards", "sagas", "truestory"] + (["seasonal"] if seasonal_on else []) + ["binge"] + list(rotation)


def web_rows(rotation, seasonal_on, names):
    """[{key, name, kind}] for the classic web (beta-rows.js), same order as Moonfin: Favourites and New on the Server (last 30 days) are queried directly, the rest are the row collections."""
    return [{"key": "favourites", "name": "Favourites", "kind": "favourites"}, {"key": "new", "name": "New on the Server", "kind": "new"}] + \
        [{"key": k, "name": names[k], "kind": "collection"} for k in order_keys(rotation, seasonal_on) if k in names]


def art_key(member_ids):
    return hashlib.sha256(",".join(member_ids[:3]).encode()).hexdigest()


def art_due(state, sid, member_ids):
    return state.get(sid) != art_key(member_ids)


def layout(sets, names, rotation, seasonal_on, server):
    """Moonfin homeSections: Continue Watching, Favourites (built in), New on the Server, then rows 4-20 (order_keys); rows without a set left out. Next Up is not in the plan."""
    order = order_keys(rotation, seasonal_on)
    # Row 3: our "New on the Server" (last 30 days, the spec's title); Moonfin's Recently Added has neither the limit nor the title.
    new = collection(sets["new"], names["new"], server) if "new" in sets else builtin("latestmedia")
    out = [builtin("resume"), builtin("favoritemovies"), builtin("favoriteseries"), new]
    out += [collection(sets[k], names[k], server) for k in order if k in sets]
    for n, s in enumerate(out):
        s["order"] = n
    return out


def awards_tmdb(fetch=True):
    """TMDB ids of award winners and nominees from Wikidata, cached; the cache is used when Wikidata does not answer."""
    if fetch:
        values = " ".join(f"wd:{q}" for q in AWARDS)
        query = f"SELECT DISTINCT ?t WHERE {{ VALUES ?a {{{values}}} {{ ?f wdt:P166 ?a }} UNION {{ ?f wdt:P1411 ?a }} ?f wdt:P4947 ?t }}"
        req = urllib.request.Request("https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": query}),
                                     headers={"Accept": "application/sparql-results+json", "User-Agent": "homelab-jellyfin-beta/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                found = sorted({b["t"]["value"] for b in json.load(r)["results"]["bindings"]})
            AWARDS_CACHE.parent.mkdir(parents=True, exist_ok=True)
            AWARDS_CACHE.write_text(json.dumps(found))
            return set(found)
        except (OSError, ValueError, KeyError) as e:
            print(f"awards: Wikidata failed ({type(e).__name__}), using the cache")
    try:
        return set(json.loads(AWARDS_CACHE.read_text()))
    except (OSError, ValueError):
        return set()


FIELDS = "Fields=Tags,Genres,ProductionLocations,People,ProviderIds,Path,DateCreated,MediaStreams,ChildCount,OfficialRating&EnableImages=false"


def library(user):
    items = api("GET", f"/Items?userId={user}&Recursive=true&IncludeItemTypes=Movie,Series&CollapseBoxSetItems=false&{FIELDS}")["Items"]
    return [i for i in items if not (i.get("Path") or "").startswith(TRENDING_PATH)]


def similar_for(user):
    cache = {}

    def get(iid):
        if iid not in cache:
            cache[iid] = ids(api("GET", f"/Items/{iid}/Similar?userId={user}&Limit=12&EnableImages=false")["Items"])
        return cache[iid]
    return get


def franchise_sets(boxes):
    return [b["Id"] for b in boxes if not b.get("Tags")]


def _steps():
    spec = importlib.util.spec_from_file_location("hidden_gems", HERE.parent.parent / "Jellyfin" / "scripts" / "hidden-gems.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.steps


def sync_set(key, name, want, boxes, admin, dry, owner=None):
    """Creates or updates the collection of row key (owner: per-user), members in want's order; returns its id (None in a dry run when new or without titles)."""
    tag = f"beta-row-{key}"
    utag = f"beta-row-u-{owner}" if owner else None
    box = next((b for b in boxes if tag in (b.get("Tags") or []) and (utag in (b.get("Tags") or []) if utag else not any(t.startswith("beta-row-u-") for t in b.get("Tags") or []))), None)
    want = want[:SIZE]
    if not want or dry:
        return box["Id"] if box else None
    if box is None:
        # Jellyfin keeps one collection folder per name: a per-user set is created under a unique name and renamed below.
        box = api("POST", f"/Collections?name={urllib.parse.quote(name + (' ' + owner[:8] if owner else ''))}&ids={','.join(want)}&isLocked=true")
        box["Tags"] = []
    sid = box["Id"]
    viewer = owner or admin  # BlockedTags hide a per-user set from everyone but its owner
    item = api("GET", f"/Items/{sid}?userId={viewer}")
    tags_want = ["beta-row", tag] + ([utag] if utag else [])
    if set(item.get("Tags") or []) != set(tags_want) or item.get("Name") != name or item.get("DisplayOrder") != "Default":
        api("POST", f"/Items/{sid}", {**item, "Name": name, "Tags": tags_want, "DisplayOrder": "Default", "LockData": True, "ForcedSortName": f"80 {name}"})
    have = ids(api("GET", f"/Items?ParentId={sid}&userId={viewer}&EnableImages=false&EnableUserData=false")["Items"])
    for method, chunk in _steps()(have, want):
        api(method, f"/Collections/{sid}/Items?ids={','.join(chunk)}")
    return sid


def blocked_for(user, all_users):
    """The per-user tags of everyone else, disabled users included (their sets stay); beta-admin: of everyone."""
    return {f"beta-row-u-{u['Id']}" for u in all_users if u["Id"] != user["Id"] or user["Name"] == BETA_ADMIN}


def hide_others(users, dry, all_users=None):
    """Every enabled user's BlockedTags hold the per-user tags of everyone else."""
    all_users = all_users or users
    changed = 0
    for u in users:
        pol = u["Policy"]
        want = blocked_for(u, all_users)
        have = set(pol.get("BlockedTags") or [])
        if not want <= have:
            changed += 1
            if not dry:
                api("POST", f"/Users/{u['Id']}/Policy", {**pol, "BlockedTags": sorted(have | want)})
    return changed


def write_layout(uid, sections, dry):
    try:
        settings = api("GET", f"/Moonfin/Settings/{uid}") or {}
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        settings = {}
    profiles = ["global"] + [p for p in ("desktop", "mobile", "tv") if isinstance(settings.get(p), dict) and settings[p].get("homeSections") is not None]
    if dry:
        return profiles
    body = {p: {"homeSections": sections, "homeRowOrder": None, "displayCollectionsRows": True, "collectionsRowSortBy": "playlistOrder", "seasonalRowEnabled": False} for p in profiles}
    api("POST", f"/Moonfin/Settings/{uid}", {"settings": {"syncEnabled": settings.get("syncEnabled", True), **body}, "clientId": "jellyfin-beta-rows", "mergeMode": "merge"})
    return profiles


def _module(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE.parent.parent / "Jellyfin" / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def poster(iid):
    try:
        return api_raw("GET", f"/Items/{iid}/Images/Primary?maxHeight=900", None, "image/jpeg")
    except urllib.error.HTTPError:
        return None


def set_art(sid, name, member_ids, state, dry):
    """Poster (first 3 member posters fanned, like prod's lists) and backdrop of a row's collection: no blank tiles in the web grids. Returns 1 when uploaded."""
    if not member_ids or not art_due(state, sid, member_ids):
        return 0
    if dry:
        return 1
    sys.path.insert(0, str(HERE.parent.parent / "Jellyfin" / "scripts"))
    import trending_art as art
    posters = [p for p in (poster(i) for i in member_ids[:3]) if p]
    if not posters:
        return 0
    api_raw("POST", f"/Items/{sid}/Images/Primary", base64.b64encode(art.collection_poster("beta", name, None, posters)), "image/jpeg")
    api_raw("POST", f"/Items/{sid}/Images/Backdrop/0", base64.b64encode(art.collection_backdrop("beta", name, None, None, posters)), "image/jpeg")
    state[sid] = art_key(member_ids)
    return 1


def load_art_state():
    try:
        return json.loads(ART_STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_art_state(state):
    ART_STATE.parent.mkdir(parents=True, exist_ok=True)
    ART_STATE.write_text(json.dumps(state))


def web_entry(rows_list, dry):
    """Injector entry "Beta rows": window.HssBetaRows + beta-rows.js."""
    if dry:
        return
    source = "window.HssBetaRows = " + json.dumps(rows_list) + ";\n" + (HERE / "beta-rows.js").read_text()
    _module("tracker_link", "tracker-link.py").main([], api=api, source=source, name=WEB_ENTRY, source_file=HERE / "beta-rows.js")


def plan(items, day, award_ids):
    """{key: (name, ids)} of every global row this month (rotating ones too, so the counts are complete)."""
    out = {"new": ("New on the Server", new_on_server(items, day)), "awards": (NAMES["awards"], awards(items, award_ids)), "truestory": (NAMES["truestory"], true_story(items)), "binge": (NAMES["binge"], binge(items)),
           "world": (NAMES["world"], by_country(items, WORLD)), "dach": (NAMES["dach"], by_country(items, DACH)), "mind": (NAMES["mind"], mind(items)),
           "animation": (NAMES["animation"], grown_up_animation(items)), "under100": (NAMES["under100"], under_100(items)), "uhd": (NAMES["uhd"], uhd_hdr(items))}
    start = 1980 + 10 * ((day.year * 12 + day.month) % 2)
    out["decade"] = (f"Decade Night: {start % 100}s", decade(items, start))
    for key, kind, minimum, label in (("director", "Director", 3, "Director Spotlight"), ("actor", "Actor", 4, "Starring")):
        person = pick_person(people_of(items, kind), minimum, day)
        if person:
            out[key] = (f"{label}: {person[0]}", person[1])
    season = seasonal(items, day.month)
    if season:
        out["seasonal"] = season
    return out


def main(argv):
    args = list(argv)
    dry, counts = "--dry-run" in args, "--counts" in args
    day = datetime.date.today()
    if "--date" in args:
        i = args.index("--date")
        try:
            day = datetime.datetime.strptime(args[i + 1], "%Y-%m-%d").date()
        except (IndexError, ValueError):
            print(__doc__.strip(), file=sys.stderr)
            return 2
        del args[i:i + 2]
    if "--month" in args:
        i = args.index("--month")
        try:
            day = datetime.datetime.strptime(args[i + 1], "%Y-%m").date()
        except (IndexError, ValueError):
            print(__doc__.strip(), file=sys.stderr)
            return 2
        del args[i:i + 2]
    if any(a not in ("--dry-run", "--counts") for a in args):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    everyone = api("GET", "/Users")
    users = [u for u in everyone if not u["Policy"].get("IsDisabled")]
    admin = next(u["Id"] for u in users if u["Policy"]["IsAdministrator"])
    items = library(admin)
    rows = plan(items, day, awards_tmdb())
    boxes = api("GET", "/Items?Recursive=true&IncludeItemTypes=BoxSet&Fields=Tags&EnableImages=false")["Items"]
    franchises = franchise_sets(boxes)
    rotation = rotating(day)
    if counts:
        print(f"{'row':<12} titles  name")
        for key, (name, want) in sorted(rows.items()):
            print(f"{key:<12} {len(want):>6}  {name}{'  (shown this month)' if key in rotation or key == 'seasonal' else ''}")
        print(f"{'sagas':<12} {len(franchises):>6}  {NAMES['sagas']} (franchise collections)")
        for u in users:
            if u["Name"] == BETA_ADMIN:
                continue
            mine = library(u["Id"])
            print(f"user {u['Name'][:2]}…: toppicks {len(top_picks(mine, similar_for(u['Id'])))}, gems {len(hidden_gems(mine))}, watchagain {len(watch_again(mine))}")
        return 0
    sets = {}
    art_state = load_art_state()
    arts = 0
    for key, (name, want) in rows.items():
        want = ordered(key, want, day)
        sid = sync_set(key, name, want, boxes, admin, dry)
        if sid:
            sets[key] = sid
            NAMES[key] = name
            arts += set_art(sid, name, want[:SIZE], art_state, dry)
    franchises = ordered("sagas", franchises, day)
    sid = sync_set("sagas", NAMES["sagas"], franchises, boxes, admin, dry)
    if sid:
        sets["sagas"] = sid
        arts += set_art(sid, NAMES["sagas"], franchises, art_state, dry)
    print(f"global rows: {', '.join(f'{k} {len(rows[k][1])}' for k in sorted(rows))}, sagas {len(franchises)}; this month: {', '.join(rotation)}{', seasonal' if 'seasonal' in rows else ''}")
    hidden = hide_others(users, dry, everyone)
    for u in users:
        if u["Name"] == BETA_ADMIN:
            continue
        mine = library(u["Id"])
        own = dict(sets)
        for key, want in (("toppicks", top_picks(mine, similar_for(u["Id"]))), ("gems", hidden_gems(mine)), ("watchagain", watch_again(mine))):
            want = ordered(key, want, day, u["Id"])
            sid = sync_set(key, NAMES[key], want, boxes, admin, dry, owner=u["Id"])
            if sid:
                own[key] = sid
                arts += set_art(sid, NAMES[key], want[:SIZE], art_state, dry)
        profiles = write_layout(u["Id"], layout(own, NAMES, rotation, "seasonal" in rows, SERVER), dry)
        print(f"user {u['Name'][:2]}…: {len(own)} rows, profiles {', '.join(profiles)}")
    web_entry(web_rows(rotation, "seasonal" in rows, NAMES), dry)
    if not dry:
        save_art_state(art_state)
    print(("would change" if dry else "done") + f"; BlockedTags updated for {hidden} user(s), artwork for {arts} collection(s), web rows entry {WEB_ENTRY!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
