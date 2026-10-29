#!/usr/bin/env python3
"""Give every NEW Jellyfin user the household playback defaults; never touch a user twice.

  user-defaults.py [--state FILE] [--dry-run]

Jellyfin has no default user configuration, so a user timer runs this every minute.
First run (no state file): every existing user is adopted, only the two "remember" flags are switched on.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# Original language = no language preference + the track flagged default (original-audio.py flags the original track).
# A user can change all of it under Jellyfin Settings.
DEFAULTS = {
    "AudioLanguagePreference": "",
    "PlayDefaultAudioTrack": True,
    "SubtitleLanguagePreference": "",
    "SubtitleMode": "Smart",
    "RememberAudioSelections": True,
    "RememberSubtitleSelections": True,
}
REMEMBER = ("RememberAudioSelections", "RememberSubtitleSelections")
_spec = importlib.util.spec_from_file_location("trending_setup", pathlib.Path(__file__).with_name("trending-setup.py"))
_setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_setup)
hide = _setup.hidden
STATE = pathlib.Path.home() / ".local/state/jellyfin-user-defaults.json"


def new_user_config(config):
    return {**config, **DEFAULTS}


def adopted_config(config):
    return {**config, **{key: True for key in REMEMBER}}


def plan(users, handled, lib_id=None):
    """users from GET /Users, handled = ids already done (None = first run), lib_id = the Trending library every user hides. Returns ([(id, config)], handled ids)."""
    updates = []
    for u in users:
        old = u["Configuration"]
        if handled is not None and u["Id"] in handled:
            new = old
        else:
            new = adopted_config(old) if handled is None else new_user_config(old)
        if lib_id:
            new = hide(new, lib_id)
        if new != old:
            updates.append((u["Id"], new))
    return updates, {u["Id"] for u in users}


def load_state(path):
    return set(json.loads(path.read_text())) if path.exists() else None


def save_state(path, handled):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(handled)))
    os.replace(tmp, path)


def main():
    from jellyfin_api import api

    ap = argparse.ArgumentParser()
    ap.add_argument("--state", type=pathlib.Path, default=STATE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    users = api("GET", "/Users")
    handled = load_state(args.state)
    lib_id = _setup.library_id(api("GET", "/Library/VirtualFolders"))
    updates, handled_now = plan(users, handled, lib_id)
    names = {u["Id"]: u["Name"] for u in users}
    for uid, config in updates:
        print(f"{'would set' if args.dry_run else 'set'} {'adopted' if handled is None else 'defaults'} for {names[uid]}")
        if not args.dry_run:
            api("POST", f"/Users/{uid}/Configuration", config)
    if not args.dry_run and handled_now != handled:
        save_state(args.state, handled_now)


if __name__ == "__main__":
    main()
