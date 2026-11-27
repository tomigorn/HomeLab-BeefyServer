"""Storage retention, Keep forever and Delete early rules (README "Storage retention", "My library"), the one rule set of the Seerr backend, Movie-Downloads' scripts/retention.py and scripts/delete-early.py: automatic keep forever by category, fan-favourite marks, stage dates, postponement, space rule, grace after switch-on, who must OK an early delete and the limits. Pure functions, no I/O."""
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
MARK_WEEK_MAX = 5
DELETE_WEEK_MAX = 10
WEEK = datetime.timedelta(days=7)
ASK_DAYS = 7
BIN_DAYS = 7
WATCH_DAYS = 30
OWNER = 1
GROUP = "streaming-requesters"
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
    """State of one title: entry from retention.json, override True = someone's fan-favourite mark (False / None: none; automatic keep forever can't be switched off), enabled_on = the day deletion was switched on (None = observe)."""
    out = {"state": "keep", "keep": True, "source": "default", "deleteOn": None, "soon": False, "overdue": False, "why": None, "postponed": None}
    if entry.get("protected"):
        out["source"] = "protected"
        return out
    keep = bool(entry.get("default")) or override is True
    out["source"] = "fan" if keep and not entry.get("default") else "default"
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


def _uid(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 and not isinstance(v, bool) else None


def _when(s):
    try:
        d = datetime.datetime.fromisoformat(str(s))
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def marks(rec):
    """{uid: {at, name}} of the fan-favourite marks in a keep.json record; an old record with keep true is a mark by whoever ticked it (by; none recorded: 0, removable by nobody)."""
    if not isinstance(rec, dict):
        return {}
    if isinstance(rec.get("marks"), dict):
        return {_uid(k): {"at": v.get("at"), "name": v.get("name")} for k, v in rec["marks"].items() if _uid(k) and isinstance(v, dict)}
    if rec.get("keep") is True:
        return {_uid(rec.get("by")) or 0: {"at": rec.get("at"), "name": None}}
    return {}


def fan_override(rec):
    return True if marks(rec) else None


def marks_used(log, uid, now):
    """Marks the person set in the last 7 days; removing one does not free its slot."""
    return sum(1 for m in log or [] if isinstance(m, dict) and m.get("by") == uid and (_when(m.get("at")) or now - 2 * WEEK) > now - WEEK)


def may_add_mark(log, uid, now):
    return marks_used(log, uid, now) < MARK_WEEK_MAX


def may_remove_mark(rec, uid):
    return uid in marks(rec)


def automatic(entry):
    return isinstance(entry, dict) and bool(entry.get("default")) and not entry.get("protected")


def delete_plan(key, tags, entry, setters, watchers, deleter):
    """An early delete: refused (protected, kept = automatic keep forever, library = not on the server) or the OKs it needs: each setter of a fan-favourite mark, each other viewer mid-watch (no Seerr account: the owner); the deleter never asks themself."""
    if protected(key, tags) or (entry or {}).get("protected"):
        return {"refused": "protected", "asks": []}
    if not isinstance(entry, dict):
        return {"refused": "library", "asks": []}
    if automatic(entry):
        return {"refused": "kept", "asks": []}
    asks = []
    fans = []
    for u, n in (setters or {}).items():
        sid = u or OWNER
        if sid != deleter and sid not in [f for f, _ in fans]:
            fans.append((sid, n))
    if fans:
        asks.append({"reason": "fan", "approvers": [u for u, _ in fans], "names": [n for _, n in fans]})
    seen = set()
    for uid, name in watchers or []:
        sid = uid if uid else OWNER
        if sid == deleter or sid in seen:
            continue
        seen.add(sid)
        asks.append({"reason": "watching", "approvers": [sid], "names": [name]})
    return {"refused": None, "asks": asks}
