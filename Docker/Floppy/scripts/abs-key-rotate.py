#!/usr/bin/env python3
"""Rotate the floppy-sync ABS API key before it expires; Telegram alert when that fails or the key expires soon.

  abs-key-rotate.py auto     root, weekly timer floppy-abs-key-rotate: rotate when the key never expires or expires within 45 days
  abs-key-rotate.py rotate   root: rotate now
  abs-key-rotate.py status   expiry of the current key (from the last run)

Rotation: a new key (365 days, same user, same name) with the old one, verified as floppy-sync, stored, then the old key deleted with the new one. A failed verify deletes the new key and keeps the old.
"""
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ABS_URL = "https://audiobookshelf.holy-grail.ch"
SERVICE_USER = "floppy-sync"
KEY_NAME = "floppy-sync"
KEY_DAYS = 365
ROTATE_BEFORE_DAYS = 45
ALERT_DAYS = 30
KEY_FILE = pathlib.Path("/home/buntu/Projects/Docker/Floppy/secrets/floppy-sync-abs-key")
STATE_DIR = pathlib.Path("/var/lib/floppy-abs-key")
OOB_ALERT = "/home/buntu/Projects/Docker/_global/scripts/oob-alert.sh"
PATHS = re.compile(r"^/api/(me|api-keys|api-keys/[A-Za-z0-9-]{1,64})$")


class AbsError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AbsAdmin:
    """ABS client for /api/me and /api/api-keys only, no redirects (the key is admin-capable)."""

    def __init__(self, key, base=ABS_URL):
        self.key = key
        self.base = base
        self.opener = urllib.request.build_opener(NoRedirect)

    def call(self, method, path, body=None):
        if method not in ("GET", "POST", "DELETE") or not PATHS.match(path):
            raise AbsError(f"refused: {method} {path.split('/')[2] if path.count('/') > 1 else path}")
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.key}", "User-Agent": "floppy-abs-key-rotate"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=20) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise AbsError(f"ABS {e.code} for {method} {path.split('/')[2]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise AbsError(f"ABS unreachable ({type(e).__name__})") from None
        if method == "DELETE":
            return {}
        return json.loads(raw) if raw.strip() else {}


def epoch(iso):
    if not iso:
        return None
    return int(datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def service_user(client):
    me = client.call("GET", "/api/me")
    if (me.get("username"), me.get("type"), me.get("isActive", True)) != (SERVICE_USER, "admin", True):
        raise AbsError(f"key is not the active ABS admin {SERVICE_USER}")
    return me


def current_record(client, me, state_dir):
    keys = [k for k in client.call("GET", "/api/api-keys").get("apiKeys", []) if k.get("userId") == me["id"]]
    remembered = read(state_dir / "key-id")
    if remembered:
        mine = [k for k in keys if k["id"] == remembered]
    else:
        mine = [k for k in keys if k.get("name") == KEY_NAME]
    if len(mine) != 1:
        raise AbsError(f"cannot tell the current {KEY_NAME} key ({len(mine)} candidates; write its id to {state_dir / 'key-id'})")
    return mine[0]


def read(path):
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""


def write(path, text, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def rotate(client, me, record, key_file, state_dir, base, now):
    created = client.call("POST", "/api/api-keys", {"name": KEY_NAME, "expiresIn": KEY_DAYS * 86400, "isActive": True, "userId": me["id"]})["apiKey"]
    new_key, new_id = created.pop("apiKey"), created["id"]
    fresh = AbsAdmin(new_key, base)
    try:
        expires = epoch(created.get("expiresAt"))
        if expires is None or expires - now < (KEY_DAYS - 2) * 86400:
            raise AbsError("new key does not expire in about 365 days")
        service_user(fresh)
    except AbsError:
        client.call("DELETE", f"/api/api-keys/{new_id}")
        raise
    write(key_file, new_key + "\n", 0o600)
    write(state_dir / "key-id", new_id + "\n", 0o600)
    fresh.call("DELETE", f"/api/api-keys/{record['id']}")
    return expires


def auto(key_file=KEY_FILE, state_dir=STATE_DIR, base=ABS_URL, now=None, alert=None, force=False):
    now = int(now or time.time())
    result = {"ok": False, "rotated": False, "expires": None, "checked": now, "error": ""}
    try:
        client = AbsAdmin(read(key_file), base)
        me = service_user(client)
        record = current_record(client, me, state_dir)
        result["expires"] = epoch(record.get("expiresAt"))
        if force or result["expires"] is None or result["expires"] - now < ROTATE_BEFORE_DAYS * 86400:
            result["expires"] = rotate(client, me, record, key_file, state_dir, base, now)
            result["rotated"] = True
        elif not read(state_dir / "key-id"):
            write(state_dir / "key-id", record["id"] + "\n", 0o600)
        result["ok"] = True
    except (AbsError, KeyError, ValueError, OSError) as e:
        result["error"] = str(e) or type(e).__name__
    write(state_dir / "status.json", json.dumps(result) + "\n", 0o644)
    messages = []
    if not result["ok"]:
        messages.append(f"beefy: floppy-sync ABS key rotation failed ({result['error']}). journalctl -u floppy-abs-key-rotate")
    if result["expires"] is not None and result["expires"] - now < ALERT_DAYS * 86400:
        messages.append(f"beefy: floppy-sync ABS key expires in {max(0, (result['expires'] - now) // 86400)} days (Floppy README: Rotate)")
    for m in messages:
        (alert or send_alert)(m)
    return result


def send_alert(message):
    subprocess.run([OOB_ALERT, message], check=False, timeout=60)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd in ("auto", "rotate"):
        r = auto(force=cmd == "rotate")
        days = (r["expires"] - r["checked"]) // 86400 if r["expires"] else "never"
        print(f"{'ok' if r['ok'] else 'FAILED'} rotated={r['rotated']} days_left={days}" + (f" error={r['error']}" if r["error"] else ""))
        return 0 if r["ok"] else 1
    if cmd == "status":
        print(read(STATE_DIR / "status.json") or "no run yet")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
