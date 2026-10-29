"""Quick Connect sign-in of an existing user for scripted checks: the admin key authorizes the code, the token is never printed. logout() only ends the session; devices are never deleted."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import jellyfin_api

VALUE_RE = re.compile(r'^[^",=\x00-\x1f]{1,64}$')
USER_RE = re.compile(r"^[0-9A-Za-z-]{1,64}$")


class AuthorizeRefused(Exception):
    """The admin key may not authorize Quick Connect: a person approves the code once in the dashboard."""

    def __init__(self, code):
        super().__init__(f"Quick Connect authorize refused, approve code {code} in Jellyfin")
        self.code = code


def header(client, device, device_id, version, token=None):
    for v in (client, device, device_id, version):
        if not VALUE_RE.match(v or ""):
            raise ValueError("bad client header value")
    h = f'MediaBrowser Client="{client}", Device="{device}", DeviceId="{device_id}", Version="{version}"'
    return h + f', Token="{token}"' if token else h


def http(method, path, body=None, auth=""):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(jellyfin_api.BASE + path, data=data, method=method, headers={"Authorization": auth})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def token(client, device, device_id, version, user_id, http=http, admin=None):
    admin = admin or jellyfin_api.api
    auth = header(client, device, device_id, version)
    if not USER_RE.match(user_id or ""):
        raise ValueError("bad user id")
    started = http("POST", "/QuickConnect/Initiate", None, auth)
    code = str(started["Code"])
    try:
        admin("POST", f"/QuickConnect/Authorize?code={urllib.parse.quote(code)}&userId={user_id}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthorizeRefused(code) from None
        raise
    return http("POST", "/Users/AuthenticateWithQuickConnect", {"Secret": started["Secret"]}, auth)["AccessToken"]


def logout(tok, device_id, http=http):
    http("POST", "/Sessions/Logout", None, header("hss-trending", "hss-trending", device_id, "1", tok))
