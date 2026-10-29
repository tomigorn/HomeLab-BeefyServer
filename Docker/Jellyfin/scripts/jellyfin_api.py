import contextlib
import fcntl
import json
import os
import pathlib
import time
import urllib.request

BASE = "http://localhost:8096"
KEY_FILE = pathlib.Path(__file__).resolve().parent.parent / "secrets" / "api-key"
LOCK_FILE = pathlib.Path(os.environ.get("JF_INJECTOR_LOCK") or pathlib.Path.home() / ".local" / "state" / "jellyfin-injector.lock")


@contextlib.contextmanager
def injector_lock(timeout=300, step=0.5):
    """One writer at a time for the whole JavaScript Injector config (GET, change, POST): the nightly scripts overlap (04:38 / 04:39)."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{LOCK_FILE} held for {timeout} s")
                time.sleep(step)
        yield
    finally:
        os.close(fd)


def api(method, path, body=None):
    key = KEY_FILE.read_text().strip()
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f'MediaBrowser Token="{key}"')
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def api_raw(method, path, data, content_type):
    key = KEY_FILE.read_text().strip()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f'MediaBrowser Token="{key}"')
    req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def user_id(name):
    for user in api("GET", "/Users"):
        if user["Name"] == name:
            return user["Id"].replace("-", "").lower()
    raise KeyError(name)
