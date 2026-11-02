import re
from datetime import datetime

TICKS = 10_000_000
STALL_MIN_WALL = 5.0
STALL_MIN_LAG = 3.0
SEEK_TOLERANCE = 5.0
POLL_INTERVAL = 5.0
STALL_STREAK = 2
SILENT_AFTER = 45.0
SLOW_FPS_RATIO = 0.95
SLOW_BUFFER = 30.0
SLOW_POLLS = 2
EPISODE_GAP = 120.0


def _parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    v = value.strip()
    if v.endswith(("Z", "z")):
        v = v[:-1] + "+00:00"
    v = re.sub(r"(\.\d{6})\d+", r"\1", v)
    try:
        return datetime.fromisoformat(v).timestamp()
    except ValueError:
        return None


def _source_fps(item):
    for stream in item.get("MediaStreams") or []:
        if stream.get("Type") == "Video":
            return stream.get("RealFrameRate")
    return None


def _item_name(item):
    name = item.get("Name") or ""
    series = item.get("SeriesName")
    return f"{series} - {name}" if series else name


class _State:
    def __init__(self, now, pos, checkin):
        self.pos = pos
        self.checkin = checkin
        self.base_now = now
        self.base_pos = pos
        self.base_ts = _parse_ts(checkin)
        self.streak = 0
        self.prev_lag = 0.0
        self.activity = now
        self.silent = False
        self.slow_polls = 0
        self.slow_since = None
        self.slow_reported = False


class Tracker:
    def __init__(self, clock):
        self.clock = clock
        self._states = {}
        self._active = {}
        self._last_incident = None
        self._last_end = None

    def update(self, sessions, now):
        incidents = []
        seen = {}
        for s in sessions:
            item = s.get("NowPlayingItem")
            if not item:
                continue
            sid = s.get("Id")
            seen[sid] = s
            play = s.get("PlayState") or {}
            pos = (play.get("PositionTicks") or 0) / TICKS
            checkin = s.get("LastPlaybackCheckIn")
            st = self._states.get(sid)
            if st is None:
                st = self._states[sid] = _State(now, pos, checkin)
            if play.get("IsPaused"):
                st.base_now = None
                st.streak = 0
                st.activity = now
                st.silent = False
                st.slow_polls = 0
                st.pos, st.checkin = pos, checkin
                continue
            inc = []
            if st.base_now is None:
                st.base_now, st.base_pos, st.activity = now, pos, now
                st.base_ts = _parse_ts(checkin)
            elif pos != st.pos or checkin != st.checkin:
                after_gap = st.silent or now - st.activity >= SILENT_AFTER
                st.activity = now
                st.silent = False
                ts = _parse_ts(checkin)
                if st.base_ts is not None and ts is not None:
                    wall, min_lag = ts - st.base_ts, STALL_MIN_LAG
                else:
                    wall, min_lag = now - st.base_now, STALL_MIN_LAG + POLL_INTERVAL
                if after_gap:
                    st.streak = 0
                    st.base_now, st.base_pos, st.base_ts = now, pos, ts
                elif wall >= STALL_MIN_WALL:
                    delta = pos - st.base_pos
                    lag = wall - delta
                    if 0 <= delta <= wall + SEEK_TOLERANCE and lag >= min_lag:
                        st.streak += 1
                        if st.streak == STALL_STREAK:
                            inc.append(self._incident("stall", st.prev_lag + lag, s, item, None))
                        elif st.streak > STALL_STREAK:
                            inc.append(self._incident("stall", lag, s, item, None))
                        st.prev_lag = lag
                    else:
                        st.streak = 0
                    st.base_now, st.base_pos, st.base_ts = now, pos, ts
            elif not st.silent and now - st.activity >= SILENT_AFTER:
                st.silent = True
                inc.append(self._incident("silent", now - st.activity, s, item, None))
            st.pos, st.checkin = pos, checkin
            slow = self._slow(s, item, pos)
            if slow is None:
                st.slow_polls, st.slow_since, st.slow_reported = 0, None, False
            else:
                if st.slow_polls == 0:
                    st.slow_since = now
                st.slow_polls += 1
                if st.slow_polls >= SLOW_POLLS and not st.slow_reported:
                    st.slow_reported = True
                    inc.append(self._incident("slow_transcode", now - st.slow_since, s, item, slow))
            incidents += inc
        if self._states.keys() - seen.keys():
            self._states = {k: v for k, v in self._states.items() if k in seen}
        if self._active and not seen:
            self._last_end = now
        self._active = seen
        if incidents:
            self._last_incident = now
        return incidents

    def _slow(self, s, item, pos):
        info = s.get("TranscodingInfo")
        fps = _source_fps(item)
        runtime = (item.get("RunTimeTicks") or 0) / TICKS
        if not info or not fps or not runtime:
            return None
        enc, pct = info.get("Framerate"), info.get("CompletionPercentage")
        if enc is None or pct is None:
            return None
        buffer_ahead = pct / 100 * runtime - pos
        if enc < SLOW_FPS_RATIO * fps and buffer_ahead < SLOW_BUFFER:
            return {"encoder_fps": enc, "source_fps": fps, "buffer_ahead": buffer_ahead}
        return None

    def _incident(self, kind, seconds, s, item, slow):
        info = s.get("TranscodingInfo")
        transcode = None
        if info:
            transcode = {
                "reasons": info.get("TranscodeReasons"),
                "hw_accel": info.get("HardwareAccelerationType"),
                "video_codec": info.get("VideoCodec"),
                "bitrate": info.get("Bitrate"),
                "is_video_direct": info.get("IsVideoDirect"),
                "encoder_fps": info.get("Framerate"),
                "source_fps": _source_fps(item),
            }
            if slow:
                transcode.update(slow)
        return {
            "kind": kind, "seconds": seconds, "session_id": s.get("Id"), "user": s.get("UserName"),
            "client": s.get("Client"), "device": s.get("DeviceName"), "item": _item_name(item),
            "play_method": (s.get("PlayState") or {}).get("PlayMethod"), "transcode": transcode,
        }

    def playing(self):
        return len(self._active)

    def active_by_method(self):
        counts = {}
        for s in self._active.values():
            method = (s.get("PlayState") or {}).get("PlayMethod") or "Unknown"
            counts[method] = counts.get(method, 0) + 1
        return counts

    def last_playing_end(self):
        return self._last_end

    def episode_active(self, now):
        return bool(self._active) and self._last_incident is not None and now - self._last_incident <= EPISODE_GAP
