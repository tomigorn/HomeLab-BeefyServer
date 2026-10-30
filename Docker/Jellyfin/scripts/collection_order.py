"""Moonfin collection detail order (Moonbase 2.5 `GET /Moonfin/Collections/{id}/Order`): one JSON array of item ids per user and collection in the plugin's per-user store.
A non-empty list replaces the app's release-year order; Moonfin 2.6/2.7 drop members missing from it, so every list holds all members.
Each scope (trending, gems) owns the files it wrote (state file); files of other collections are never touched. Writes as the plugin owner (sudo + setpriv)."""
import json
import os
import pathlib
import re
import subprocess

ROOT = pathlib.Path("/srv/appdata/jellyfin/data/plugins/configurations/Moonfin/collections")
STATE = pathlib.Path.home() / ".local" / "state" / "jellyfin-collection-order.json"
HEX = re.compile(r"^[0-9a-f]{32}$")


class SudoFs:
    """File operations as the plugin owner (setpriv drops root before each command, so a swapped path grants nothing the plugin does not have); dirs 2775, files 0664 like the plugin's own; a write lands by rename."""

    def __init__(self, uid=None, gid=None, run=subprocess.run):
        if uid is None or gid is None:
            st = ROOT.parent.stat()
            uid, gid = st.st_uid, st.st_gid
        self.drop = ["sudo", "-n", "setpriv", f"--reuid={uid}", f"--regid={gid}", "--clear-groups"]
        self.run = run

    def mkdir(self, path):
        self.run(self.drop + ["mkdir", "-m", "2775", "--", str(path)], check=True)

    def write(self, path, data):
        tmp = str(path.with_name(f".{path.name}.tmp"))
        try:
            self.run(self.drop + ["tee", "--", tmp], input=data, stdout=subprocess.DEVNULL, check=True)
            self.run(self.drop + ["chmod", "0664", "--", tmp], check=True)
            self.run(self.drop + ["mv", "-f", "-T", "--", tmp, str(path)], check=True)
        except subprocess.CalledProcessError:
            self.run(self.drop + ["rm", "-f", "--", tmp], check=False)
            raise

    def remove(self, path):
        self.run(self.drop + ["rm", "-f", "--", str(path)], check=True)


def plain(i):
    v = str(i).replace("-", "").lower()
    if not HEX.match(v):
        raise ValueError(f"not an id: {i!r}")
    return v


def read_state(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_state(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def sync(scope, users, orders, dry_run, log, root=ROOT, state=STATE, fs=None):
    """Writes {collection id: [item ids]} for every user id; removes the files this scope wrote before for collections or users gone. Returns the number of files written or removed.
    The state keeps every pair written so far, also when a step fails halfway."""
    users = sorted({plain(u) for u in users})
    orders = {plain(c): [str(i) for i in ids] for c, ids in orders.items()}
    saved = read_state(state)
    old = {tuple(p) for p in saved.get(scope) or []}
    want = {(u, c) for u in users for c in orders}
    fs = fs or SudoFs()
    owned, changed = set(old), 0
    try:
        for u, c in sorted(want):
            f = root / u / f"{c}.json"
            data = json.dumps(orders[c]).encode()
            try:
                same = f.read_bytes() == data
            except OSError:
                same = False
            if same:
                owned.add((u, c))
                continue
            changed += 1
            if dry_run:
                continue
            if not root.is_dir():
                fs.mkdir(root)
            if not f.parent.is_dir():
                fs.mkdir(f.parent)
            fs.write(f, data)
            owned.add((u, c))
        for u, c in sorted(old - want):
            f = root / plain(u) / f"{plain(c)}.json"
            if f.exists():
                changed += 1
                if dry_run:
                    continue
                fs.remove(f)
            owned.discard((u, c))
    finally:
        if not dry_run and owned != old:
            saved[scope] = sorted(list(p) for p in owned)
            write_state(state, saved)
    if changed:
        log(f"collection order ({scope}): {'would change' if dry_run else 'changed'} {changed} files ({len(users)} users, {len(orders)} collections)")
    return changed
