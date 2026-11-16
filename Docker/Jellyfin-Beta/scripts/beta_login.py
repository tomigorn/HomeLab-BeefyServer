#!/usr/bin/env python3
"""Signs a household member in to the beta without SSO: they press "Use Quick Connect" on the beta's sign-in page (web, Moonfin, TV) and read the 6-digit code; this approves it for their own Jellyfin user with the beta's admin key.
Usage: beta_login.py CODE USERNAME   (users: beta_login.py --users)"""
import re
import sys
import urllib.error

from beta_api import api

USER_RE = re.compile(r"^[\w.@-]{1,64}$")


def parse(argv):
    if len(argv) != 2 or not re.fullmatch(r"\d{6}", argv[0]) or not USER_RE.match(argv[1]):
        raise ValueError("usage")
    return argv[0], argv[1]


def find(users, name):
    for u in users:
        if u["Name"].lower() == name.lower():
            return u["Id"]
    raise KeyError(name)


def main(argv):
    if argv in (["-h"], ["--help"]):
        print(__doc__.strip())
        return 0
    users = None
    if argv == ["--users"]:
        users = api("GET", "/Users")
        print("\n".join(sorted(u["Name"] for u in users if not u["Policy"].get("IsHidden"))))
        return 0
    try:
        code, name = parse(argv)
    except ValueError:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        uid = find(api("GET", "/Users"), name)
    except KeyError:
        print(f"no beta user {name}; list them: beta_login.py --users", file=sys.stderr)
        return 1
    try:
        api("POST", f"/QuickConnect/Authorize?code={code}&userId={uid}")
    except urllib.error.HTTPError as e:
        print(f"code not accepted (HTTP {e.code}): expired? Press Quick Connect again for a new code", file=sys.stderr)
        return 1
    print(f"approved: {name} is signed in on that device")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
