"""Seerr-compatible facade for the Moonfin app (Jellyfin README "Moonfin requests (Seerr facade)")."""
import base64
import binascii
import datetime
import hashlib
import hmac
import json
import pathlib
import re
import secrets

FIRST_UID = 1000001
COOKIE = "connect.sid"
MAX_AGE = 30 * 86400
ROTATE_AFTER = 86400
MAX_COOKIE = 1024
B64_RE = re.compile(r"[A-Za-z0-9_-]+")
KEY_RE = re.compile(r"[0-9a-fA-F]{64}")


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def mac(key, body):
    return b64(hmac.new(key, body.encode(), hashlib.sha256).digest())


def make_cookie(key, sid, uid, iat, exp):
    body = b64(json.dumps({"v": 1, "sid": sid, "uid": uid, "iat": iat, "exp": exp}, separators=(",", ":")).encode())
    return f"s:{body}.{mac(key, body)}"


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def read_cookie(key, cookie_header, now):
    """Payload of the one valid signed connect.sid cookie in a Cookie header, else None."""
    if not key or not cookie_header:
        return None
    values = []
    for part in cookie_header.split(";"):
        name, eq, value = part.strip().partition("=")
        if eq and name == COOKIE:
            values.append(value.strip())
    if len(values) != 1 or len(values[0]) > MAX_COOKIE:
        return None
    value = values[0]
    if value.startswith("s:"):
        value = value[2:]
    elif value[:4].lower() == "s%3a":
        value = value[4:]
    else:
        return None
    body, dot, sig = value.partition(".")
    if not dot or not B64_RE.fullmatch(body) or not B64_RE.fullmatch(sig) or not hmac.compare_digest(sig, mac(key, body)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, binascii.Error):
        return None
    if not isinstance(data, dict) or data.get("v") != 1 or not is_int(data.get("v")):
        return None
    if not isinstance(data.get("sid"), str) or not data["sid"] or not all(is_int(data.get(k)) for k in ("uid", "iat", "exp")):
        return None
    if not data["iat"] <= now < data["exp"]:
        return None
    return data


def set_cookie_header(value):
    return f"{COOKIE}={value}; HttpOnly; Path=/; SameSite=Strict; Max-Age={MAX_AGE}"


def load_key(path):
    try:
        text = pathlib.Path(path).read_text().strip()
    except (OSError, TypeError):
        return None
    return bytes.fromhex(text) if KEY_RE.fullmatch(text) else None


class Sessions:
    """Facade users (stable synthetic Seerr ids) and sign-in sessions in the shared state db."""

    def __init__(self, core):
        self.core = core

    def init(self):
        with self.core.lock, self.core.db() as con:
            con.execute("CREATE TABLE IF NOT EXISTS facade_users (jf_id TEXT PRIMARY KEY, uid INTEGER UNIQUE NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, jf_id TEXT NOT NULL, created TEXT, last_seen TEXT, revoked INTEGER NOT NULL DEFAULT 0)")

    def uid_for(self, jf_id):
        with self.core.lock, self.core.db() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT uid FROM facade_users WHERE jf_id = ?", (jf_id,)).fetchone()
                if row:
                    return row[0]
                top = con.execute("SELECT MAX(uid) FROM facade_users").fetchone()[0]
                uid = max(FIRST_UID, (top or 0) + 1)
                con.execute("INSERT INTO facade_users (jf_id, uid) VALUES (?, ?)", (jf_id, uid))
                return uid
            finally:
                con.execute("COMMIT")

    def open(self, jf_id):
        sid = secrets.token_urlsafe(24)
        now = self.core.iso(self.core.clock())
        with self.core.lock, self.core.db() as con:
            con.execute("INSERT INTO sessions (sid, jf_id, created, last_seen) VALUES (?, ?, ?, ?)", (sid, jf_id, now, now))
        return sid

    def active(self, sid, jf_id):
        with self.core.db() as con:
            row = con.execute("SELECT 1 FROM sessions WHERE sid = ? AND jf_id = ? AND revoked = 0", (sid, jf_id)).fetchone()
        return row is not None

    def touch(self, sid):
        with self.core.lock, self.core.db() as con:
            con.execute("UPDATE sessions SET last_seen = ? WHERE sid = ?", (self.core.iso(self.core.clock()), sid))

    def revoke(self, sid):
        with self.core.lock, self.core.db() as con:
            con.execute("UPDATE sessions SET revoked = 1 WHERE sid = ?", (sid,))

    def prune(self):
        """Revoked sessions, and sessions unused for longer than a cookie lives (their cookies have expired)."""
        cutoff = self.core.iso(self.core.clock() - datetime.timedelta(seconds=MAX_AGE))
        with self.core.lock, self.core.db() as con:
            con.execute("DELETE FROM sessions WHERE revoked = 1 OR COALESCE(last_seen, created) < ?", (cutoff,))
