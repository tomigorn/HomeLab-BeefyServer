"""Moonfin collection detail order (Moonbase 2.5 `GET /Moonfin/Collections/{id}/Order`): one JSON array of item ids per user and collection in the plugin's per-user store.
A non-empty list replaces the app's release-year order; Moonfin 2.6/2.7 drop members missing from it, so every list holds all members.
Each scope (trending, gems) owns the files it wrote (state file); files of other collections are never touched. Writes as the plugin owner through sudo."""
import json
import os
import pathlib
import re
import subprocess
import tempfile

ROOT = pathlib.Path("/srv/appdata/jellyfin/data/plugins/configurations/Moonfin/collections")
STATE = pathlib.Path.home() / ".local" / "state" / "jellyfin-collection-order.json"
HEX = re.compile(r"^[0-9a-f]{32}$")


class SudoFs:
    """File operations as the plugin owner (dirs 2775, files 0664 like the plugin's own); the write lands by rename."""

    def __init__(self, uid=None, gid=None, run=subprocess.run):
        if uid is None or gid is None:
            st = ROOT.parent.stat()
            uid, gid = st.st_uid, st.st_gid
        self.own = ["-o", str(uid), "-g", str(gid)]
        self.run = run

    def mkdir(self, path):
        self.run(["sudo", "-n", "install", "-d", *self.own, "-m", "2775", str(path)], check=True)

    def write(self, path, data):
        tmp = path.with_name(f".{path.name}.tmp")
        with tempfile.NamedTemporaryFile(dir=pathlib.Path.home() / ".cache") as src:
            src.write(data)
            src.flush()
            self.run(["sudo", "-n", "install", *self.own, "-m", "0664", src.name, str(tmp)], check=True)
        self.run(["sudo", "-n", "mv", "-f", "-T", str(tmp), str(path)], check=True)

    def remove(self, path):
        self.run(["sudo", "-n", "rm", "-f", "--", str(path)], check=True)


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


def sync(scope, users, orders, dry_run, log, root=ROOT, state=STATE, fs=None):
    """Writes {collection id: [item ids]} for every user id; removes the files this scope wrote before for collections or users gone. Returns the number of files written or removed."""
    users = sorted({plain(u) for u in users})
    orders = {plain(c): [str(i) for i in ids] for c, ids in orders.items()}
    saved = read_state(state)
    old = {tuple(p) for p in saved.get(scope) or []}
    want = {(u, c) for u in users for c in orders}
    fs = fs or SudoFs()
    changed = 0
    for u, c in sorted(want):
        f = root / u / f"{c}.json"
        data = json.dumps(orders[c]).encode()
        try:
            same = f.read_bytes() == data
        except OSError:
            same = False
        if same:
            continue
        changed += 1
        if dry_run:
            continue
        if not root.is_dir():
            fs.mkdir(root)
        if not f.parent.is_dir():
            fs.mkdir(f.parent)
        fs.write(f, data)
    for u, c in sorted(old - want):
        f = root / plain(u) / f"{plain(c)}.json"
        if not f.exists():
            continue
        changed += 1
        if not dry_run:
            fs.remove(f)
    if not dry_run and old != want:
        saved[scope] = sorted(list(p) for p in want)
        state.parent.mkdir(parents=True, exist_ok=True)
        tmp = state.with_name(f".{state.name}.tmp")
        tmp.write_text(json.dumps(saved, indent=1, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, state)
    if changed:
        log(f"collection order ({scope}): {'would change' if dry_run else 'changed'} {changed} files ({len(users)} users, {len(orders)} collections)")
    return changed
