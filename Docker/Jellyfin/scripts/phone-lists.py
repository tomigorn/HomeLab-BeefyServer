#!/usr/bin/env python3
"""Phone lists: two private home rows per user in the phone and tablet apps (Moonfin "mobile" profile), on top of the home (spec fastpi reports/specs/2026-11-19-phone-versions-design.md "Phone lists").
"📱 Your phone version requests": the titles this user asked a phone version of (Movie-Downloads scripts/phone-fetch.py, <box>/state.json): being prepared (an entry set with a ⏳ band poster), ready (the title itself), nothing smaller found (entry set, 2 days); a ready title leaves once this user downloaded it or 14 days after it got ready.
"📲 On your phone": what the download hook sent to this user's phone in the last 14 days (<box>/sent.jsonl, popular/app.py).
Each row is a BoxSet tagged hss-phone + hss-phone-u-<userId>, kept once made (Moonfin hides an empty collection row); every other user's policy blocks that owner tag (like media-bar.py), entry sets too.
Moonfin phones and tablets read the "mobile" profile; its own homeSections replace the global ones, so the rows go there only (TV, web and desktop never get them).
Runs after each phone-fetch export (systemd path unit) and hourly (expiry); unchanged inputs skip the run for up to SKIP_MINUTES.
Idempotent. Usage: phone-lists.py [--dry-run | --check]  (--check exits 1 when a set, a block or a home row differs)"""
import base64
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from jellyfin_api import api as jf_api, api_raw as jf_raw, file_lock  # noqa: E402

BOX = pathlib.Path(os.environ.get("PHONE_BOX", "/srv/appdata/phone-versions"))
STATE = pathlib.Path.home() / ".local" / "state" / "jellyfin-phone-lists.json"
# Shared with moonbase.py and media-bar.py: all three write Moonfin user settings and user policies.
LOCK = pathlib.Path.home() / ".local" / "state" / "jellyfin-moonbase.lock"
SKIP_MINUTES = 50

SERVER_URL = "https://jellyfin.holy-grail.ch"
TAG = "hss-phone"
OWNER = "hss-phone-u-"
KIND = "hss-phone-kind-"
ITEM = "hss-phone-item-"
HINT = "Tap the title, then Download → Original: your phone gets the smaller phone version once it is ready."
PREPARING = ("searching", "downloading", "checking", "placing")
GAVE_UP = ("nothing", "failed", "not-needed")
READY_DAYS, SENT_DAYS, GAVE_UP_DAYS = 14, 14, 2
NAMES = {"ready": {"en": "📱 Your phone version requests", "de": "📱 Deine angefragten Handy-Versionen"}, "sent": {"en": "📲 On your phone", "de": "📲 Auf deinem Handy"}}
# earlier row names, still recognised so their rows are renamed in place
OLD_NAMES = ("📱 Ready for your phone", "📱 Bereit fürs Handy")
# Bands and entry texts stay English for everyone (owner 2026-11-20); only the row names follow the display language.
BANDS = {"searching": "Searching…", "downloading": "Downloading {p} %", "checking": "Checking", "placing": "Almost ready", "none": "No smaller version"}


def when(text):
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def ready_row(items, sent, uid, now):
    """This user's entries, newest request first: {"item", "kind" (ready / preparing / none), "state", "percent", "title", "at"}."""
    out = []
    for jf, x in items.items():
        if not isinstance(x, dict):
            continue
        asked = [when(r.get("at")) for r in x.get("requestedBy") or [] if isinstance(r, dict) and r.get("user") == uid]
        asked = max([a for a in asked if a], default=None)
        state, updated = x.get("state"), when(x.get("updated"))
        if asked is None or (state not in PREPARING and updated is None):
            continue
        if state in PREPARING:
            kind = "preparing"
        elif state == "ready":
            if now - updated > datetime.timedelta(days=READY_DAYS) or any(s.get("user") == uid and s.get("item") == jf and (when(s.get("at")) or asked) > asked for s in sent):
                continue
            kind = "ready"
        elif state in GAVE_UP:
            if now - updated > datetime.timedelta(days=GAVE_UP_DAYS):
                continue
            kind = "none"
        else:
            continue
        out.append({"item": jf, "kind": kind, "state": state, "percent": int(x.get("percent") or 0), "title": x.get("title") or "", "at": asked})
    return sorted(out, key=lambda e: e["at"], reverse=True)


def sent_row(sent, uid, now):
    """Jellyfin item ids sent to this user's phone within SENT_DAYS, newest first, each once."""
    out = {}
    for s in sent:
        at = when(s.get("at"))
        if s.get("user") == uid and s.get("item") and at and now - at <= datetime.timedelta(days=SENT_DAYS):
            out[s["item"]] = max(at, out.get(s["item"], at))
    return sorted(out, key=out.get, reverse=True)


def band_text(entry):
    return BANDS["none"] if entry["kind"] == "none" else BANDS[entry["state"]].format(p=entry["percent"])


def language(prefs):
    """"de" when the user's Jellyfin display language is German, else "en" (no other per-user UI language is stored)."""
    lang = str(((prefs or {}).get("CustomPrefs") or {}).get("language") or "")
    return "de" if lang.lower().startswith("de") else "en"


def blocked_tags(have, uid, owners):
    """The policy's blocked tags with every other owner's tag; the existing order stays (media-bar.py edits the same list)."""
    want = [OWNER + o for o in sorted(owners) if o != uid]
    kept = [t for t in have or [] if not t.startswith(OWNER) or t in want]
    return kept + [t for t in want if t not in kept]


def is_phone_row(section):
    return section.get("pluginSource") == "collections" and section.get("pluginDisplayText") in {n for names in NAMES.values() for n in names.values()} | set(OLD_NAMES)


def row(sid, name):
    return {"kind": "pluginDynamic", "pluginSource": "collections", "type": "none", "enabled": True, "order": 0,
            "serverId": SERVER_URL, "pluginSection": "collection", "pluginAdditionalData": sid, "pluginDisplayText": name}


def home_rows(sections, wanted):
    """The home sections with our rows (wanted: [(set id, name)]) on top, every other row in its order after them."""
    rest = sorted((s for s in sections or [] if not is_phone_row(s)), key=lambda s: s.get("order") or 0)
    out = [row(sid, name) for sid, name in wanted] + [dict(s) for s in rest]
    for order, s in enumerate(out):
        s["order"] = order
    return out


def _art():
    spec = importlib.util.spec_from_file_location("trending_art", HERE / "trending_art.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _steps():
    spec = importlib.util.spec_from_file_location("hidden_gems", HERE / "hidden-gems.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.steps


def read_inputs(box):
    """(items, sent, raw bytes for the fingerprint); a missing state file is no request yet, an unreadable one raises ValueError."""
    raw = b""
    try:
        data = (box / "state.json").read_bytes()
        raw += data
        items = json.loads(data).get("items")
        if not isinstance(items, dict):
            raise ValueError("no items")
    except FileNotFoundError:
        items = {}
    sent = []
    try:
        data = (box / "sent.jsonl").read_bytes()
        raw += data
        for line in data.decode(errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                sent.append(rec)
    except FileNotFoundError:
        pass
    return items, sent, raw


def load_state(path):
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def owner_of(tags):
    return next((t[len(OWNER):] for t in tags if t.startswith(OWNER)), None)


def tagged(tags, prefix):
    return next((t[len(prefix):] for t in tags if t.startswith(prefix)), None)


class Sync:
    def __init__(self, api, raw, write, log):
        self.api, self.raw, self.write, self.log = api, raw, write, log
        self.problems, self.changes = [], []

    def user_settings(self, uid):
        try:
            return self.api("GET", f"/Moonfin/Settings/{uid}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def existing(self):
        """{(owner, kind, item or None): set id}; duplicates and sets without an owner or kind are returned to delete."""
        sets, extra = {}, []
        for box in self.api("GET", f"/Items?Recursive=true&IncludeItemTypes=BoxSet&Tags={TAG}&Fields=Tags&EnableImages=false&EnableUserData=false")["Items"]:
            tags = box.get("Tags") or []
            key = (owner_of(tags), tagged(tags, KIND), tagged(tags, ITEM) if tagged(tags, KIND) == "entry" else None)
            if None in key[:2] or key in sets:
                extra.append(box["Id"])
            else:
                sets[key] = box["Id"]
        return sets, extra

    def found(self, uid, ids):
        """The ids this user can see (gone or blocked titles drop out)."""
        if not ids:
            return set()
        got = self.api("GET", f"/Items?ids={','.join(ids)}&userId={uid}&EnableImages=false&EnableUserData=false")["Items"]
        return {i["Id"] for i in got}

    def ensure(self, sid, name, tags, overview, members, ordered, uid):
        """Create (sid None) or update one set; returns its id (None in a dry run that would create it)."""
        if sid is None:
            self.problems.append(f"set {name}: missing")
            if not self.write:
                return None
            sid = self.api("POST", f"/Collections?name={urllib.parse.quote(name)}&ids={','.join(members)}&isLocked=true")["Id"]
        item = self.api("GET", f"/Items/{sid}?userId={uid}")
        want = {"Name": name, "Tags": sorted(tags), "ForcedSortName": f"00 {name}", "LockData": True, "DisplayOrder": "Default", "Overview": overview}
        have = {**{k: item.get(k) for k in want}, "Tags": sorted(item.get("Tags") or [])}
        if have != want:
            self.problems.append(f"set {name}: metadata")
            if self.write:
                self.api("POST", f"/Items/{sid}", {**item, **want})
        cur = [i["Id"] for i in self.api("GET", f"/Items?ParentId={sid}&userId={uid}&EnableImages=false&EnableUserData=false")["Items"]]
        todo = ordered(cur, members) if members else ([("DELETE", cur)] if cur else [])
        if todo:
            self.problems.append(f"set {name}: members")
            if self.write:
                for method, ids in todo:
                    self.api(method, f"/Collections/{sid}/Items?ids={','.join(ids)}")
        return sid

    def image(self, iid, kind, width):
        try:
            return self.raw("GET", f"/Items/{iid}/Images/{kind}?maxWidth={width}", None, "image/jpeg")
        except (urllib.error.URLError, OSError):
            return None

    def entry_art(self, sid, entry, text, admin, art):
        got = self.api("GET", f"/Items?ids={entry['item']}&userId={admin}&Fields=SeriesId&EnableUserData=false")["Items"]
        item = got[0] if got else {}
        episode = item.get("Type") == "Episode"
        base = self.image(item.get("SeriesId") if episode and item.get("SeriesId") else entry["item"], "Primary", 600)
        back = self.image(entry["item"], "Primary" if episode else "Backdrop/0", 1280)
        icon = "none" if entry["kind"] == "none" else "hourglass"
        self.raw("POST", f"/Items/{sid}/Images/Primary", base64.b64encode(art.status_poster(base, text, icon, title=entry["title"])), "image/jpeg")
        self.raw("POST", f"/Items/{sid}/Images/Thumb", base64.b64encode(art.status_thumb(back, entry["title"], text, icon)), "image/jpeg")


def run(argv, api=jf_api, raw=jf_raw, box=BOX, state_path=STATE, now=None, log=print, art=None, steps=None):
    check, dry = "--check" in argv, "--dry-run" in argv
    write = not (check or dry)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        items, sent, raw_in = read_inputs(box)
    except (OSError, ValueError) as e:
        log(f"phone state unreadable ({type(e).__name__}), lists left as they are; next: ls -l {box}")
        return 1
    state = load_state(state_path)
    fingerprint = hashlib.sha256(raw_in + json.dumps([NAMES, BANDS, HINT], sort_keys=True).encode()).hexdigest()
    last = when(state.get("full"))
    if write and state.get("fingerprint") == fingerprint and last and now - last < datetime.timedelta(minutes=SKIP_MINUTES):
        log("unchanged inputs, skipped")
        return 0
    steps = steps or _steps()
    sync = Sync(api, raw, write, log)
    users = api("GET", "/Users")
    uids = [u["Id"] for u in users]
    admin = next(u["Id"] for u in users if (u.get("Policy") or {}).get("IsAdministrator"))
    sets, extra = sync.existing()
    labels = dict(state.get("labels") or {})
    plans = {}
    for uid in uids:
        ready = ready_row(items, sent, uid, now)
        sent_ids = sent_row(sent, uid, now)
        seen = sync.found(uid, sorted({e["item"] for e in ready} | set(sent_ids)))
        ready = [e for e in ready if e["item"] in seen]
        sent_ids = [i for i in sent_ids if i in seen]
        if ready or sent_ids or any(k[0] == uid for k in sets):
            lang = language(api("GET", f"/DisplayPreferences/usersettings?userId={uid}&client=emby"))
            plans[uid] = (ready, sent_ids, lang)
    owners = {k[0] for k in sets if k[0] in uids} | set(plans)
    for user in users:
        uid = user["Id"]
        policy = api("GET", f"/Users/{uid}")["Policy"]
        tags = blocked_tags(policy.get("BlockedTags"), uid, owners)
        if tags != (policy.get("BlockedTags") or []):
            sync.problems.append(f"user {uid}: blocks differ")
            if write:
                api("POST", f"/Users/{uid}/Policy", {**policy, "BlockedTags": tags})
    keep = set()
    rows = {}
    for uid, (ready, sent_ids, lang) in plans.items():
        members = []
        for e in ready:
            if e["kind"] == "ready":
                members.append(e["item"])
                continue
            key = (uid, "entry", e["item"])
            text = band_text(e)
            tags = [TAG, OWNER + uid, KIND + "entry", ITEM + e["item"]]
            sid = sync.ensure(sets.get(key), e["title"] or e["item"], tags, f"{text}. {HINT}", [e["item"]], steps, uid)
            if sid is None:
                continue
            keep.add(sid)
            members.append(sid)
            # the overview carries the same text, so --check (root, no state) compares that instead
            if labels.get(sid) != text and not check:
                sync.problems.append(f"entry {e['item']}: band {text}")
                if write:
                    sync.entry_art(sid, e, text, admin, art or _art())
                    labels[sid] = text
        wanted = []
        for kind, ids in (("ready", members), ("sent", sent_ids)):
            key = (uid, kind, None)
            if key not in sets and not ids:
                continue
            name = NAMES[kind][lang]
            sid = sync.ensure(sets.get(key), name, [TAG, OWNER + uid, KIND + kind], "", ids, steps, uid)
            if sid is not None:
                keep.add(sid)
                wanted.append((sid, name))
        rows[uid] = wanted
    for sid in extra + [sid for sid in sets.values() if sid not in keep]:
        sync.problems.append(f"set {sid}: not needed")
        if write:
            api("DELETE", f"/Items/{sid}")
            labels.pop(sid, None)
    for uid in uids:
        settings = sync.user_settings(uid)
        if not settings:
            continue
        mobile = settings.get("mobile") if isinstance(settings.get("mobile"), dict) else {}
        have = mobile.get("homeSections")
        wanted = rows.get(uid, [])
        if have is None and not wanted:
            continue
        base = have if have is not None else (settings.get("global") or {}).get("homeSections") or []
        new = home_rows(base, wanted)
        if new != have:
            sync.problems.append(f"user {uid}: home rows")
            if write:
                api("POST", f"/Moonfin/Settings/{uid}", {"settings": {"syncEnabled": settings.get("syncEnabled", True), "mobile": {"homeSections": new}},
                                                          "clientId": "hss-phone-lists", "mergeMode": "merge"})
    if check:
        log(f"ok {len(plans)} users" if not sync.problems else "drift: " + "; ".join(sync.problems))
        return 1 if sync.problems else 0
    if dry:
        log("would change: " + "; ".join(sync.problems) if sync.problems else "unchanged")
        return 0
    save_state(state_path, {"labels": {k: v for k, v in labels.items() if k in keep}, "fingerprint": fingerprint, "full": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    log("changed: " + "; ".join(sync.problems) if sync.problems else "unchanged")
    return 0


def main(argv):
    if any(a not in ("--dry-run", "--check") for a in argv) or len(argv) > 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if argv:
        return run(argv)
    with file_lock(LOCK, timeout=600, step=1):
        return run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
