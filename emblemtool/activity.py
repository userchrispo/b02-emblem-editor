"""Last time the PS5 hit the emblem endpoint, recent traffic log, and SSE pub/sub.

The control panel subscribes via /api/events (SSE) or polls /api/status.
The proxy is the only writer for hits/captures; the web UI can clear the
session-reset flag after the user restarts the game.
"""
import json
import os
import queue
import threading
import time

from . import config

_lock = threading.Lock()
_sub_lock = threading.Lock()
_subscribers = set()

MAX_HISTORY = 50

_EMPTY = {
    "seq": 0,
    "last_hit": None,
    "last_capture": None,
    "needs_session_reset": False,
    "events": [],
}


def _path():
    return config.ACTIVITY_FILE


def read_activity():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {**_EMPTY, "events": []}
    if not isinstance(data, dict):
        return {**_EMPTY, "events": []}
    out = {**_EMPTY, "events": []}
    out.update(data)
    if not isinstance(out.get("events"), list):
        out["events"] = []
    return out


def _write(data):
    path = _path()
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def subscribe():
    """Register an SSE subscriber queue."""
    q = queue.Queue(maxsize=32)
    with _sub_lock:
        _subscribers.add(q)
    return q


def unsubscribe(q):
    with _sub_lock:
        _subscribers.discard(q)


def notify_subscribers(event_type, payload):
    """Push a non-blocking notification to all connected SSE clients."""
    msg = (event_type, payload)
    with _sub_lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass


def record_event(kind, slot=None, group=None, *, host=None, path=None, size=None, cache_headers=None):
    """kind: passthrough | capture | show | show-cache-check"""
    with _lock:
        data = read_activity()
        data["seq"] = int(data.get("seq") or 0) + 1
        now = time.time()
        hit = {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ts": now,
            "kind": kind,
            "slot": slot,
            "seq": data["seq"],
        }
        if group is not None:
            hit["group"] = group
        if host:
            hit["host"] = host
        if path:
            hit["path"] = path
        if size is not None:
            hit["size"] = int(size)
        if cache_headers:
            hit["cache_headers"] = dict(cache_headers)

        data["last_hit"] = hit
        events = list(data.get("events") or [])
        events.insert(0, hit)
        data["events"] = events[:MAX_HISTORY]

        if kind == "capture" and group is not None:
            data["last_capture"] = {
                "at": hit["at"],
                "ts": now,
                "seq": data["seq"],
                "group": group,
                "slot": slot,
            }
        if kind == "show":
            data["needs_session_reset"] = True
        _write(data)
    notify_subscribers("activity", hit)
    return data


def clear_session_flag():
    with _lock:
        data = read_activity()
        data["needs_session_reset"] = False
        _write(data)
    notify_subscribers("session_reset", {"needs_session_reset": False})
    return data
