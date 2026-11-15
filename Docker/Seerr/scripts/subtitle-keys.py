#!/usr/bin/env python3
"""Copies the subtitle provider logins the indexer bar needs from Bazarr into secrets/indexer-bar (README "Indexer bar"): SubDL key (subdl.apikey) and OpenSubtitles username, password and the API key Bazarr uses (opensubtitles.json), mode 0400.
Re-run after changing them in Bazarr or after a Bazarr update. Needs sudo (Bazarr's config is root-only); never prints a secret.
Usage: scripts/subtitle-keys.py [-h]"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "secrets" / "indexer-bar"
CONFIG = "/srv/appdata/bazarr/config/config.yaml"
CONTAINER = "movie-bazarr"
PROVIDERS = "/app/bazarr/bin/bazarr/app/get_providers.py"


def extract(config_text, providers_src):
    cfg = yaml.safe_load(config_text) or {}
    subdl = (cfg.get("subdl") or {}).get("api_key") or None
    o = cfg.get("opensubtitlescom") or {}
    m = re.search(r"'opensubtitlescom':\s*\{[^}]*?'api_key':\s*'([^']+)'", providers_src)
    opensubs = {"username": o["username"], "password": o["password"], "api_key": m.group(1)} if m and o.get("username") and o.get("password") else None
    return subdl, opensubs


def save(out, subdl, opensubs):
    out = pathlib.Path(out)
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    names = []
    for name, text in (("subdl.apikey", subdl and subdl + "\n"), ("opensubtitles.json", opensubs and json.dumps(opensubs))):
        if not text:
            continue
        fd, tmp = tempfile.mkstemp(dir=out, prefix=".key-")
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o400)
        os.replace(tmp, out / name)
        names.append(name)
    return names


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] in ("-h", "--help"):
            print(__doc__)
            return 0
        print(f"unknown option {sys.argv[1]!r}, try: {sys.argv[0]} --help", file=sys.stderr)
        return 2
    try:
        config = subprocess.run(["sudo", "cat", os.environ.get("IB_BAZARR_CONFIG", CONFIG)], check=True, capture_output=True, text=True).stdout
        src = subprocess.run(["sudo", "docker", "exec", os.environ.get("IB_BAZARR_CONTAINER", CONTAINER), "cat", PROVIDERS],
                             check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as e:
        print(f"could not read Bazarr's config or code ({e.cmd[1]} failed); is Bazarr running? next: sudo docker ps | grep bazarr", file=sys.stderr)
        return 1
    subdl, opensubs = extract(config, src)
    names = save(OUT, subdl, opensubs)
    for name, ok in (("SubDL key", subdl), ("OpenSubtitles login + API key", opensubs)):
        if not ok:
            print(f"missing in Bazarr: {name} (that provider stays estimated in the bar)", file=sys.stderr)
    print(f"written: {', '.join(names) or 'nothing'} in {OUT}")
    print("next: sudo systemctl start indexer-usage.service && jq '.subtitles' /srv/appdata/indexer-bar/usage.json")
    return 0 if names else 1


if __name__ == "__main__":
    sys.exit(main())
