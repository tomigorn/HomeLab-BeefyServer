#!/usr/bin/env python3
"""Add, replace or remove a household member's Jellyfin → Tracker (Ryot) webhook.

  tracker-webhook.py add <jellyfin-user>     integration URL on stdin
  tracker-webhook.py remove <jellyfin-user>
  tracker-webhook.py list
"""
import sys
import urllib.parse
import uuid

HOST = "tracker.holy-grail.ch"
PLUGIN = "Webhooks"
EVENTS = ["Play", "Pause", "Resume", "Stop", "Progress", "MarkPlayed"]


def hook_name(user):
    return f"Tracker {user}"


def check_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname != HOST or not parts.path.startswith("/_i/") or len(parts.path) <= len("/_i/"):
        raise ValueError(f"not a {HOST}/_i/ integration URL")


def upsert_hook(config, user, user_id, url):
    check_url(url)
    name = hook_name(user)
    hooks = list(config.get("Hooks") or [])
    old = next((h for h in hooks if h.get("Name") == name), None)
    hook = {"Id": old["Id"] if old else uuid.uuid4().hex, "Name": name, "Url": url, "UserId": user_id, "Format": "Default", "Events": list(EVENTS)}
    hooks = [hook if h is old else h for h in hooks] if old else hooks + [hook]
    return {**config, "Hooks": hooks}


def remove_hook(config, user):
    name = hook_name(user)
    return {**config, "Hooks": [h for h in config.get("Hooks") or [] if h.get("Name") != name]}


def mask(url):
    return "…/_i/****" if "/_i/" in url else "****"


def main(argv, stdin=sys.stdin, out=sys.stdout, api=None, user_id=None):
    if api is None or user_id is None:
        import jellyfin_api
        api = api or jellyfin_api.api
        user_id = user_id or jellyfin_api.user_id
    if not argv or argv[0] not in ("add", "remove", "list") or (argv[0] != "list" and len(argv) != 2):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    command = argv[0]
    try:
        plugin_id = next(p["Id"] for p in api("GET", "/Plugins") if p["Name"] == PLUGIN)
    except StopIteration:
        print(f"plugin {PLUGIN} not installed", file=sys.stderr)
        return 1
    path = f"/Plugins/{plugin_id}/Configuration"
    config = api("GET", path)
    if command == "list":
        for h in config.get("Hooks") or []:
            print(f"{h.get('Name')}\tuser {h.get('UserId') or '(any)'}\t{mask(h.get('Url', ''))}", file=out)
        return 0
    user = argv[1]
    if command == "add":
        try:
            uid = user_id(user)
        except KeyError:
            print(f"unknown Jellyfin user: {user}", file=sys.stderr)
            return 1
        try:
            config = upsert_hook(config, user, uid, stdin.read().strip())
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        api("POST", path, config)
        print(f"added {hook_name(user)}", file=out)
        return 0
    api("POST", path, remove_hook(config, user))
    print(f"removed {hook_name(user)}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
