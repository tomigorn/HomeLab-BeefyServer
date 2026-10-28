#!/usr/bin/env python3
"""Pushes Audiobookshelf listening progress into Floppy (README "Integrations"); Floppy holds no ABS key and opens no connection.

abs-push.py targets     as root (unit ExecStartPre=+): Floppy usernames minus skip/exclude list -> spool
abs-push.py fetch       as the unit's DynamicUser: GET-only, the floppy-sync key, spool deltas per matching ABS user
abs-push.py apply       as root (unit ExecStartPost=+): feed the spool to Floppy's own importer
abs-push.py store-key   sudo; asks for the floppy-sync ABS API key (hidden), checks it, stores it root-only
abs-push.py selftest    sudo; Floppy's importer on a synthetic book, rolled back (post-update check)
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
SERVICE_USER = "floppy-sync"
SKIP_USERS = {"abr", "audiobook-importer", SERVICE_USER, "root"}
HERE = pathlib.Path(__file__).resolve().parent
APPLY_SNIPPET = HERE / "abs-push-apply.py"
KEY_FILE = HERE.parent / "secrets" / "floppy-sync-abs-key"
EXCLUDE_FILE = pathlib.Path("/etc/floppy-abs-push/exclude")
STATE_DIR = "/var/lib/floppy-abs-push"
UNIT = "floppy-abs-push.service"
COVER_REFRESH_SECONDS = 20 * 3600
MAX_COVER_BYTES = 10 * 1024 * 1024
COVER_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
BROWSER_ONLY = ("displayTitle", "displaySubtitle", "coverPath", "mediaUpdatedAt")
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SAFE_PATH = re.compile(r"^/api/[A-Za-z0-9_/-]+(\?expanded=1)?$")
# REDIS_URL exists only in the compose entrypoint; the importer queues celery tasks, so it is rebuilt from the secret inside the container.
FLOPPY_SHELL = 'export REDIS_URL="redis://:$(cat /run/secrets/floppy_redis_password)@floppy-redis:6379/0"; exec python manage.py shell -c "$0"'
DOCKER_EXEC = ["/usr/bin/docker", "exec", "-i", "-u", "abc", "-w", "/floppy", "floppy", "sh", "-c", FLOPPY_SHELL]
FLOPPY_USERS_CODE = 'import json; from users.models import User; print("RESULT", json.dumps(list(User.objects.filter(is_active=True).values_list("username", flat=True))))'


class AbsError(Exception):
    pass


class AbsNotFound(AbsError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Abs:
    """Read-only ABS client: GET to the fixed ABS URL only, no redirects (the key is admin-capable)."""

    def __init__(self, key, base=ABS_URL):
        self.key = key
        self.base = base
        self.opener = urllib.request.build_opener(NoRedirect)

    def _open(self, path, method="GET"):
        if method != "GET":
            raise AbsError(f"refused: {method} (GET only)")
        if not SAFE_PATH.match(path) or ".." in path:
            raise AbsError("refused: path outside /api/")
        req = urllib.request.Request(self.base + path, headers={"Authorization": f"Bearer {self.key}", "User-Agent": "floppy-abs-push"}, method="GET")
        if req.get_method() != "GET" or req.data is not None:
            raise AbsError("refused: not a plain GET")
        try:
            return self.opener.open(req, timeout=20)
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


def check_service(abs_):
    me = abs_.get("/api/me")
    if (me.get("username"), me.get("type"), me.get("isActive", True)) != (SERVICE_USER, "admin", True):
        raise AbsError(f"key is not the active ABS admin {SERVICE_USER} (got {me.get('username')}, {me.get('type')})")
    return me


def read_exclude(path):
    try:
        lines = pathlib.Path(path).read_text().splitlines()
    except FileNotFoundError:
        return []
    return [n for n in (line.split("#")[0].strip() for line in lines) if n]


def targets(floppy, exclude):
    skip = SKIP_USERS | set(exclude)
    return sorted({u for u in floppy if SAFE_NAME.match(u) and u not in skip})


def run_floppy_code(code):
    try:
        r = subprocess.run([*DOCKER_EXEC, code], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return 1, ""
    return r.returncode, r.stdout


def floppy_users(runner=run_floppy_code):
    code, out = runner(FLOPPY_USERS_CODE)
    lines = [line[7:] for line in out.splitlines() if line.startswith("RESULT ")]
    if code != 0 or not lines:
        raise AbsError(f"Floppy user list failed (exit {code})")
    return [u for u in json.loads(lines[-1]) if isinstance(u, str) and SAFE_NAME.match(u)]


def synced_users(abs_, wanted):
    wanted = set(wanted) - SKIP_USERS
    out = []
    for u in abs_.get("/api/users").get("users") or []:
        name = u.get("username")
        if name not in wanted or u.get("type") == "root" or not u.get("isActive"):
            continue
        if not SAFE_ID.match(str(u.get("id") or "")):
            raise AbsError("unexpected user id in /api/users")
        out.append((u["id"], name))
    return sorted(out, key=lambda x: x[1])


def _entry_key(e):
    item, episode = str(e.get("libraryItemId") or ""), e.get("episodeId")
    for part in filter(None, (item, episode)):
        if not SAFE_ID.match(str(part)):
            raise AbsError("unexpected item id in mediaProgress")
    return f"{item}/{episode}" if episode else item


def _has_cover(item):
    media = item.get("media") or {}
    return bool(item.get("coverPath") or media.get("coverPath"))


def build_payload(member, user_id, abs_, state, now):
    user = abs_.get(f"/api/users/{user_id}")
    entries = [{k: v for k, v in e.items() if k not in BROWSER_ONLY} for e in user.get("mediaProgress") or [] if e.get("libraryItemId")]
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
    payload = {"user": member, "me": {"type": user.get("type"), "mediaProgress": entries}, "items": items, "covers": covers}
    return payload, {"progress": progress, "covers": {b: t for b, t in covers_at.items() if b in books}}


# Root runs targets and apply inside the DynamicUser's state dir: never follow a link it planted there.
def open_dir(path):
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def write_json(path, data, mode=0o600):
    path = pathlib.Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    dfd = open_dir(path.parent)
    try:
        tmp = f".{path.name}.{os.urandom(8).hex()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dfd)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            os.unlink(tmp, dir_fd=dfd)
            raise
    finally:
        os.close(dfd)


def read_json(path):
    path = pathlib.Path(path)
    dfd = open_dir(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
    finally:
        os.close(dfd)
    with os.fdopen(fd) as f:
        return json.load(f)


def load_state(state_dir, member):
    state_path = pathlib.Path(state_dir, "state", f"{member}.json")
    applied = pathlib.Path(state_dir, "applied", f"{member}.json")
    if applied.exists():
        write_json(state_path, json.loads(applied.read_text())["next_state"])
        applied.unlink()
    return json.loads(state_path.read_text()) if state_path.exists() else {}


def mark_applied(state_dir, member):
    pathlib.Path(state_dir, "applied").mkdir(mode=0o700, exist_ok=True)
    src, dst = open_dir(pathlib.Path(state_dir, "pending")), open_dir(pathlib.Path(state_dir, "applied"))
    try:
        os.replace(f"{member}.json", f"{member}.json", src_dir_fd=src, dst_dir_fd=dst)
    finally:
        os.close(src)
        os.close(dst)


def write_targets(state_dir):
    names = targets(floppy_users(), read_exclude(EXCLUDE_FILE))
    # Root writes it, the DynamicUser reads it; the state dir itself is 0700.
    write_json(pathlib.Path(state_dir, "targets.json"), names, mode=0o644)
    print(f"targets: {len(names)} Floppy users")


def fetch(cred_dir, state_dir, now=None):
    now = now or time.time()
    for sub in ("state", "pending", "applied"):
        pathlib.Path(state_dir, sub).mkdir(mode=0o700, exist_ok=True)
    failed = []
    abs_ = Abs(pathlib.Path(cred_dir, "abs-key").read_text().strip())
    try:
        check_service(abs_)
        users = synced_users(abs_, json.loads(pathlib.Path(state_dir, "targets.json").read_text()))
    except AbsError as e:
        print(f"{SERVICE_USER}: {e}")
        users, failed = [], [SERVICE_USER]
    for user_id, member in users:
        try:
            payload, nxt = build_payload(member, user_id, abs_, load_state(state_dir, member), now)
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
        try:
            payload = read_json(pending)["payload"]
        except OSError as e:
            ok = False
            print(f"{member}: pending file refused ({e.strerror})")
            continue
        code, out = runner(json.dumps(payload))
        results = [line[7:] for line in out.splitlines() if line.startswith("RESULT ")]
        if code == 0 and results and results[-1].startswith("ok"):
            mark_applied(state_dir, member)
            print(results[-1])
        else:
            ok = False
            tail = [line for line in out.splitlines() if "Error" in line or "error" in line][-3:]
            print(f"{member}: Floppy apply failed (exit {code}): {' | '.join(results + tail) or 'no output'}")
    return ok


def _chown_root(path):
    os.chown(path, 0, 0)


def store_key(key, path=KEY_FILE, chown=_chown_root):
    key = key.strip()
    if not key:
        raise AbsError("no key given")
    me = check_service(Abs(key))
    path = pathlib.Path(path)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    chown(tmp)
    os.replace(tmp, path)
    return f"stored the key of ABS user {me['username']} ({me['type']}) in {path} (root:root 600); first sync: sudo systemctl start {UNIT}"


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
    state_dir = os.path.realpath(os.environ.get("STATE_DIRECTORY", STATE_DIR))
    if cmd == "targets":
        write_targets(state_dir)
        return 0
    if cmd == "fetch":
        fetch(os.environ["CREDENTIALS_DIRECTORY"], state_dir)
        return 0
    if cmd == "apply":
        failed_path = pathlib.Path(state_dir, "fetch-failed.json")
        try:
            failed = read_json(failed_path)
        except FileNotFoundError:
            failed = []
        return 0 if apply_all(state_dir) and not failed else 1
    if cmd == "selftest":
        return 0 if selftest() else 1
    if cmd == "store-key":
        if os.geteuid() != 0:
            sys.exit("run with sudo")
        key = getpass.getpass(f"ABS API key of {SERVICE_USER} (hidden): ") if sys.stdin.isatty() else sys.stdin.readline()
        try:
            print(store_key(key))
        except AbsError as e:
            sys.exit(f"refused, nothing stored: {e}")
        return 0
    print(__doc__.split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
