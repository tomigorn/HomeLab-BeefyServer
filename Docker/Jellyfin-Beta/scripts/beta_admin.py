#!/usr/bin/env python3
"""The beta's own pieces after a copy from prod: its API key (secrets/api-key, created with prod's key, which the copied DB still holds) and the local beta-admin (password in secrets/beta-admin.password, created once).
Usage: beta_admin.py [--check]"""
import json
import pathlib
import secrets
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
SECRETS = HERE.parent / "secrets"
PROD_KEY = HERE.parent.parent / "Jellyfin" / "secrets" / "api-key"
BASE = "http://localhost:8196"
NAME = "beta-admin"
APP = "jellyfin-beta-scripts"
DEFAULT_PROVIDER = "Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider"


def call(key, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Authorization": f'MediaBrowser Token="{key}"', "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def key_works(key):
    try:
        call(key, "GET", "/System/Info")
        return True
    except (urllib.error.URLError, OSError):
        return False


def ensure_key(check):
    f = SECRETS / "api-key"
    if f.is_file() and key_works(f.read_text().strip()):
        return f.read_text().strip(), False
    if check:
        return None, True
    prod = PROD_KEY.read_text().strip()
    call(prod, "POST", f"/Auth/Keys?app={APP}")
    key = [k for k in call(prod, "GET", "/Auth/Keys")["Items"] if k["AppName"] == APP][-1]["AccessToken"]
    f.parent.mkdir(mode=0o700, exist_ok=True)
    f.write_text(key)
    f.chmod(0o600)
    return key, True


def ensure_admin(key, check):
    pw_file = SECRETS / "beta-admin.password"
    if not pw_file.is_file():
        if check:
            return True
        pw_file.write_text(secrets.token_urlsafe(18))
        pw_file.chmod(0o600)
    pw = pw_file.read_text().strip()
    user = next((u for u in call(key, "GET", "/Users") if u["Name"] == NAME), None)
    if user is None:
        if check:
            return True
        try:
            call(key, "POST", "/Users/New", {"Name": NAME, "Password": pw})
        except urllib.error.HTTPError:
            pass  # 12.4 + the OIDC plugin answer 401 but create the user
        user = next(u for u in call(key, "GET", "/Users") if u["Name"] == NAME)
    pol = user["Policy"]
    if pol.get("IsAdministrator") and pol.get("IsHidden") and pol.get("AuthenticationProviderId") == DEFAULT_PROVIDER:
        return False
    if check:
        return True
    call(key, "POST", f"/Users/{user['Id']}/Policy", {**pol, "IsAdministrator": True, "IsHidden": True, "AuthenticationProviderId": DEFAULT_PROVIDER})
    call(key, "POST", f"/Users/{user['Id']}/Password", {"NewPw": pw, "ResetPassword": False})
    return True


def main(argv):
    if any(a != "--check" for a in argv):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    check = bool(argv)
    key, k_changed = ensure_key(check)
    a_changed = ensure_admin(key, check) if key else True
    print(f"api key {'missing' if check and k_changed else 'new' if k_changed else 'ok'}, {NAME} {'to fix' if check and a_changed else 'set' if a_changed else 'ok'}")
    return 1 if check and (k_changed or a_changed) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
