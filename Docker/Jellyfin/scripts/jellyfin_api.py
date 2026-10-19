import json
import pathlib
import urllib.request

BASE = "http://localhost:8096"
KEY_FILE = pathlib.Path(__file__).resolve().parent.parent / "secrets" / "api-key"


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


def user_id(name):
    for user in api("GET", "/Users"):
        if user["Name"] == name:
            return user["Id"].replace("-", "").lower()
    raise KeyError(name)
