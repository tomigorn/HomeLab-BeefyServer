import datetime
import fcntl
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import jellyfin_api  # noqa: E402
import test_popular  # noqa: E402
import test_studio_logos  # noqa: E402
import test_upcoming_popularity  # noqa: E402


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def held(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


class Lock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = jellyfin_api.LOCK_FILE
        jellyfin_api.LOCK_FILE = pathlib.Path(self.tmp.name) / "state" / "injector.lock"
        jellyfin_api.LOCK_FILE.parent.mkdir()

    def tearDown(self):
        jellyfin_api.LOCK_FILE = self.old
        self.tmp.cleanup()

    def guarded(self, api):
        """api wrapper that records whether every injector POST ran under the lock."""
        self.posts = []

        def wrapped(method, path, body=None):
            if method == "POST" and path.startswith("/Plugins/"):
                self.posts.append(held(jellyfin_api.LOCK_FILE))
            return api(method, path, body)
        return wrapped

    def test_one_holder_at_a_time_the_second_waits_then_gives_up(self):
        with jellyfin_api.injector_lock():
            self.assertTrue(held(jellyfin_api.LOCK_FILE))
            with self.assertRaises(TimeoutError):
                with jellyfin_api.injector_lock(timeout=0.3, step=0.05):
                    pass
        self.assertFalse(held(jellyfin_api.LOCK_FILE))
        with jellyfin_api.injector_lock(timeout=0.3):
            pass

    def test_tracker_link_and_its_users_post_under_the_lock(self):
        tl = load("tracker_link", "tracker-link.py")
        config = {"CustomJavaScripts": []}
        api = self.guarded(lambda m, p, b=None: [{"Name": "JavaScript Injector", "Id": "p1"}] if p == "/Plugins" else config)
        self.assertEqual(tl.main([], api=api, source="a()", out=io.StringIO()), 0)
        self.assertEqual(self.posts, [True])

    def test_upcoming_popularity_posts_under_the_lock(self):
        up = test_upcoming_popularity.up
        jf = test_upcoming_popularity.FakeJellyfin()
        t = test_upcoming_popularity.Main()
        t.setUp()
        self.assertEqual(up.main([], api=self.guarded(jf.api), arr=t.arr, seerr=t.seerr, today=datetime.date(2026, 10, 29), out=io.StringIO()), 0)
        self.assertEqual(self.posts, [True])

    def test_studio_logos_posts_under_the_lock_and_uploads_outside_it(self):
        sl = test_studio_logos.sl
        jf = test_studio_logos.FakeJellyfin([test_studio_logos.movie("862", ["Pixar"])], [test_studio_logos.studio("Pixar")])
        uploads = []

        def upload(sid, data):
            uploads.append(held(jellyfin_api.LOCK_FILE))
        seerr = lambda ref: {"productionCompanies": [{"id": 3, "name": "Pixar", "logoPath": "/pixar.png"}]}
        code = sl.main([], api=self.guarded(jf.api), seerr=seerr, fetch=lambda url: b"PNG", upload=upload, cache_file=pathlib.Path(self.tmp.name) / "c.json", out=io.StringIO(), see_through=lambda d: True)
        self.assertEqual(code, 0)
        self.assertEqual((self.posts, uploads), ([True], [False]))

    def test_popular_posts_under_the_lock(self):
        fake = test_popular.FakeIo()
        seen = []
        put = fake.injector_put

        def guarded_put(config):
            seen.append(held(jellyfin_api.LOCK_FILE))
            put(config)
        fake.injector_put = guarded_put
        fake.injector_lock = jellyfin_api.injector_lock
        sf = pathlib.Path(self.tmp.name) / "s.json"
        code = test_popular.pop.main([], io_=fake, now=datetime.datetime(2026, 10, 21, 4, 39), state_file=sf, overrides_file=pathlib.Path("/nonexistent"), out=io.StringIO(), sleep=lambda s: None,
                                     services=[(8, "Netflix")], genres=[], allow_file=sf.with_name("allow.json"), prom_file=sf.with_name("p.prom"))
        self.assertEqual((code, seen), (0, [True]))


if __name__ == "__main__":
    unittest.main()
