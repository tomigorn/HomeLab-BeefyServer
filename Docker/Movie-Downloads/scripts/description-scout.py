#!/usr/bin/env python3
"""Description scout (README "Description scout"): the audio-<language> filter sees only release names, so a release that says the language only in its description is never grabbed. For every monitored Radarr movie without a file that carries a scouted audio tag, this searches the torrent indexers through Prowlarr, reads the descriptions of releases that are surely that movie (TPB via apibay, 1337x via its page, both through FlareSolverr in the VPN), and hands a match with seeders to Radarr (release push) with the language word appended to its name. Radarr then decides as usual. Run by systemd/description-scout.timer.
Usage: scripts/description-scout.py [--dry-run] [--tmdb ID]
       scripts/description-scout.py --resolve-flaresolverr FILE   (root, writes FlareSolverr's URL; the unit runs it first)"""
import argparse
import html
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
STATE_DIR = pathlib.Path(os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local" / "state")) / "movie-downloads"
MARKER = "DESC"
MAX_ERRORS = 3
SUBS = re.compile(r"sub(title)?s?\b|subbed|untertitel|\but\b|\bst\b", re.I)
AUDIO = re.compile(r"audio|language|sprache|\bton\b|dub|spoken|langue|lingua", re.I)
TRACKERS = "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce&tr=udp%3A%2F%2Fopen.stealth.si%3A80%2Fannounce&tr=udp%3A%2F%2Ftracker.torrent.eu.org%3A451%2Fannounce"


class ApiError(Exception):
    pass


def load_languages(tsv, codes):
    langs = []
    for line in pathlib.Path(tsv).read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        code, name, audio, _ = line.split("\t")
        if code not in codes or audio == "-":
            continue
        rx = re.compile(audio, re.I)
        token = name.replace(" ", "").capitalize()
        if rx.search(token):
            langs.append({"code": code, "name": name, "regex": rx, "tag": "audio-" + name.lower().replace(" ", "-"), "token": token})
    return langs


def unescape(s):
    for _ in range(4):
        u = html.unescape(s)
        if u == s:
            break
        s = u
    return s


def html_to_text(s):
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", s)
    return unescape(re.sub(r"<[^>]+>", " ", s))


def description_matches(text, rx):
    for line in unescape(text).splitlines():
        for m in rx.finditer(line):
            before = line[:m.start()]
            subs = max((x.end() for x in SUBS.finditer(before)), default=-1)
            audio = max((x.end() for x in AUDIO.finditer(before)), default=-1)
            if subs <= audio:
                return True
    return False


def tpb_from_apibay(page):
    m = re.search(r"<pre[^>]*>(.*)</pre>", page, re.S)
    raw = m.group(1).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&") if m else page
    data = json.loads(raw)
    if not isinstance(data, dict) or "descr" not in data:
        raise ApiError("apibay: no description")
    imdb = data.get("imdb") or None
    return {"descr": unescape(data["descr"]), "imdb": imdb if imdb and re.fullmatch(r"tt\d+", imdb) else None,
            "info_hash": (data.get("info_hash") or "").upper() or None, "name": data.get("name")}


def x1337_from_page(page):
    m = re.search(r'id="description"[^>]*>(.*?)(<!--\s*description\s*-->|id="files")', page, re.S)
    if not m:
        raise ApiError("1337x: no description block")
    block = m.group(1)
    imdb = re.search(r"imdb\.com/title/(tt\d+)", block)
    magnet = re.search(r'href="(magnet:\?[^"]+)"', page)
    return {"descr": html_to_text(block), "imdb": imdb.group(1) if imdb else None, "magnet": html.unescape(magnet.group(1)) if magnet else None}


def description_source(result):
    url = result.get("infoUrl") or ""
    m = re.match(r"https?://(?:[^/]*thepiratebay[^/]*|knaben\.[a-z]+/thepiratebay)/description\.php\?id=(\d+)$", url)
    if m:
        return ("tpb", m.group(1))
    if re.match(r"https?://(www\.)?1337x\.[a-z]+/torrent/\d+/", url):
        return ("1337x", url)
    return None


def imdb_num(v):
    if not v:
        return 0
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else 0


def mapping_ok(movie, parsed, imdb):
    if imdb and imdb_num(imdb) != imdb_num(movie.get("imdbId")):
        return False, f"imdb {imdb} is not {movie.get('imdbId')}"
    got = (parsed or {}).get("movie") or {}
    if got.get("id") != movie["id"]:
        return False, "Radarr parses it as another movie" if got else "Radarr cannot map it to a movie"
    year = ((parsed or {}).get("parsedMovieInfo") or {}).get("year") or 0
    if year not in {movie.get("year"), movie.get("secondaryYear")} - {None, 0}:
        return False, f"year {year or 'missing'} is not {movie.get('year')}"
    return True, "same movie"


def push_title(result, lang):
    return f"{result['title'].rstrip('. ')}.{lang['token']}.{MARKER}"


def push_payload(result, movie, lang, magnet, indexer):
    return {"title": push_title(result, lang), "guid": result.get("guid") or magnet, "magnetUrl": magnet, "downloadUrl": magnet,
            "infoUrl": result.get("infoUrl"), "protocol": "torrent", "indexerId": indexer["id"], "indexer": indexer["name"],
            "size": result.get("size") or 0, "seeders": result.get("seeders") or 0, "leechers": result.get("leechers") or 0,
            "publishDate": result.get("publishDate"), "tmdbId": movie.get("tmdbId") or 0, "imdbId": imdb_num(movie.get("imdbId"))}


def release_key(result):
    m = re.search(r"btih:([0-9a-fA-F]{40}|[A-Z2-7]{32})", result.get("guid") or result.get("magnetUrl") or "")
    return "btih:" + m.group(1).upper() if m else "url:" + (result.get("infoUrl") or result.get("guid") or "")


class Scout:
    def __init__(self, radarr, prowlarr, fetch, state_path, log, dry_run=False, max_push=2, max_fetch=15):
        self.radarr, self.prowlarr, self.fetch, self.log = radarr, prowlarr, fetch, log
        self.state_path = pathlib.Path(state_path)
        self.dry_run, self.max_push, self.max_fetch = dry_run, max_push, max_fetch
        self.pushes = self.fetches = 0
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def save(self):
        if self.dry_run:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True))
        tmp.replace(self.state_path)

    def run(self, langs, tmdb=None):
        try:
            tags = {t["label"]: t["id"] for t in self.radarr("GET", "/tag")}
            movies = self.radarr("GET", "/movie")
            rindexers = {i["name"]: i for i in self.radarr("GET", "/indexer") if i.get("protocol") == "torrent"}
            pindexers = [i for i in self.prowlarr("GET", "/indexer") if i.get("protocol") == "torrent" and i.get("enable")]
        except ApiError as e:
            self.log(f"API error, run skipped: {e}")
            return 1
        rc = 0
        for lang in langs:
            tid = tags.get(lang["tag"])
            wanted = [m for m in movies if tid is not None and tid in m.get("tags", []) and m.get("monitored") and not m.get("hasFile")
                      and (tmdb is None or m.get("tmdbId") == tmdb)]
            for movie in wanted:
                try:
                    self.movie(movie, lang, pindexers, rindexers)
                except ApiError as e:
                    self.log(f"{movie['title']} ({movie.get('year')}): API error, skipped: {e}")
                    rc = 1
        return rc

    def search(self, movie, pindexers):
        ids = [i["id"] for i in pindexers]
        titles = [movie["title"]] + ([movie["originalTitle"]] if movie.get("originalTitle") and movie["originalTitle"] != movie["title"] else [])
        queries = [(f"{t} {movie['year']}", ids) for t in titles]
        by_imdb = [i["id"] for i in pindexers if "imdbId" in ((i.get("capabilities") or {}).get("movieSearchParams") or [])]
        if by_imdb and movie.get("imdbId"):
            queries.append((f"{{ImdbId:{movie['imdbId']}}}", by_imdb))
        seen, results = set(), []
        for q, ids in queries:
            for r in self.prowlarr("GET", "/search", {"query": q, "type": "movie", "categories": [2000], "indexerIds": ids}) or []:
                k = release_key(r)
                if k not in seen:
                    seen.add(k)
                    results.append(r)
        return results

    def movie(self, movie, lang, pindexers, rindexers):
        name = f"{movie['title']} ({movie.get('year')})"
        results = self.search(movie, pindexers)
        self.log(f"{name}: {len(results)} torrent results")
        for r in results:
            if r.get("protocol") != "torrent":
                continue
            if lang["regex"].search(r["title"]):
                continue
            key = release_key(r)
            st = self.state.get(key, {})
            if st.get("pushed") or st.get("verdict") in ("no", "failed"):
                continue
            ok, why = mapping_ok(movie, self.radarr("GET", "/parse", {"title": r["title"]}), r.get("imdbId") or None)
            if not ok:
                continue
            src = description_source(r)
            if not src:
                continue
            if st.get("verdict") != "match":
                if self.fetches >= self.max_fetch:
                    self.log(f"{name}: '{r['title']}': description fetch cap ({self.max_fetch}) reached, next run")
                    continue
                self.fetches += 1
                try:
                    if src[0] == "tpb":
                        info = tpb_from_apibay(self.fetch(f"https://apibay.org/t.php?id={src[1]}"))
                        h = info["info_hash"]
                        info["magnet"] = r["guid"] if (r.get("guid") or "").startswith("magnet:") else (
                            f"magnet:?xt=urn:btih:{h}&dn={urllib.parse.quote(info['name'] or r['title'])}{TRACKERS}" if h else None)
                    else:
                        info = x1337_from_page(self.fetch(src[1]))
                except (ApiError, ValueError, KeyError) as e:
                    errors = st.get("errors", 0) + 1
                    self.state[key] = dict(st, errors=errors, title=r["title"], **({"verdict": "failed"} if errors >= MAX_ERRORS else {}))
                    self.save()
                    self.log(f"{name}: '{r['title']}' ({r.get('indexer')}): description fetch failed ({errors}/{MAX_ERRORS}): {e}")
                    continue
                if info.get("imdb") and imdb_num(info["imdb"]) != imdb_num(movie.get("imdbId")):
                    self.state[key] = {"verdict": "no", "title": r["title"], "why": f"description imdb {info['imdb']}"}
                    self.save()
                    self.log(f"{name}: '{r['title']}': description names {info['imdb']}, another movie, skipped")
                    continue
                if not description_matches(info["descr"], lang["regex"]):
                    self.state[key] = {"verdict": "no", "title": r["title"]}
                    self.save()
                    self.log(f"{name}: '{r['title']}' ({r.get('indexer')}): no {lang['name']} in the description")
                    continue
                st = {"verdict": "match", "title": r["title"], "magnet": info.get("magnet")}
                self.state[key] = st
                self.save()
            self.log(f"{name}: '{r['title']}' ({r.get('indexer')}, {r.get('infoUrl')}): {lang['name']} in the description")
            if (r.get("seeders") or 0) < 1:
                self.log(f"{name}: '{r['title']}': seeders {r.get('seeders') or 0}, not pushed (dead torrent, checked again next run)")
                continue
            if not st.get("magnet"):
                self.log(f"{name}: '{r['title']}': no magnet link, not pushed")
                continue
            indexer = rindexers.get(f"{r.get('indexer')} (Prowlarr)")
            if not indexer:
                self.log(f"{name}: '{r['title']}': indexer {r.get('indexer')} not in Radarr, not pushed")
                continue
            payload = push_payload(r, movie, lang, st["magnet"], indexer)
            ok, why = mapping_ok(movie, self.radarr("GET", "/parse", {"title": payload["title"]}), r.get("imdbId") or None)
            if not ok:
                self.log(f"{name}: '{payload['title']}': {why} after renaming, not pushed")
                continue
            if self.pushes >= self.max_push:
                self.log(f"{name}: '{r['title']}': push cap ({self.max_push}) reached, next run")
                continue
            self.pushes += 1
            if self.dry_run:
                self.log(f"{name}: dry run, would push {json.dumps(payload)}")
                continue
            try:
                answer = self.radarr("POST", "/release/push", body=payload)
            except ApiError as e:
                errors = st.get("errors", 0) + 1
                self.state[key] = dict(st, errors=errors, **({"verdict": "failed"} if errors >= MAX_ERRORS else {}))
                self.save()
                self.log(f"{name}: '{payload['title']}': push failed ({errors}/{MAX_ERRORS}): {e}")
                continue
            d = (answer or [{}])[0]
            self.state[key] = dict(st, pushed=time.strftime("%Y-%m-%dT%H:%M:%S"), approved=bool(d.get("approved")))
            self.save()
            rej = "; ".join(x.get("reason", str(x)) if isinstance(x, dict) else str(x) for x in d.get("rejections") or [])
            self.log(f"{name}: pushed '{payload['title']}' to Radarr: {'approved, Radarr grabs it' if d.get('approved') else 'rejected: ' + rej}")


def config(path):
    xml = pathlib.Path(path).read_text()
    get = lambda k: (re.search(f"<{k}>([^<]*)</{k}>", xml) or [None, ""])[1]
    return get("ApiKey"), get("Port"), get("UrlBase")


def client(base, key):
    def call(method, path, query=None, body=None):
        url = base + path + ("?" + urllib.parse.urlencode(query, doseq=True) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"X-Api-Key": key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                raw = r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ApiError(f"{method} {path}: {getattr(e, 'code', '')} {getattr(e, 'reason', e)}".strip()) from None
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            raise ApiError(f"{method} {path}: not JSON") from None
    return call


def flaresolverr(url):
    def fetch(target):
        body = json.dumps({"cmd": "request.get", "url": target, "maxTimeout": 60000}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.load(r)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise ApiError(f"FlareSolverr: {e}") from None
        sol = d.get("solution") or {}
        if d.get("status") != "ok" or sol.get("status") != 200:
            raise ApiError(f"FlareSolverr: {d.get('status')} {d.get('message')} HTTP {sol.get('status')}")
        return sol.get("response") or ""
    return fetch


def resolve_flaresolverr(out):
    ip = subprocess.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", "movie-gluetun"],
                        capture_output=True, text=True, check=True).stdout.split()
    if not ip:
        sys.exit("movie-gluetun has no IP")
    pathlib.Path(out).write_text(f"http://{ip[0]}:8191/v1\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch and match, never push, state unchanged")
    ap.add_argument("--tmdb", type=int, help="only this movie")
    ap.add_argument("--resolve-flaresolverr", metavar="FILE")
    a = ap.parse_args()
    if a.resolve_flaresolverr:
        return resolve_flaresolverr(a.resolve_flaresolverr)
    logfile = pathlib.Path(os.environ.get("DS_LOG", STATE_DIR / "description-scout.log"))
    logfile.parent.mkdir(parents=True, exist_ok=True)

    def log(msg):
        msg = msg.replace("\n", " ")
        print(msg, flush=True)
        with logfile.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {'[dry run] ' if a.dry_run else ''}{msg}\n")

    fs = os.environ.get("DS_FLARESOLVERR") or (pathlib.Path(os.environ.get("DS_FLARESOLVERR_FILE", "/run/description-scout/flaresolverr")).read_text().strip()
                                              if pathlib.Path(os.environ.get("DS_FLARESOLVERR_FILE", "/run/description-scout/flaresolverr")).exists() else "")
    if not fs:
        log("FlareSolverr URL unknown (DS_FLARESOLVERR or the unit's resolve step), run skipped")
        return 1
    rkey, rport, rbase = config(os.environ.get("DS_RADARR_CONFIG", "/srv/appdata/radarr/config.xml"))
    pkey, _, _ = config(os.environ.get("DS_PROWLARR_CONFIG", "/srv/appdata/prowlarr/config.xml"))
    langs = load_languages(HERE / "languages.tsv", os.environ.get("DS_LANGUAGES", "gsw").split(","))
    scout = Scout(client(f"http://localhost:{rport}{rbase}/api/v3", rkey), client(os.environ.get("DS_PROWLARR_URL", "http://127.0.0.1:9696") + "/api/v1", pkey),
                  flaresolverr(fs), os.environ.get("DS_STATE", STATE_DIR / "description-scout.json"), log, dry_run=a.dry_run,
                  max_push=int(os.environ.get("DS_MAX_PUSH", 2)), max_fetch=int(os.environ.get("DS_MAX_FETCH", 15)))
    return scout.run(langs, tmdb=a.tmdb)


if __name__ == "__main__":
    sys.exit(main())
