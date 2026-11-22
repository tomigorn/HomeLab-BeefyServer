#!/usr/bin/env python3
"""Delete early runner (README "Delete early", Seerr README "Delete early"): carries out what Seerr's download-now backend queued in /srv/appdata/seerr-delete-early/*.req.
delete: refused for the test film / keep-test, a title without files, the 11th delete of a user in 7 days; asks for an OK when the title is Keep forever and the deleter did not request it (the requester, none: the owner) or someone else is mid-watch (started in the last 30 days, not finished; the viewer, no Seerr account: the owner); else deletes the way retention does (unmonitor + tag retention-deleted, files through Radarr/Sonarr into the 7-day recycle bin, phone versions moved the same way, Left the server list) under the media lock.
answer: the asked person's OK or no; all OK = delete, one no = denied; no answer in 7 days = expired (denied). restore: files back from the bin, monitored again, Radarr/Sonarr rescan, Jellyfin told.
Notices: Jellyfin pop-up on the person's live sessions, Telegram for the owner (and anyone in secrets/delete-early-telegram.json). State ~/.local/state/movie-downloads/delete-early.json (read by the backend), audit delete-early-audit.jsonl.
Usage: scripts/delete-early.py [--list] | -h"""
import argparse
import contextlib
import dataclasses
import datetime
import errno
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import types
import urllib.parse
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("retention", HERE / "retention.py")
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)
rr = rt.rr

TZ = ZoneInfo("Europe/Zurich")
OWNER = 1
ASK_DAYS = 7
BIN_DAYS = 7
WATCH_DAYS = 30
WEEK_MAX = 10
COUNTED = ("pending", "approved", "deleted", "restored", "failed")
LINK = "https://requests.holy-grail.ch"
MONTHS = {"en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
          "de": ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."]}
TEXT = {
    "en": {"head": "🗑 Delete early", "season": " (season {n})", "keep": "it is marked ♾️ Keep forever", "watching": "you have started watching it",
           "ask": "{who} wants to delete {title}{scope}: {reason}. Approve or deny on its page until {until}: {link}",
           "deleted": "{who} deleted {title}{scope}. It stays in the recycle bin until {until}; undo on its page: {link}",
           "approved": "{title}{scope} is deleted ({who} agreed). In the recycle bin until {until}: {link}",
           "denied": "{who} said no: {title}{scope} stays.", "expired": "No answer within 7 days: {title}{scope} stays."},
    "de": {"head": "🗑 Früher löschen", "season": " (Staffel {n})", "keep": "es ist als ♾️ Für immer behalten markiert", "watching": "du hast angefangen, es zu schauen",
           "ask": "{who} möchte {title}{scope} löschen: {reason}. Auf der Titelseite bis {until} zustimmen oder ablehnen: {link}",
           "deleted": "{who} hat {title}{scope} gelöscht. Es bleibt bis {until} im Papierkorb; rückgängig auf der Titelseite: {link}",
           "approved": "{title}{scope} ist gelöscht ({who} war einverstanden). Im Papierkorb bis {until}: {link}",
           "denied": "{who} hat abgelehnt: {title}{scope} bleibt.", "expired": "Keine Antwort innert 7 Tagen: {title}{scope} bleibt."},
}


class Refused(Exception):
    pass


@dataclasses.dataclass
class Env:
    radarr: object
    sonarr: object
    jf: object
    seerr: object
    jobs: pathlib.Path
    state: pathlib.Path
    audit: pathlib.Path
    gone: pathlib.Path
    retention: pathlib.Path
    keep: pathlib.Path
    identity: pathlib.Path
    pool: str
    now: object
    notify: object
    lock: object


def when(s):
    try:
        d = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def norm(uid):
    return str(uid or "").replace("-", "").lower()


def day(iso, lang):
    d = when(iso)
    if not d:
        return ""
    d = d.astimezone(TZ)
    return f"{d.day}. {MONTHS['de'][d.month - 1]} {d.year}" if lang == "de" else f"{d.day} {MONTHS['en'][d.month - 1]} {d.year}"


def message(event, info, lang):
    t = TEXT[lang]
    scope = t["season"].format(n=info["scope"]) if isinstance(info.get("scope"), int) else ""
    text = t[event].format(who=info.get("who") or "", title=info.get("title") or "", scope=scope, reason=t.get(info.get("reason") or "", ""), until=day(info.get("until"), lang),
                           link=info.get("link") or "")
    return t["head"], text


# ---- state ----
def load(env):
    try:
        data = json.loads(env.state.read_text())
    except FileNotFoundError:
        return {"items": {}}
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        raise ValueError("delete-early state has the wrong shape")
    return data


def save(env, data):
    rt.write_atomic(env.state, data)


def audit(env, event, it, **extra):
    rec = {"at": env.now().isoformat(timespec="seconds"), "event": event, "id": it.get("id"), "key": rr.key(it["kind"], it["tmdb"]) if it.get("kind") else None,
           "season": it.get("season"), "title": it.get("title"), "by": it.get("by"), "byName": it.get("byName"), "account": it.get("account"), **extra}
    with open(env.audit, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"delete-early: {event} {rec['key']} {'whole' if it.get('season') is None else 'season ' + str(it.get('season'))} {it.get('title')!r} by user {it.get('by')}"
          + "".join(f" {k}={v}" for k, v in extra.items()), flush=True)


def link(it):
    return f"{LINK}/{it['kind']}/{it['tmdb']}"


def info(it, who, until=None, reason=None):
    return {"who": who, "title": it.get("title"), "scope": it.get("season"), "link": link(it), "until": until, "reason": reason}


def touch(env, it, state):
    it["state"] = state
    it["updated"] = env.now().isoformat(timespec="seconds")


# ---- who ----
def people(env):
    """(Jellyfin user id -> Seerr user id, Seerr user id -> name): Seerr's Jellyfin link, else the same Holy Grail subject (Jellyfin identity map)."""
    try:
        users = (env.seerr("/user?take=1000") or {}).get("results") or []
    except Exception as e:  # noqa: BLE001
        print(f"delete-early: Seerr users unreadable ({type(e).__name__})", flush=True)
        users = []
    jf_to, names = {}, {}
    for u in users:
        if isinstance(u, dict) and isinstance(u.get("id"), int):
            names[u["id"]] = u.get("displayName") or u.get("username") or f"user {u['id']}"
            if u.get("jellyfinUserId"):
                jf_to[norm(u["jellyfinUserId"])] = u["id"]
    try:
        ident = json.loads(env.identity.read_text())
        by_sub = {sub: int(sid) for sid, sub in (ident.get("seerr") or {}).items() if str(sid).isdigit()}
        for jid, sub in (ident.get("jellyfin") or {}).items():
            if norm(jid) not in jf_to and sub in by_sub:
                jf_to[norm(jid)] = by_sub[sub]
    except (OSError, ValueError, AttributeError):
        pass
    return jf_to, names


def mid_movie(ud, since):
    last = when((ud or {}).get("LastPlayedDate"))
    return bool((ud or {}).get("PlaybackPositionTicks")) and not (ud or {}).get("Played") and bool(last) and last >= since


def watchers(env, kind, item, season, now):
    """Jellyfin user ids mid-watch: started in the last 30 days and not finished (a film: a resume point on any version; a series or season: an episode played since, not every episode played)."""
    folder = "/" + (rt.rel_of(item.get("path"), "/data/") or "")
    since, out = now - datetime.timedelta(days=WATCH_DAYS), set()
    under = lambda p: isinstance(p, str) and (p == folder or p.startswith(folder + "/"))  # noqa: E731
    users = [norm(u.get("Id")) for u in env.jf("/Users") or [] if isinstance(u, dict) and u.get("Id")]
    if kind == "movie":
        ids = []
        for m in (env.jf("/Items?Recursive=true&IncludeItemTypes=Movie&Fields=Path,MediaSources&EnableImages=false") or {}).get("Items") or []:
            if under(m.get("Path")):
                ids += [m["Id"]] + [s["Id"] for s in m.get("MediaSources") or [] if s.get("Id")]
        ids = list(dict.fromkeys(ids))
        for uid in users:
            if any(mid_movie(env.jf(f"/Users/{uid}/Items/{i}").get("UserData"), since) for i in ids):
                out.add(uid)
        return out
    sid = next((s["Id"] for s in (env.jf("/Items?Recursive=true&IncludeItemTypes=Series&Fields=Path&EnableImages=false") or {}).get("Items") or [] if s.get("Path") == folder), None)
    if not sid:
        return out
    for uid in users:
        eps = (env.jf(f"/Users/{uid}/Items?ParentId={sid}&Recursive=true&IncludeItemTypes=Episode&EnableImages=false&EnableUserData=true") or {}).get("Items") or []
        eps = [e for e in eps if season is None or e.get("ParentIndexNumber") == season]
        started = any((when((e.get("UserData") or {}).get("LastPlayedDate")) or since) > since for e in eps)
        if started and not all((e.get("UserData") or {}).get("Played") for e in eps):
            out.add(uid)
    return out


def kept_forever(env, it):
    if it.get("keep"):
        return True
    key = rr.key(it["kind"], it["tmdb"])
    try:
        entry = json.loads(env.retention.read_text())["titles"].get(key)
    except (OSError, ValueError, KeyError, AttributeError):
        entry = None
    overrides, _ = rt.read_overrides(env.keep)
    if isinstance(entry, dict):
        return rr.effective(entry, rt.override_for(overrides, key, entry), env.now().date())["keep"]
    return bool(overrides.get(key))


# ---- arr ----
def arr_item(env, it):
    call = env.radarr if it["kind"] == "movie" else env.sonarr
    found = call(f"/movie?tmdbId={it['tmdb']}") if it["kind"] == "movie" else (call(f"/series?tvdbId={it['tvdb']}") if it.get("tvdb") else [])
    found = [x for x in found or [] if isinstance(x, dict)]
    if len(found) != 1:
        raise Refused("library")
    x = found[0]
    labels = {t["id"]: t["label"] for t in call("/tag")}
    if rr.protected(rr.key(it["kind"], it["tmdb"]), [labels.get(i, "") for i in x.get("tags") or []]):
        raise Refused("protected")
    return call, x


def host(env, path):
    rel = rt.rel_of(path, "/data/")
    if not rel or not rel.startswith("media/"):
        raise Refused("path")
    return str(pathlib.Path(env.pool, rel))


def season_of(path):
    m = rt.SEASON.search(path)
    return int(m.group(1)) if m else None


def extras(videos):
    """Subtitles and other side files of the videos (`<video stem>.de.srt`): the arrs move them into the bin with the video, so a restore brings them back too."""
    out = []
    for v in videos:
        d, stem = os.path.dirname(v), os.path.splitext(os.path.basename(v))[0]
        with contextlib.suppress(OSError):
            out += [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.startswith(stem + ".") and os.path.join(d, n) != v and not n.startswith(".")]
    return out


def execute(env, it):
    """Deletes the files into the recycle bin; returns the host paths that went."""
    call, x = arr_item(env, it)
    kind, season = it["kind"], it.get("season")
    phones = [str(p) for p in rt.phone_files(types.SimpleNamespace(rel=rt.rel_of(x.get("path"), "/data/"), kind=kind), env.pool)]
    if kind == "movie":
        f = x.get("movieFile") or {}
        if not x.get("hasFile") or not f.get("id"):
            raise Refused("library")
        files = [host(env, f["path"])]
        side = extras(files)
        tid = rt.ensure_tag(call)
        x["monitored"], x["tags"] = False, sorted(set(x.get("tags") or []) | {tid})
        call(f"/movie/{x['id']}", "PUT", x)
        call(f"/moviefile/{f['id']}", "DELETE")
    else:
        eps = [e for e in call(f"/episodefile?seriesId={x['id']}") or [] if season is None or e.get("seasonNumber") == season]
        if not eps:
            raise Refused("library")
        files = [host(env, e["path"]) for e in eps]
        side = extras(files)
        phones = [p for p in phones if season is None or season_of(p) == season]
        if season is None:
            tid = rt.ensure_tag(call)
            x["monitored"], x["tags"] = False, sorted(set(x.get("tags") or []) | {tid})
        else:
            for s in x.get("seasons") or []:
                if s.get("seasonNumber") == season:
                    s["monitored"] = False
            ids = [e["id"] for e in call(f"/episode?seriesId={x['id']}&seasonNumber={season}") or [] if e.get("id")]
            if ids:
                call("/episode/monitor", "PUT", {"episodeIds": ids, "monitored": False})
        call(f"/series/{x['id']}", "PUT", x)
        call("/episodefile/bulk", "DELETE", {"episodeFileIds": [e["id"] for e in eps]})
    files += side
    for p in phones + [p for p in side if os.path.lexists(p)]:
        rt.recycle(p, env.pool)
    if season is None:
        rt.record_gone(env.gone, rr.key(kind, it["tmdb"]), it.get("title"), it.get("year"), env.now().astimezone(TZ).date(), tvdb=it.get("tvdb"))
    return files + phones


def seerr_status(env, it, status):
    """Seerr's own media status of the whole title (POST /media/<id>/<status>): unknown = the placeholder with a Request button at once (a film; a series's seasons wait for the nightly availability sync; Seerr has no "deleted" here), available after a restore. The media row itself is never deleted (Clear Data would wipe the title)."""
    try:
        m = env.seerr(f"/{it['kind']}/{it['tmdb']}") or {}
        mid = (m.get("mediaInfo") or {}).get("id")
        if isinstance(mid, int):
            env.seerr(f"/media/{mid}/{status}", "POST", {})
            return True
    except Exception as e:  # noqa: BLE001
        print(f"delete-early: Seerr status {status} for {it.get('id')} not set ({type(e).__name__})", flush=True)
    return False


def in_bin(env, orig):
    """The newest copy of a deleted file in the bin (`name`, or `name_2` when the name was taken), else None."""
    rel = pathlib.Path(orig).relative_to(pathlib.Path(env.pool, "media"))
    d = pathlib.Path(env.pool, rt.BIN, *rel.parts[1:-1])
    stem, ext = os.path.splitext(rel.name)
    found = []
    with contextlib.suppress(OSError):
        for n in os.listdir(d):
            if n == rel.name or (n.startswith(stem + "_") and n.endswith(ext) and n[len(stem) + 1:len(n) - len(ext)].isdigit()):
                found.append(d / n)
    return max(found, key=lambda p: p.stat().st_ctime) if found else None


def move_back(src, dest):
    made = []
    d = os.path.dirname(dest)
    while not os.path.isdir(d):
        made.append(d)
        d = os.path.dirname(d)
    for d in reversed(made):
        os.mkdir(d)
        os.chmod(d, 0o2775)
    try:
        os.rename(src, dest)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        tmp = os.path.join(os.path.dirname(dest), f".{os.path.basename(dest)}.partial")
        try:
            shutil.copyfile(src, tmp)
            os.chmod(tmp, 0o664)
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
            os.rename(tmp, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        os.unlink(src)


def restore(env, it):
    back, missing = [], []
    for orig in it.get("files") or []:
        src = in_bin(env, orig)
        if not src or os.path.lexists(orig):
            missing.append(orig)
            continue
        move_back(str(src), orig)
        back.append(orig)
    if not back:
        raise Refused("not in the bin")
    call, x = arr_item(env, it)
    tag = next((t["id"] for t in call("/tag") if t.get("label") == rt.DELETED_TAG), None)
    if it["kind"] == "movie":
        x["monitored"], x["tags"] = True, [t for t in x.get("tags") or [] if t != tag]
        call(f"/movie/{x['id']}", "PUT", x)
        call("/command", "POST", {"name": "RescanMovie", "movieId": x["id"]})
    else:
        season = it.get("season")
        if season is None:
            x["monitored"], x["tags"] = True, [t for t in x.get("tags") or [] if t != tag]
        else:
            for s in x.get("seasons") or []:
                if s.get("seasonNumber") == season:
                    s["monitored"] = True
            ids = [e["id"] for e in call(f"/episode?seriesId={x['id']}&seasonNumber={season}") or [] if e.get("id")]
            if ids:
                call("/episode/monitor", "PUT", {"episodeIds": ids, "monitored": True})
        call(f"/series/{x['id']}", "PUT", x)
        call("/command", "POST", {"name": "RescanSeries", "seriesId": x["id"]})
    if it.get("season") is None:
        key = rr.key(it["kind"], it["tmdb"])
        rt.write_atomic(env.gone, {"gone": [g for g in rt.read_gone(env.gone) if g.get("key") != key]})
    with contextlib.suppress(Exception):
        env.jf("/Library/Media/Updated", "POST", {"Updates": [{"Path": "/" + (rt.rel_of(x.get("path"), "/data/") or ""), "UpdateType": "Created"}]})
    return back, missing


# ---- jobs ----
def used(items, uid, now, skip=None):
    return sum(1 for i, it in items.items() if i != skip and it.get("by") == uid and it.get("state") in COUNTED and (when(it.get("at")) or now) > now - datetime.timedelta(days=7))


def on_delete(env, data, job, now, who):
    it = {k: job.get(k) for k in ("id", "kind", "tmdb", "tvdb", "season", "title", "year", "requesters", "keep", "account")}
    it.update(by=job.get("user"), byName=job.get("userName"), at=job.get("at") or now.isoformat(timespec="seconds"), asks=[])
    if not isinstance(it["id"], str) or it["kind"] not in ("movie", "tv") or not isinstance(it["tmdb"], int) or it["id"] in data["items"]:
        return
    data["items"][it["id"]] = it
    audit(env, "requested", it)
    try:
        if rr.protected(rr.key(it["kind"], it["tmdb"]), []):
            raise Refused("protected")
        if used(data["items"], it["by"], now, skip=it["id"]) >= WEEK_MAX:
            raise Refused("quota")
        _, x = arr_item(env, it)
        jf_to, names = who
        asks = []
        if kept_forever(env, it) and it["by"] not in (it.get("requesters") or []):
            approvers = [u for u in it.get("requesters") or [] if u != it["by"]] or [OWNER]
            asks.append({"reason": "keep", "approvers": approvers, "names": [names.get(u, f"user {u}") for u in approvers], "answer": None})
        mine = {j for j, s in jf_to.items() if s == it["by"]}
        for jid in sorted(watchers(env, it["kind"], x, it.get("season"), now) - mine):
            sid = jf_to.get(jid, OWNER)
            if sid == it["by"] or any(a["reason"] == "watching" and a["approvers"] == [sid] for a in asks):
                continue
            asks.append({"reason": "watching", "approvers": [sid], "names": [names.get(sid, f"user {sid}")], "answer": None})
    except Refused as e:
        it["why"] = str(e)
        touch(env, it, "refused")
        audit(env, "refused", it, why=it["why"])
        return
    except Exception as e:  # noqa: BLE001
        it["why"] = f"{type(e).__name__}: {e}"[:200]
        touch(env, it, "failed")
        audit(env, "failed", it, why=it["why"])
        return
    if asks:
        it["asks"], it["expires"] = asks, (now + datetime.timedelta(days=ASK_DAYS)).isoformat(timespec="seconds")
        touch(env, it, "pending")
        audit(env, "pending", it, asks=[[a["reason"], a["approvers"]] for a in asks])
        for sid in dict.fromkeys(u for a in asks for u in a["approvers"]):
            env.notify(sid, "ask", info(it, it.get("byName"), it["expires"], next(a["reason"] for a in asks if sid in a["approvers"])))
        return
    touch(env, it, "approved")


def on_answer(env, data, job, now):
    it = data["items"].get(job.get("id"))
    uid, ok = job.get("user"), job.get("approve")
    if not isinstance(it, dict) or it.get("state") != "pending" or not isinstance(ok, bool):
        return
    mine = [a for a in it.get("asks") or [] if a.get("answer") is None and uid in (a.get("approvers") or [])]
    if not mine:
        print(f"delete-early: answer by user {uid} for {it.get('id')} ignored (not asked)", flush=True)
        return
    for a in mine:
        a.update(answer=ok, by=uid, at=now.isoformat(timespec="seconds"))
    name = job.get("userName") or f"user {uid}"
    if not ok:
        touch(env, it, "denied")
        audit(env, "denied", it, answeredBy=uid, answeredByName=name)
        env.notify(it["by"], "denied", info(it, name))
        return
    if all(a.get("answer") is True for a in it["asks"]):
        audit(env, "approved", it, answeredBy=uid, answeredByName=name)
        it["approvedBy"] = name
        touch(env, it, "approved")
    else:
        audit(env, "answer", it, answeredBy=uid, answeredByName=name, approve=True)


def on_restore(env, data, job, now):
    it = data["items"].get(job.get("id"))
    uid = job.get("user")
    if not isinstance(it, dict) or it.get("state") != "deleted" or (when(it.get("binUntil")) or now) <= now:
        return True
    if not (uid == it.get("by") or uid in (it.get("requesters") or []) or job.get("manager") is True):
        print(f"delete-early: restore by user {uid} for {it.get('id')} refused (not the deleter, a requester or a manager)", flush=True)
        return True
    with env.lock() as locked:
        if not locked:
            return False
        try:
            back, missing = restore(env, it)
        except Exception as e:  # noqa: BLE001
            audit(env, "restore-failed", it, user=uid, why=f"{type(e).__name__}: {e}")
            return True
    if it.get("season") is None:
        seerr_status(env, it, "available")
    it.update(restoredBy=uid, restoredByName=job.get("userName"), restoredAt=now.isoformat(timespec="seconds"))
    touch(env, it, "restored")
    audit(env, "restored", it, user=uid, files=len(back), left=len(missing))
    return True


def run_approved(env, data, now):
    todo = [it for it in data["items"].values() if isinstance(it, dict) and it.get("state") == "approved"]
    if not todo:
        return
    with env.lock() as locked:
        if not locked:
            print("delete-early: the media lock stayed busy - approved deletes wait for the next run", flush=True)
            return
        for it in todo:
            try:
                files = execute(env, it)
            except Refused as e:
                it["why"] = str(e)
                touch(env, it, "refused")
                audit(env, "refused", it, why=it["why"])
                continue
            except Exception as e:  # noqa: BLE001
                it["why"] = f"{type(e).__name__}: {e}"[:200]
                touch(env, it, "failed")
                audit(env, "failed", it, why=it["why"])
                continue
            it.update(files=files, deletedAt=now.isoformat(timespec="seconds"), binUntil=(now + datetime.timedelta(days=BIN_DAYS)).isoformat(timespec="seconds"))
            touch(env, it, "deleted")
            audit(env, "deleted", it, files=len(files))
            if it.get("season") is None:
                seerr_status(env, it, "unknown")
            save(env, data)
            if it.get("approvedBy"):
                env.notify(it["by"], "approved", info(it, it["approvedBy"], it["binUntil"]))
            for u in dict.fromkeys(it.get("requesters") or []):
                if u != it["by"]:
                    env.notify(u, "deleted", info(it, it.get("byName"), it["binUntil"]))


def expire(env, data, now):
    for it in data["items"].values():
        if isinstance(it, dict) and it.get("state") == "pending" and (when(it.get("expires")) or now) <= now:
            touch(env, it, "expired")
            audit(env, "expired", it)
            env.notify(it["by"], "expired", info(it, None))


def run(env):
    now = env.now()
    data = load(env)
    expire(env, data, now)
    jobs = []
    for p in sorted(env.jobs.glob("*.req")):
        try:
            jobs.append((json.loads(p.read_text()), p))
        except (OSError, ValueError):
            print(f"delete-early: unreadable job {p.name} dropped", flush=True)
            with contextlib.suppress(OSError):
                p.unlink()
    jobs.sort(key=lambda j: (str(j[0].get("at") or ""), j[1].name))
    who = None
    for job, path in jobs:
        op, done = job.get("op"), True
        if op == "delete":
            who = who or people(env)
            on_delete(env, data, job, now, who)
        elif op == "answer":
            on_answer(env, data, job, now)
        elif op == "restore":
            done = on_restore(env, data, job, now)
        save(env, data)
        if done:
            with contextlib.suppress(OSError):
                path.unlink()
    run_approved(env, data, now)
    save(env, data)
    return 0


# ---- notices ----
class Notifier:
    """Jellyfin pop-up on the person's live sessions (text by the Jellyfin display language) and Telegram where a chat is known (the owner: oob-alert's chat)."""

    def __init__(self, jf, jf_to, chats, telegram):
        self.jf, self.jf_to, self.chats, self.telegram = jf, jf_to, chats, telegram

    def __call__(self, sid, event, inf):
        for jid in [j for j, s in self.jf_to.items() if s == sid]:
            try:
                prefs = self.jf(f"/DisplayPreferences/usersettings?userId={jid}&client=emby") or {}
                lang = "de" if str((prefs.get("CustomPrefs") or {}).get("language") or "").lower().startswith("de") else "en"
                head, text = message(event, inf, lang)
                for s in self.jf("/Sessions?activeWithinSeconds=900") or []:
                    if norm(s.get("UserId")) == jid:
                        self.jf(f"/Sessions/{urllib.parse.quote(str(s['Id']), safe='')}/Message", "POST", {"Header": head, "Text": text, "TimeoutMs": 30000})
            except Exception as e:  # noqa: BLE001
                print(f"delete-early: pop-up for user {sid} not sent ({type(e).__name__})", flush=True)
        chat = self.chats.get(sid)
        if chat:
            head, text = message(event, inf, "en")
            try:
                self.telegram(chat, f"{head}\n{text}")
            except Exception as e:  # noqa: BLE001
                print(f"delete-early: Telegram for user {sid} not sent ({type(e).__name__})", flush=True)


def env_file(path):
    out = {}
    with contextlib.suppress(OSError):
        for line in pathlib.Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def telegram_sender(token_file):
    def send(chat, text):
        token = pathlib.Path(token_file).read_text().strip()
        subprocess.run(["curl", "-sS", "--fail", "--max-time", "20", "--doh-url", "https://1.1.1.1/dns-query", "--output", "/dev/null", "--config", "-",
                        "--data-urlencode", f"chat_id={chat}", "--data-urlencode", f"text={text}"],
                       input=f'url = "https://api.telegram.org/bot{token}/sendMessage"\n', text=True, check=True, timeout=30, capture_output=True)
    return send


def main(argv=None):
    ap = argparse.ArgumentParser(description="Delete early runner: carries out Seerr's queued delete / answer / restore jobs (started by delete-early.path and delete-early.timer)",
                                 epilog="next: journalctl -u delete-early -n 50  |  scripts/delete-early.py --list")
    ap.add_argument("--list", action="store_true", help="print every request with its state, change nothing")
    a = ap.parse_args(argv)
    e = os.environ
    state = pathlib.Path(e.get("DE_STATE", pathlib.Path.home() / ".local" / "state" / "movie-downloads"))
    if a.list:
        try:
            items = json.loads((state / "delete-early.json").read_text())["items"].values()
        except (OSError, ValueError, KeyError):
            items = []
        for it in sorted(items, key=lambda x: x.get("at") or ""):
            print(f"{it.get('at', '')[:16]}  {it.get('state', ''):9} {rr.key(it['kind'], it['tmdb']):14} {'whole' if it.get('season') is None else 'season ' + str(it['season']):9} "
                  f"{it.get('title')!r} by {it.get('byName')}" + (f"  bin until {it['binUntil'][:16]}" if it.get("state") == "deleted" else "") + (f"  ({it['why']})" if it.get("why") else ""))
        print("next: journalctl -u delete-early -n 50")
        return 0
    jf_key = pathlib.Path(e.get("DE_JF_KEY", "/home/buntu/Projects/Docker/Jellyfin/secrets/api-key")).read_text().strip()
    seerr_key = pathlib.Path(e.get("DE_SEERR_KEY", HERE.parent.parent / "Seerr" / "secrets" / "api-key")).read_text().strip()
    radarr = rt.api(e.get("DE_RADARR", "http://127.0.0.1:7878") + "/api/v3", {"X-Api-Key": rt.arr_key(e.get("DE_RADARR_CONFIG", "/srv/appdata/radarr/config.xml"))})
    sonarr = rt.api(e.get("DE_SONARR", "http://127.0.0.1:8989") + "/api/v3", {"X-Api-Key": rt.arr_key(e.get("DE_SONARR_CONFIG", "/srv/appdata/sonarr/config.xml"))})
    jf = rt.api(e.get("DE_JF", "http://127.0.0.1:8096"), {"Authorization": f"MediaBrowser Token={jf_key}"})
    seerr = rt.api(e.get("DE_SEERR", "http://127.0.0.1:5055") + "/api/v1", {"X-Api-Key": seerr_key})
    oob = env_file(e.get("DE_OOB_ENV", HERE.parent.parent / "_global" / "secrets" / "oob-alert.env"))
    chats = {OWNER: oob["CHAT_ID"]} if oob.get("CHAT_ID") else {}
    with contextlib.suppress(OSError, ValueError, AttributeError):
        chats.update({int(k): str(v) for k, v in json.loads(pathlib.Path(e.get("DE_TELEGRAM", HERE.parent / "secrets" / "delete-early-telegram.json")).read_text()).items()
                      if str(k).isdigit()})
    env = Env(radarr=radarr, sonarr=sonarr, jf=jf, seerr=seerr, jobs=pathlib.Path(e.get("DE_JOBS", "/srv/appdata/seerr-delete-early")), state=state / "delete-early.json",
              audit=state / "delete-early-audit.jsonl", gone=state / "retention-gone.json", retention=pathlib.Path(e.get("DE_RETENTION", "/srv/appdata/indexer-bar/retention.json")),
              keep=pathlib.Path(e.get("DE_KEEP", "/srv/appdata/seerr-retention/keep.json")), identity=pathlib.Path(e.get("DE_IDENTITY", "/srv/appdata/jellyfin-popular/identity.json")),
              pool=e.get("DE_POOL", rt.POOL), now=lambda: datetime.datetime.now(TZ).replace(microsecond=0), notify=None, lock=rt.media_lock)
    jf_to, _ = people(env)
    token_file = oob.get("TOKEN_FILE") or str(HERE.parent.parent / "_global" / "secrets" / "telegram_bot_token")
    env.notify = Notifier(jf, jf_to, chats, telegram_sender(token_file))
    return run(env)


if __name__ == "__main__":
    sys.exit(main())
