import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KINDS = ("movie", "show")
QUERY = "SELECT id FROM metadata WHERE source = 'tmdb' AND lot = %s AND identifier = %s LIMIT 1"
PATH = re.compile(r"^/go/tmdb/([^/]+)/([^/]+)$")


def resolve(kind, tmdb_id, title, lookup):
    if kind not in KINDS:
        return 400, "unknown kind"
    if not re.fullmatch(r"[0-9]{1,12}", tmdb_id):
        return 400, "tmdb id must be numeric"
    met_id = lookup(kind, tmdb_id)
    if met_id:
        return 302, "/media/item/" + urllib.parse.quote(met_id, safe="")
    if title:
        return 302, f"/media/search/{kind}?query={urllib.parse.quote(title, safe='')}&source=TMDB"
    return 302, "/"


def parse(target):
    parts = urllib.parse.urlsplit(target)
    match = PATH.match(parts.path)
    if not match:
        return None
    title = urllib.parse.parse_qs(parts.query).get("title", [""])[0].strip()
    return match.group(1), match.group(2), title


def db_lookup(kind, tmdb_id):
    import psycopg

    with psycopg.connect(os.environ["RYOT_GO_DSN"], connect_timeout=5) as conn:
        row = conn.execute(QUERY, (kind, tmdb_id)).fetchone()
    return row[0] if row else None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, "ok")
        parsed = parse(self.path)
        if parsed is None:
            return self.reply(404, "not found")
        try:
            status, value = resolve(*parsed, db_lookup)
        except Exception as exc:
            self.log_error("lookup failed: %s", type(exc).__name__)
            return self.reply(502, "lookup failed")
        if status == 302:
            self.send_response(302)
            self.send_header("Location", value)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.reply(status, value)

    def reply(self, status, text):
        body = (text + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
