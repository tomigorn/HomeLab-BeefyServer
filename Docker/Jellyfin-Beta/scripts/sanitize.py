#!/usr/bin/env python3
"""Makes a copy of prod's Jellyfin config safe as the beta channel: no timed scheduled task triggers (startup ones stay), no library monitor/trickplay/chapter images/NFO writes, no webhooks, no Seerr, no Intro Skipper auto analysis, own server id and name and a BETA login note.
OIDC: with secrets/oidc.json (client_id, client_secret, issuer, public_url; written by the beta's Authentik provider) the SSO plugin points at the beta provider and the copied identity links follow it; without it the plugin is switched off. Password sign-in stays off for SSO-linked users, on for the local beta-admin.
Run as root (files belong to the container uid). Usage: sanitize.py [--check] [CONFIG_DIR]  (default /srv/appdata/jellyfin-beta; --check exits 1 when something would change)"""
import json
import os
import pathlib
import re
import sys
import uuid

BETA = pathlib.Path("/srv/appdata/jellyfin-beta")
PROD = pathlib.Path("/srv/appdata/jellyfin")
OIDC_FILE = pathlib.Path(__file__).resolve().parent.parent / "secrets" / "oidc.json"
ID_FILE = pathlib.Path(__file__).resolve().parent.parent / "secrets" / "server-id"
SERVER_NAME = "Holy Grail Beta"
BETA_NOTE = "<b>BETA</b> channel: a test copy of Jellyfin. Watch history here is not kept. "

LIBRARY = {
    "EnableRealtimeMonitor": "false",
    "EnableTrickplayImageExtraction": "false",
    "ExtractTrickplayImagesDuringLibraryScan": "false",
    "EnableChapterImageExtraction": "false",
    "ExtractChapterImagesDuringLibraryScan": "false",
    "SaveLocalMetadata": "false",
    "SaveSubtitlesWithMedia": "false",
    "SaveLyricsWithMedia": "false",
    "SaveTrickplayWithMedia": "false",
    "AutomaticRefreshIntervalDays": "0",
}
PLUGINS = {
    "Moonfin.Server.xml": {"SeerrEnabled": "false", "SeerrUrl": "", "PushRelayUrl": "", "ImdbListsEnabled": "false", "MdblistOfficialListsEnabled": "false",
                           "StudioLogosEnabled": "false", "RecommendationsProviderEnabled": "false", "StuckDownloadNotify": "false"},
    "IntroSkipper.xml": {"AutoDetectIntros": "false", "AutoDetectCredits": "false"},
}
EMPTY_HOOKS = '<?xml version="1.0" encoding="utf-8"?>\n<PluginConfiguration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n  <Hooks />\n</PluginConfiguration>'


def set_values(text, values):
    for tag, value in values.items():
        text = re.sub(rf"<{tag}>[^<]*</{tag}>|<{tag}\s*/>", f"<{tag}>{value}</{tag}>", text)
    return text


def xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def oidc(text, conf):
    # SSO users keep prod's no-password rule; only the unlinked local beta-admin may use a password (direct port only: Traefik blocks password logins).
    text = set_values(text, {"PasswordLoginMode": "DisableForLinkedUsersOnly"})
    if not conf:
        return set_values(text, {"Enabled": "false"})
    old_issuer = re.search(r"<IssuerUrl>([^<]*)</IssuerUrl>", text)
    text = set_values(text, {"Enabled": "true", "ClientId": xml_escape(conf["client_id"]), "ClientSecret": xml_escape(conf["client_secret"]),
                             "IssuerUrl": xml_escape(conf["issuer"]), "PublicUrl": xml_escape(conf["public_url"])})
    if old_issuer and old_issuer.group(1) != conf["issuer"]:
        text = text.replace(f"<Issuer>{old_issuer.group(1)}</Issuer>", f"<Issuer>{xml_escape(conf['issuer'])}</Issuer>")
    # Sessions belong to prod's tokens; the beta starts without them.
    text = re.sub(r"<OidcSessions>.*?</OidcSessions>", "<OidcSessions />", text, flags=re.S)
    return text


def wanted(config, oidc_conf, prod_id=None, beta_id=None):
    """{path: new text} for every file that differs."""
    out = {}
    device = config / "data" / "data" / "device.txt"
    if device.is_file() and prod_id and device.read_text().strip() == prod_id.strip():
        # Own server id, the same after every refresh: apps and Moonfin must not mistake the beta for prod.
        out[device] = beta_id or uuid.uuid4().hex

    def put(path, new):
        if path.is_file() and path.read_text() != new:
            out[path] = new

    for f in sorted((config / "ScheduledTasks").glob("*.js")):
        try:
            triggers = json.loads(f.read_text())
        except ValueError:
            triggers = []
        startup = [t for t in triggers if isinstance(t, dict) and t.get("Type") == "StartupTrigger"]
        if startup != triggers:
            out[f] = json.dumps(startup, separators=(",", ":"))
    for f in sorted((config / "data" / "root" / "default").glob("*/options.xml")):
        put(f, set_values(f.read_text(), LIBRARY))
    conf = config / "data" / "plugins" / "configurations"
    hooks = conf / "Jellyfin.Webhooks.xml"
    if hooks.is_file() and "<HookConfig>" in hooks.read_text():
        out[hooks] = EMPTY_HOOKS
    for name, values in PLUGINS.items():
        f = conf / name
        if f.is_file():
            put(f, set_values(f.read_text(), values))
    f = conf / "Jellyfin.Plugin.Oidc.xml"
    if f.is_file():
        put(f, oidc(f.read_text(), oidc_conf))
    f = config / "system.xml"
    if f.is_file():
        put(f, set_values(f.read_text(), {"ServerName": SERVER_NAME}))
    f = config / "branding.xml"
    if f.is_file():
        text = f.read_text()
        m = re.search(r"<LoginDisclaimer>(.*?)</LoginDisclaimer>", text, re.S)
        note = xml_escape(BETA_NOTE)
        if m and not m.group(1).startswith(note):
            put(f, text[:m.start(1)] + note + m.group(1) + text[m.end(1):])
    return out


def run(config, check, oidc_file=OIDC_FILE, prod_id=None, id_file=ID_FILE):
    if config.resolve() == PROD.resolve():
        raise SystemExit("refusing to touch prod's config")
    if prod_id is None:
        prod_device = PROD / "data" / "data" / "device.txt"
        prod_id = prod_device.read_text() if prod_device.is_file() else None
    oidc_conf = json.loads(oidc_file.read_text()) if oidc_file.is_file() else None
    beta_id = id_file.read_text().strip() if id_file.is_file() else None
    todo = wanted(config, oidc_conf, prod_id, beta_id)
    for path in todo:
        print(("would change " if check else "changed ") + str(path.relative_to(config)))
    if check:
        return 1 if todo else 0
    for path, text in todo.items():
        st = path.stat()
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(text)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass
        tmp.chmod(st.st_mode & 0o7777)
        tmp.replace(path)
    device = config / "data" / "data" / "device.txt"
    if device.is_file() and not (id_file.is_file() and id_file.read_text() == device.read_text()):
        id_file.write_text(device.read_text())
    if not todo:
        print("unchanged")
    return 0


def main(argv):
    args = [a for a in argv if a != "--check"]
    if len(args) > 1 or any(a.startswith("-") for a in args):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return run(pathlib.Path(args[0]) if args else BETA, "--check" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
