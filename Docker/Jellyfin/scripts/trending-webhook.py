#!/usr/bin/env python3
"""Add, remove or check the Jellyfin → popular backend webhook for TV requests (Webhooks plugin, every user).

  trending-webhook.py add      add or replace the "Trending requests" hook (secret from secrets/popular/hook.secret)
  trending-webhook.py remove   remove it, other hooks stay
  trending-webhook.py check    ok / exit 1
  trending-webhook.py list     every hook, URLs masked
"""
import pathlib
import re
import secrets
import sys
import urllib.parse
import uuid

HOST = "jellyfin-popular"
PORT = 8098
PREFIX = "/hook/"
PLUGIN = "Webhooks"
NAME = "Trending requests"
EVENTS = ["Play", "Progress", "Pause", "Resume", "Stop"]
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
SECRET_FILE = pathlib.Path(__file__).resolve().parent.parent / "secrets" / "popular" / "hook.secret"


def webhook_url(secret):
    if not TOKEN_RE.fullmatch(secret or ""):
        raise ValueError("not a hook secret")
    return f"http://{HOST}:{PORT}{PREFIX}{secret}"


def check_url(url):
    parts = urllib.parse.urlsplit(url or "")
    token = parts.path[len(PREFIX):]
    if parts.scheme != "http" or parts.hostname != HOST or parts.port != PORT or not parts.path.startswith(PREFIX) or not TOKEN_RE.fullmatch(token) or parts.query or parts.fragment:
        raise ValueError(f"not the popular hook URL on {HOST}:{PORT}")


def upsert_hook(config, url):
    check_url(url)
    hooks = list(config.get("Hooks") or [])
    old = next((h for h in hooks if h.get("Name") == NAME), None)
    hook = {"Id": old["Id"] if old else uuid.uuid4().hex, "Name": NAME, "Url": url, "Format": "Default", "Events": list(EVENTS)}
    hooks = [hook if h is old else h for h in hooks] if old else hooks + [hook]
    return {**config, "Hooks": hooks}


def remove_hook(config):
    return {**config, "Hooks": [h for h in config.get("Hooks") or [] if h.get("Name") != NAME]}


def mask(url):
    for marker in (PREFIX, "/webhook/jellyfin/", "/_i/"):
        if marker in (url or ""):
            return f"…{marker}****"
    return "****"


def problem(config, url):
    hook = next((h for h in config.get("Hooks") or [] if h.get("Name") == NAME), None)
    if hook is None:
        return "missing"
    if not secrets.compare_digest(str(hook.get("Url") or "").encode(), url.encode()):
        return "wrong URL"
    if hook.get("UserId"):
        return "bound to one user"
    if hook.get("Format") != "Default" or sorted(hook.get("Events") or []) != sorted(EVENTS):
        return "wrong format or events"
    return None


def main(argv, out=sys.stdout, api=None, secret_file=SECRET_FILE):
    if api is None:
        import jellyfin_api
        api = jellyfin_api.api
    command = argv[0] if len(argv) == 1 else None
    if command not in ("add", "remove", "check", "list"):
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
    if command == "remove":
        api("POST", path, remove_hook(config))
        print(f"removed {NAME}", file=out)
        return 0
    try:
        url = webhook_url(pathlib.Path(secret_file).read_text().strip())
    except (OSError, ValueError):
        print(f"no hook secret in {secret_file}", file=sys.stderr)
        return 1
    if command == "check":
        why = problem(config, url)
        print(why or "ok", file=out if why is None else sys.stderr)
        return 1 if why else 0
    api("POST", path, upsert_hook(config, url))
    print(f"added {NAME} -> {mask(url)}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
