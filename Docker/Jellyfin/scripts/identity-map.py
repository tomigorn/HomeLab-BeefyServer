#!/usr/bin/env python3
"""Holy Grail sign-in subjects of the Jellyfin and Seerr users, for the request attribution of popular/app.py (find_person).

  identity-map.py [--out FILE] [--seerr-db FILE] [--check]

Writes {"jellyfin": {user id: subject}, "seerr": {user id: subject}} only when it changed (user timer, every minute): ids and subjects only, no names or mail addresses.
--check: the file is in sync and every human Seerr user is reached through its Jellyfin link or its subject, never only by name.
"""
import argparse
import contextlib
import json
import os
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import jellyfin_api  # noqa: E402

ISSUER = "https://sso.holy-grail.ch/application/o/jellyfin"
PROVIDER = "holy-grail"
OIDC_PLUGIN = "4c5b9b9680cd4c3d9e3d23fa4ebf6ce4"
SERVICE_EMAIL = "jellyfin-popular"
OUT = pathlib.Path("/srv/appdata/jellyfin-popular/identity.json")
SEERR_DB = pathlib.Path("/srv/appdata/seerr/db/db.sqlite3")


def norm(uid):
    return str(uid or "").replace("-", "").lower()


def unique(pairs):
    out, bad = {}, set()
    for k, v in pairs:
        if not k or not v or k in bad:
            continue
        if out.get(k, v) != v:
            bad.add(k)
            out.pop(k)
            continue
        out[k] = v
    return out


def build(oidc_config, con):
    links = [l for l in oidc_config.get("IdentityLinks") or [] if isinstance(l, dict) and str(l.get("Issuer") or "").rstrip("/") == ISSUER]
    jf = unique((norm(l.get("UserId")), l.get("Subject")) for l in links)
    shared = {v for v in jf.values() if list(jf.values()).count(v) > 1}
    jf = {k: v for k, v in jf.items() if v not in shared}
    seerr = unique((str(uid), sub) for uid, sub in con.execute("SELECT userId, sub FROM linked_accounts WHERE provider = ? AND userId IS NOT NULL", (PROVIDER,)))
    return {"jellyfin": jf, "seerr": seerr}


def problems(mapping, con, jellyfin_ids):
    """Human Seerr users that no Jellyfin user reaches through the Jellyfin link or the subject."""
    subs = set(mapping["jellyfin"].values())
    rows = con.execute("SELECT id, jellyfinUserId FROM user WHERE lower(coalesce(email, '')) != ? ORDER BY id", (SERVICE_EMAIL,))
    return [uid for uid, jf in rows if norm(jf) not in jellyfin_ids and mapping["seerr"].get(str(uid)) not in subs]


def write_if_changed(path, mapping):
    try:
        if json.loads(path.read_text()) == mapping:
            return False
    except (OSError, ValueError):
        pass
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(mapping, sort_keys=True))
    tmp.chmod(0o644)
    os.replace(tmp, path)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    ap.add_argument("--seerr-db", type=pathlib.Path, default=SEERR_DB)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    try:
        with contextlib.closing(sqlite3.connect(f"file:{a.seerr_db}?mode=ro", uri=True)) as con:
            return run(a, con)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as e:
        print(f"failed {type(e).__name__}")
        return 1


def run(a, con):
    mapping = build(jellyfin_api.api("GET", f"/Plugins/{OIDC_PLUGIN}/Configuration"), con)
    if not a.check:
        if write_if_changed(a.out, mapping):
            print(f"written {len(mapping['jellyfin'])} jellyfin, {len(mapping['seerr'])} seerr")
        return 0
    try:
        synced = json.loads(a.out.read_text()) == mapping
    except (OSError, ValueError):
        synced = False
    bad = problems(mapping, con, {norm(u.get("Id")) for u in jellyfin_api.api("GET", "/Users")})
    humans = con.execute("SELECT count(*) FROM user WHERE lower(coalesce(email, '')) != ?", (SERVICE_EMAIL,)).fetchone()[0]
    if synced and not bad:
        print(f"ok {humans} users")
        return 0
    print(("in sync" if synced else "out of sync") + (f", only by name: Seerr users {' '.join(map(str, bad))}" if bad else ""))
    return 1


if __name__ == "__main__":
    sys.exit(main())
