#!/usr/bin/env python3
"""Point Seerr's webhook agent at Floppy's shared Seerr webhook (requests land in the requester's Tracker as Planning).

  seerr-webhook.py apply    secret from ../../Floppy/secrets/floppy_seerr_webhook_secret (sudo)
  seerr-webhook.py test     Seerr sends its test notification (Floppy answers 400 "Missing requester")
  seerr-webhook.py show
"""
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:5055/api/v1"
URL = "http://192.168.1.102:8012/webhook/seerr/global/"
# MEDIA_PENDING 2, MEDIA_APPROVED 4, MEDIA_AVAILABLE 8, MEDIA_AUTO_APPROVED 128
TYPES = 2 | 4 | 8 | 128
SETTINGS = "/srv/appdata/seerr/settings.json"
SECRET = pathlib.Path(__file__).resolve().parent.parent.parent / "Floppy" / "secrets" / "floppy_seerr_webhook_secret"
FIELDS = ["notification_type", "media_type", "media_tmdbid", "media_status", "requestedBy_username"]


def sudo_read(path):
    return subprocess.run(["sudo", "cat", str(path)], capture_output=True, text=True, check=True).stdout


def template(secret):
    body = ",\n".join([f'  "secret": {json.dumps(secret)}'] + [f'  "{f}": "{{{{{f}}}}}"' for f in FIELDS])
    return "{\n" + body + "\n}"


def api(method, path, body=None):
    key = json.loads(sudo_read(SETTINGS))["main"]["apiKey"]
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"X-Api-Key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def body(secret):
    return {"enabled": True, "types": TYPES, "options": {"webhookUrl": URL, "jsonPayload": template(secret), "authHeader": ""}}


def main(argv):
    if argv == ["show"]:
        cur = api("GET", "/settings/notifications/webhook")
        print(cur.get("enabled"), cur.get("types"), cur["options"].get("webhookUrl"))
        return 0
    if argv not in (["apply"], ["test"]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    secret = sudo_read(SECRET).strip()
    if argv == ["test"]:
        try:
            api("POST", "/settings/notifications/webhook/test", body(secret))
        except urllib.error.HTTPError as e:
            print(f"Seerr test notification sent, Seerr says {e.code}: expected, the test has no requester; check the Floppy log")
            return 0
        print("Seerr test notification sent")
        return 0
    api("POST", "/settings/notifications/webhook", body(secret))
    print("Seerr webhook -> Floppy applied")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
