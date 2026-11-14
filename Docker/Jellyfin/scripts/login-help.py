#!/usr/bin/env python3
"""Idempotent: the Jellyfin sign-in page (web, phone apps, TV web apps) links the Jellyfin page of the wiki.

Usage: python3 login-help.py
"""
import sys

import jellyfin_api as j

TEXT = ('Need help? <a href="https://wiki.holy-grail.ch/apps/jellyfin/" target="_blank" rel="noopener noreferrer">'
        'Watching with Jellyfin</a> in the Holy Grail wiki')


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
