"""Storage retention rules (README "Storage retention"), shared by the Seerr backend and Movie-Downloads' scripts/retention.py: keep-forever default by category, stage dates, postponement, space rule, keep overrides, grace after switch-on. Pure functions, no I/O."""
import datetime
import re

OLD_YEARS = 12
NICHE_MIN_AGE = 2
NICHE_VOTES = {"movie": 100, "tv": 1000}
GOATED_RATING = 8.0
GOATED_VOTES = 25000
SSD_DAYS = 30
DELETE_DAYS = 90
SOON_DAYS = 7
POSTPONE_MAX_DAYS = 30
GRACE_DAYS = 14
TEST_FILM = "movie/653"
TEST_TAG = "keep-test"
SWISS_TAG = "audio-swiss-german"
NICHE_PROFILES = ("older versions allowed", "swiss german")
KEY = re.compile(r"^(movie|tv)/([1-9][0-9]{0,9})$")


def reasons(kind, year, this_year, niche_votes=None, rating=None, votes=None, tags=(), profile=""):
    """["old", "niche", "goated"] that apply; empty = temporary by default. niche_votes: the votes the old-or-niche rule reads (Radarr TMDB votes, Sonarr votes); rating/votes: IMDb."""
    out = []
    year = year if isinstance(year, int) and year > 0 else None
    if year and year <= this_year - OLD_YEARS:
        out.append("old")
    few = year is not None and year <= this_year - NICHE_MIN_AGE and isinstance(niche_votes, (int, float)) and niche_votes < NICHE_VOTES[kind]
    picked = not out and any(p in (profile or "").lower() for p in NICHE_PROFILES)
    if few or SWISS_TAG in (tags or ()) or picked:
        out.append("niche")
    if isinstance(rating, (int, float)) and isinstance(votes, (int, float)) and rating >= GOATED_RATING and votes >= GOATED_VOTES:
        out.append("goated")
    return out


def _d(iso):
    return datetime.date.fromisoformat(iso) if iso else None


def schedule(imported):
    return {"imported": imported.isoformat(), "ssdUntil": (imported + datetime.timedelta(days=SSD_DAYS)).isoformat(),
            "hddFrom": (imported + datetime.timedelta(days=SSD_DAYS)).isoformat(), "due": (imported + datetime.timedelta(days=DELETE_DAYS)).isoformat()}


def effective(entry, override, today, enabled_on=None):
    """State of one title: entry from retention.json, override True/False/None from the keep store, enabled_on = the day deletion was switched on (None = observe)."""
    out = {"state": "keep", "keep": True, "source": "default", "deleteOn": None, "soon": False, "overdue": False, "why": None, "postponed": None}
    if entry.get("protected"):
        out["source"] = "protected"
        return out
    keep = bool(entry.get("default")) if override is None else bool(override)
    out["source"] = "default" if override is None else "override"
    if keep:
        return out
    out.update(state="temporary", keep=False)
    when, why = _d(entry.get("due")), "due"
    if when is None:
        return out
    space = _d(entry.get("space"))
    if space and space < when:
        when, why = space, "space"
    if entry.get("postponed"):
        when, why = when + datetime.timedelta(days=POSTPONE_MAX_DAYS), "postponed"
        out["postponed"] = entry["postponed"]
    if enabled_on and when < enabled_on + datetime.timedelta(days=GRACE_DAYS):
        when, why = enabled_on + datetime.timedelta(days=GRACE_DAYS), "grace"
    out.update(deleteOn=when.isoformat(), why=why, soon=(when - today).days <= SOON_DAYS, overdue=when < today)
    return out


def key(kind, tmdb):
    return f"{kind}/{int(tmdb)}"


def parse_key(k):
    m = KEY.match(k) if isinstance(k, str) else None
    return (m.group(1), int(m.group(2))) if m else None


def protected(k, tags):
    return k == TEST_FILM or TEST_TAG in (tags or ())
