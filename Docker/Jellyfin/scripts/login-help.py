#!/usr/bin/env python3
"""Idempotent: the Jellyfin sign-in page (web, phone apps, TV web apps) shows a link to the public getting-started guide.

Usage: python3 login-help.py
"""
import sys

import jellyfin_api as j

TEXT = ('New here or stuck? <a href="https://wiki.holy-grail.ch/getting-started/" target="_blank" rel="noopener noreferrer">'
        'Help: getting started with Holy Grail</a>')


def updated(branding):
    if branding.get("LoginDisclaimer") == TEXT:
        return None
    return {**branding, "LoginDisclaimer": TEXT}


def main():
    if len(sys.argv) > 1:
        print(__doc__.strip())
        return
    new = updated(j.api("GET", "/System/Configuration/branding") or {})
    if new is None:
        print("login help: ok")
        return
    j.api("POST", "/System/Configuration/branding", new)
    print("login help: set. Check: open https://jellyfin.holy-grail.ch/ signed out")


if __name__ == "__main__":
    main()
