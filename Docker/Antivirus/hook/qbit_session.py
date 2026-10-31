"""qBittorrent Web API for the hooks: localhost has no auth bypass, so a 403 logs in once (user admin, password from QBIT_PASS_FILE) and retries."""
import http.cookiejar
import os
import urllib.error
import urllib.parse
import urllib.request


class LoginRefused(OSError):
    pass


class Qbit:
    def __init__(self, base, user="admin", pass_file=None):
        self.base = base
        self.user = user
        self.pass_file = pass_file or os.environ.get("QBIT_PASS_FILE", "/run/secrets/qbit-webui.password")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def login(self):
        with open(self.pass_file) as f:
            form = urllib.parse.urlencode({"username": self.user, "password": f.read().strip()}).encode()
        try:
            body = self.opener.open(urllib.request.Request(self.base + "/api/v2/auth/login", form), timeout=10).read()
        except urllib.error.HTTPError:
            body = b"Fails."
        if body.strip() == b"Fails." or not any("SID" in c.name for c in self.jar):
            raise LoginRefused("login refused")

    def post(self, path, data):
        def call():
            req = urllib.request.Request(self.base + "/api/v2/" + path, data=urllib.parse.urlencode(data).encode())
            return self.opener.open(req, timeout=10).read()
        try:
            return call()
        except urllib.error.HTTPError as e:
            if e.code != 403:
                raise
        self.login()
        return call()
