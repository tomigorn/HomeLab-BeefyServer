#!/usr/bin/env python3
"""Pushes Audiobookshelf listening progress into Floppy (README "Integrations"); Floppy holds no ABS key and opens no connection.

abs-push.py fetch           as the unit's DynamicUser: read ABS with each member's key, spool deltas
abs-push.py apply           as root (unit ExecStartPost=+): feed the spool to Floppy's own importer
abs-push.py link <member>   sudo; asks for the ABS key of the member's non-admin ABS user (hidden)
abs-push.py unlink <member> sudo
abs-push.py selftest        sudo; Floppy's importer on a synthetic book, rolled back (post-update check)
"""
import base64
import getpass
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
HERE = pathlib.Path(__file__).resolve().parent
APPLY_SNIPPET = HERE / "abs-push-apply.py"
KEY_DIR = HERE.parent / "secrets" / "abs-push"
STATE_DIR = "/var/lib/floppy-abs-push"
UNIT = "floppy-abs-push.service"
COVER_REFRESH_SECONDS = 20 * 3600
MAX_COVER_BYTES = 10 * 1024 * 1024
COVER_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# REDIS_URL exists only in the compose entrypoint; the importer queues celery tasks, so it is rebuilt from the secret inside the container.
FLOPPY_SHELL = 'export REDIS_URL="redis://:$(cat /run/secrets/floppy_redis_password)@floppy-redis:6379/0"; exec python manage.py shell -c "$0"'
DOCKER_EXEC = ["/usr/bin/docker", "exec", "-i", "-u", "abc", "-w", "/floppy", "floppy", "sh", "-c", FLOPPY_SHELL]


class AbsError(Exception):
    pass


class AbsNotFound(AbsError):
    pass


class Abs:
    def __init__(self, key, base=ABS_URL):
        self.key = key
        self.base = base

    def _open(self, path):
        req = urllib.request.Request(self.base + path, headers={"Authorization": f"Bearer {self.key}", "User-Agent": "floppy-abs-push"})
        try:
            return urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AbsError(f"ABS rejected the key ({e.code})") from None
            raise (AbsNotFound if e.code == 404 else AbsError)(f"ABS {e.code} for {path.split('?')[0]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise AbsError(f"ABS unreachable ({type(e).__name__})") from None

    def get(self, path):
        with self._open(path) as r:
            return json.load(r)

    def cover(self, item_id):
        try:
            with self._open(f"/api/items/{item_id}/cover") as r:
                ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
                return ctype, r.read(MAX_COVER_BYTES + 1)
        except AbsError:
            return None


def member_keys(cred_dir):
    keys = {}
    for p in sorted(pathlib.Path(cred_dir).glob("key_*")):
        name = p.name[4:]
        if SAFE_NAME.match(name):
            keys[name] = p.read_text().strip()
    return keys


def _entry_key(e):
    item, episode = str(e.get("libraryItemId") or ""), e.get("episodeId")
    for part in filter(None, (item, episode)):
        if not SAFE_ID.match(str(part)):
            raise AbsError("unexpected item id in mediaProgress")
    return f"{item}/{episode}" if episode else item


def _has_cover(item):
    media = item.get("media") or {}
    return bool(item.get("coverPath") or media.get("coverPath"))


def build_payload(member, abs_, state, now):
    me = abs_.get("/api/me")
    if me.get("type") in ("admin", "root"):
        raise AbsError(f"key belongs to an ABS {me.get('type')}; use the member's non-admin ABS user")
    entries = [e for e in me.get("mediaProgress") or [] if e.get("libraryItemId")]
    progress = {_entry_key(e): int(e.get("lastUpdate") or 0) for e in entries}
    covers_at = dict(state.get("covers") or {})
    books = sorted({str(e["libraryItemId"]) for e in entries if not e.get("episodeId")})
    shows = sorted({str(e["libraryItemId"]) for e in entries if e.get("episodeId")})
    due = [b for b in books if now - covers_at.get(b, 0) > COVER_REFRESH_SECONDS]
    if progress == state.get("progress") and not due:
        return None, state

    items = {}
    for path_key in [*books, *(f"{s}?expanded=1" for s in shows)]:
        try:
            items[path_key] = abs_.get(f"/api/items/{path_key}")
        except AbsNotFound:
            pass
    covers = {}
    for b in due:
        if b not in items or not _has_cover(items[b]):
            continue
        got = abs_.cover(b)
        if not got or got[0] not in COVER_TYPES or len(got[1]) > MAX_COVER_BYTES:
            continue
        covers[b] = {"type": got[0], "data": base64.b64encode(got[1]).decode()}
        covers_at[b] = now
    payload = {"user": member, "me": {"type": me.get("type"), "mediaProgress": entries}, "items": items, "covers": covers}
    return payload, {"progress": progress, "covers": {b: t for b, t in covers_at.items() if b in books}}


def write_json(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def load_state(state_dir, member):
    state_path = pathlib.Path(state_dir, "state", f"{member}.json")
    applied = pathlib.Path(state_dir, "applied", f"{member}.json")
    if applied.exists():
        write_json(state_path, json.loads(applied.read_text())["next_state"])
        applied.unlink()
    return json.loads(state_path.read_text()) if state_path.exists() else {}


def mark_applied(state_dir, member):
    dst = pathlib.Path(state_dir, "applied", f"{member}.json")
    dst.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.replace(pathlib.Path(state_dir, "pending", f"{member}.json"), dst)


def fetch(cred_dir, state_dir, now=None):
    now = now or time.time()
    for sub in ("state", "pending", "applied"):
        pathlib.Path(state_dir, sub).mkdir(mode=0o700, exist_ok=True)
    failed = []
    for member, key in member_keys(cred_dir).items():
        try:
            payload, nxt = build_payload(member, Abs(key), load_state(state_dir, member), now)
        except AbsError as e:
            print(f"{member}: {e}")
            failed.append(member)
            continue
        pending = pathlib.Path(state_dir, "pending", f"{member}.json")
        if payload is None:
            pending.unlink(missing_ok=True)
            print(f"{member}: no change")
            continue
        write_json(pending, {"payload": payload, "next_state": nxt})
        print(f"{member}: {len(payload['me']['mediaProgress'])} progress rows, {len(payload['covers'])} covers spooled")
    write_json(pathlib.Path(state_dir, "fetch-failed.json"), failed)


def run_floppy(stdin):
    try:
        r = subprocess.run([*DOCKER_EXEC, APPLY_SNIPPET.read_text()], input=stdin, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return 1, "RESULT timeout\n"
    return r.returncode, r.stdout + r.stderr


def apply_all(state_dir, runner=run_floppy):
    ok = True
    for pending in sorted(pathlib.Path(state_dir, "pending").glob("*.json")):
        member = pending.stem
        code, out = runner(json.dumps(json.loads(pending.read_text())["payload"]))
        results = [line[7:] for line in out.splitlines() if line.startswith("RESULT ")]
        if code == 0 and results and results[-1].startswith("ok"):
            mark_applied(state_dir, member)
            print(results[-1])
        else:
            ok = False
            tail = [line for line in out.splitlines() if "Error" in line or "error" in line][-3:]
            print(f"{member}: Floppy apply failed (exit {code}): {' | '.join(results + tail) or 'no output'}")
    return ok


def link(member):
    key = (getpass.getpass(f"ABS API key for {member}: ") if sys.stdin.isatty() else sys.stdin.readline()).strip()
    me = Abs(key).get("/api/me")
    if me.get("type") in ("admin", "root"):
        sys.exit(f"refused: ABS user {me.get('username')} is {me.get('type')}; use a key of a non-admin ABS user")
    KEY_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chown(KEY_DIR, 0, 0)
    path = KEY_DIR / member
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    os.chown(path, 0, 0)
    print(f"linked {member} -> ABS {me.get('username')} ({me.get('type')}); first push: sudo systemctl start {UNIT}")


def unlink(member):
    (KEY_DIR / member).unlink(missing_ok=True)
    for sub in ("state", "pending", "applied"):
        pathlib.Path(STATE_DIR, sub, f"{member}.json").unlink(missing_ok=True)
    code, out = run_floppy(json.dumps({"user": member, "action": "unlink"}))
    print(next((line[7:] for line in out.splitlines() if line.startswith("RESULT ")), f"Floppy unlink failed (exit {code})"))


def selftest_payload():
    item = "abs-push-selftest"
    entry = {"libraryItemId": item, "lastUpdate": 1, "currentTime": 600, "duration": 3600, "progress": 0.2, "isFinished": False}
    meta = {"title": "Selftest Book", "authors": [{"name": "Jane Doe"}], "genres": ["Test"], "publisher": "Example"}
    return {"dry_run": True, "me": {"type": "user", "mediaProgress": [entry]}, "items": {item: {"id": item, "media": {"duration": 3600, "metadata": meta}}}, "covers": {}}


def selftest(runner=run_floppy):
    code, out = runner(json.dumps(selftest_payload()))
    results = [line[7:] for line in out.splitlines() if line.startswith("RESULT ")]
    print(results[-1] if results else f"selftest failed (exit {code})")
    return code == 0 and bool(results) and results[-1].startswith("ok dry-run")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "fetch":
        fetch(os.environ["CREDENTIALS_DIRECTORY"], os.environ.get("STATE_DIRECTORY", STATE_DIR))
        return 0
    if cmd == "apply":
        state_dir = os.environ.get("STATE_DIRECTORY", STATE_DIR)
        failed_path = pathlib.Path(state_dir, "fetch-failed.json")
        failed = json.loads(failed_path.read_text()) if failed_path.exists() else []
        return 0 if apply_all(state_dir) and not failed else 1
    if cmd == "selftest":
        return 0 if selftest() else 1
    if cmd in ("link", "unlink") and len(argv) == 3 and SAFE_NAME.match(argv[2]):
        (link if cmd == "link" else unlink)(argv[2])
        return 0
    print(__doc__.split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
