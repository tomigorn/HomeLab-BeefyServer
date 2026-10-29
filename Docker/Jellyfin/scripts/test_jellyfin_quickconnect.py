import contextlib
import io
import unittest
import urllib.error

import jellyfin_quickconnect as qc

TOKEN = "f" * 32


class Fake:
    def __init__(self, authorize_error=None):
        self.calls, self.admin_calls, self.authorize_error = [], [], authorize_error

    def http(self, method, path, body=None, auth=""):
        self.calls.append((method, path, body, auth))
        if path == "/QuickConnect/Initiate":
            return {"Secret": "qsecret", "Code": "123456"}
        if path == "/Users/AuthenticateWithQuickConnect":
            return {"AccessToken": TOKEN, "User": {"Id": "u1"}}
        if path == "/Sessions/Logout":
            return None
        raise AssertionError((method, path))

    def admin(self, method, path, body=None):
        self.admin_calls.append((method, path, body))
        if self.authorize_error:
            raise self.authorize_error
        return True


class TokenTest(unittest.TestCase):
    def test_token_flow(self):
        f = Fake()
        tok = qc.token("Android TV", "Living room", "trending-e2e", "1.0", "u1", http=f.http, admin=f.admin)
        self.assertEqual(tok, TOKEN)
        self.assertEqual([c[:2] for c in f.calls], [("POST", "/QuickConnect/Initiate"), ("POST", "/Users/AuthenticateWithQuickConnect")])
        self.assertEqual(f.calls[1][2], {"Secret": "qsecret"})
        for c in f.calls:
            self.assertEqual(c[3], 'MediaBrowser Client="Android TV", Device="Living room", DeviceId="trending-e2e", Version="1.0"')
        self.assertEqual(f.admin_calls, [("POST", "/QuickConnect/Authorize?code=123456&userId=u1", None)])

    def test_header_values_are_checked(self):
        f = Fake()
        for bad in ('x"', "a,b", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                qc.token(bad, "d", "trending-e2e", "1", "u1", http=f.http, admin=f.admin)
        with self.assertRaises(ValueError):
            qc.token("c", "d", "trending-e2e", "1", "u1&userId=x", http=f.http, admin=f.admin)
        self.assertEqual(f.calls, [])

    def test_refused_authorize_names_the_code(self):
        f = Fake(urllib.error.HTTPError("u", 403, "Forbidden", None, None))
        with self.assertRaises(qc.AuthorizeRefused) as ctx:
            qc.token("c", "d", "trending-e2e", "1", "u1", http=f.http, admin=f.admin)
        self.assertEqual(ctx.exception.code, "123456")
        self.assertNotIn("qsecret", str(ctx.exception))

    def test_token_never_logged(self):
        f = Fake()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            tok = qc.token("c", "d", "trending-e2e", "1", "u1", http=f.http, admin=f.admin)
            qc.logout(tok, "trending-e2e", http=f.http)
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertNotIn("qsecret", out.getvalue())
        self.assertNotIn(TOKEN, repr(qc.AuthorizeRefused("123456")))


class LogoutTest(unittest.TestCase):
    def test_logout_never_deletes_devices(self):
        f = Fake()
        qc.logout(TOKEN, "trending-e2e", http=f.http)
        self.assertEqual([c[:2] for c in f.calls], [("POST", "/Sessions/Logout")])
        self.assertIn(f'Token="{TOKEN}"', f.calls[0][3])
        self.assertFalse([c for c in f.calls if c[0] == "DELETE" or c[1].startswith("/Devices")])


if __name__ == "__main__":
    unittest.main()
