#!/usr/bin/env python3
"""Add, replace or remove a household member's Jellyfin → Tracker (Floppy) webhook.

  tracker-webhook.py add <jellyfin-user> [<floppy-user>]   floppy-user defaults to jellyfin-user
  tracker-webhook.py remove <jellyfin-user>
  tracker-webhook.py list
"""
import re
import subprocess
import sys
import urllib.parse
import uuid

HOST = "192.168.1.102"
PORT = 8012
PREFIX = "/webhook/jellyfin/"
PLUGIN = "Webhooks"
EVENTS = ["Play", "Stop", "MarkPlayed", "MarkUnplayed"]
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
MARKER = "FLOPPY_TOKEN="
# Reads the user's webhook token and turns on Floppy's MarkPlayed/MarkUnplayed handling for that user.
FLOPPY_SNIPPET = (
    "import os; from users.models import User; "
    "u = User.objects.filter(username=os.environ['FLOPPY_USER'], is_active=True).first(); "
    "u and User.objects.filter(pk=u.pk).update(jellyfin_mark_played_enabled=True, jellyfin_mark_unplayed_enabled=True); "
    f"print('{MARKER}' + (u.token if u else ''))"
)


def hook_name(user):
    return f"Tracker {user}"


def webhook_url(token):
    if not TOKEN_RE.fullmatch(token or ""):
        raise ValueError("not a Floppy token")
    return f"http://{HOST}:{PORT}{PREFIX}{token}"


def check_url(url):
    parts = urllib.parse.urlsplit(url)
    token = parts.path[len(PREFIX):]
    if parts.scheme != "http" or parts.hostname != HOST or parts.port != PORT or not parts.path.startswith(PREFIX) or not TOKEN_RE.fullmatch(token) or parts.query:
        raise ValueError(f"not a Floppy webhook URL on {HOST}:{PORT}")


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
    for marker in (PREFIX, "/_i/"):
        if marker in url:
            return f"…{marker}****"
    return "****"


def parse_token(output):
    for line in output.splitlines():
        if line.startswith(MARKER) and line[len(MARKER):].strip():
            return line[len(MARKER):].strip()
    raise KeyError("no active Floppy user")


def floppy_token(user):
    result = subprocess.run(
        ["sudo", "docker", "exec", "-u", "abc", "-w", "/floppy", "-e", f"FLOPPY_USER={user}", "floppy", "python", "manage.py", "shell", "-c", FLOPPY_SNIPPET],
        capture_output=True, text=True, check=True, timeout=120,
    )
    return parse_token(result.stdout)


def main(argv, out=sys.stdout, api=None, user_id=None, floppy=None):
    if api is None or user_id is None:
        import jellyfin_api
        api = api or jellyfin_api.api
        user_id = user_id or jellyfin_api.user_id
    floppy = floppy or floppy_token
    command = argv[0] if argv else None
    if command not in ("add", "remove", "list") or (command == "list" and len(argv) != 1) or (command == "remove" and len(argv) != 2) or (command == "add" and len(argv) not in (2, 3)):
        print(__doc__.strip(), file=sys.stderr)
        return 2
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
        floppy_user = argv[2] if len(argv) == 3 else user
        try:
            uid = user_id(user)
        except KeyError:
            print(f"unknown Jellyfin user: {user}", file=sys.stderr)
            return 1
        try:
            url = webhook_url(floppy(floppy_user))
        except (KeyError, ValueError):
            print(f"no active Floppy user {floppy_user}: sign in to the Tracker once first", file=sys.stderr)
            return 1
        api("POST", path, upsert_hook(config, user, uid, url))
        print(f"added {hook_name(user)} -> Floppy {floppy_user}", file=out)
        return 0
    api("POST", path, remove_hook(config, user))
    print(f"removed {hook_name(user)}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
