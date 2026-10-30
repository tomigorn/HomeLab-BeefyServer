import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import collection_order as co  # noqa: E402

U1, U2 = "6303eb08e7994c0e8fd030ae518ee5b8", "70d44a36-7988-405d-8792-444c1c716d9a"
U2X = U2.replace("-", "")
G1 = "0123456789abcdef0123456789abcdef"
C1, C2, OTHER = "1c1886a0678227369926dfe6eb30b94a", "5f16e6a1c3ec95a04f5bbb20209d7272", "deadbeefdeadbeefdeadbeefdeadbeef"


class PlainFs:
    """Same operations as co.SudoFs, without the owner change."""

    def __init__(self):
        self.ops = []

    def mkdir(self, path):
        self.ops.append(("mkdir", path))
        path.mkdir()

    def write(self, path, data):
        self.ops.append(("write", path))
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def remove(self, path):
        self.ops.append(("remove", path))
        path.unlink()


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.root = base / "Moonfin" / "collections"
        self.root.parent.mkdir()
        self.state = base / "state.json"
        self.fs = PlainFs()
        self.log = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, scope, users, orders, dry=False):
        self.fs.ops = []
        return co.sync(scope, users, orders, dry, self.log.append, root=self.root, state=self.state, fs=self.fs)

    def read(self, uid, cid):
        return json.loads((self.root / uid / f"{cid}.json").read_text())

    def test_every_user_gets_every_list_in_order(self):
        self.assertEqual(self.run_sync("trending", [U1, U2], {C1: ["a", "b", "c"], C2: ["x"]}), 4)
        for uid in (U1, U2X):
            self.assertEqual(self.read(uid, C1), ["a", "b", "c"])
            self.assertEqual(self.read(uid, C2), ["x"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted([U1, U2X]))

    def test_unchanged_writes_nothing(self):
        self.run_sync("trending", [U1], {C1: ["a", "b"]})
        self.assertEqual(self.run_sync("trending", [U1], {C1: ["a", "b"]}), 0)
        self.assertEqual(self.fs.ops, [])
        self.assertEqual(self.run_sync("trending", [U1], {C1: ["b", "a"]}), 1)
        self.assertEqual(self.read(U1, C1), ["b", "a"])

    def test_new_user_gets_files_on_the_next_run(self):
        self.run_sync("trending", [U1], {C1: ["a"]})
        self.assertEqual(self.run_sync("trending", [U1, U2], {C1: ["a"]}), 1)
        self.assertEqual(self.read(U2X, C1), ["a"])

    def test_gone_list_removed_other_files_and_scopes_kept(self):
        self.run_sync("trending", [U1], {C1: ["a"], C2: ["b"]})
        self.run_sync("gems", [U1], {G1: ["g"]})
        (self.root / U1 / f"{OTHER}.json").write_text('["own"]')
        self.assertEqual(self.run_sync("trending", [U1], {C1: ["a"]}), 1)
        self.assertFalse((self.root / U1 / f"{C2}.json").exists())
        self.assertEqual(self.read(U1, OTHER), ["own"])
        self.assertEqual(self.read(U1, G1), ["g"])
        self.assertEqual(self.read(U1, C1), ["a"])

    def test_gone_user_loses_only_our_files(self):
        self.run_sync("trending", [U1, U2], {C1: ["a"]})
        (self.root / U2X / f"{OTHER}.json").write_text('["own"]')
        self.run_sync("trending", [U1], {C1: ["a"]})
        self.assertFalse((self.root / U2X / f"{C1}.json").exists())
        self.assertEqual(self.read(U2X, OTHER), ["own"])

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.run_sync("trending", [U1], {C1: ["a"]}, dry=True), 1)
        self.assertEqual(self.fs.ops, [])
        self.assertFalse(self.root.exists())
        self.assertFalse(self.state.exists())

    def test_ids_must_be_plain_hex(self):
        with self.assertRaises(ValueError):
            self.run_sync("trending", ["../x"], {C1: ["a"]})
        with self.assertRaises(ValueError):
            self.run_sync("trending", [U1], {"../../x": ["a"]})

    def test_sudo_fs_commands_keep_the_plugin_owner_and_modes(self):
        calls = []
        fs = co.SudoFs(uid=65108, gid=65108, run=lambda cmd, **kw: calls.append(cmd))
        d = pathlib.Path("/m/collections/u")
        fs.mkdir(d)
        fs.write(d / "c.json", b"[]")
        fs.remove(d / "c.json")
        self.assertEqual(calls[0], ["sudo", "-n", "install", "-d", "-o", "65108", "-g", "65108", "-m", "2775", "/m/collections/u"])
        self.assertEqual(calls[1][:9], ["sudo", "-n", "install", "-o", "65108", "-g", "65108", "-m", "0664"])
        self.assertEqual(calls[1][-1], "/m/collections/u/.c.json.tmp")
        self.assertEqual(calls[2], ["sudo", "-n", "mv", "-f", "-T", "/m/collections/u/.c.json.tmp", "/m/collections/u/c.json"])
        self.assertEqual(calls[3], ["sudo", "-n", "rm", "-f", "--", "/m/collections/u/c.json"])


if __name__ == "__main__":
    unittest.main()
