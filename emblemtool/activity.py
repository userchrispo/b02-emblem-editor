"""Last time the PS5 hit the emblem endpoint, and whether Show already fired.

The control panel polls this through /api/status. The proxy is the only writer
for hits/captures; the web UI can clear the session-reset flag after the user
restarts the game.
"""
import json
import os
import threading
import time

from . import config

_lock = threading.Lock()

_EMPTY = {
    "seq": 0,
    "last_hit": None,
    "last_capture": None,
    "needs_session_reset": False,
}


def _path():
    return config.ACTIVITY_FILE


def read_activity():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(_EMPTY)
    if not isinstance(data, dict):
        return dict(_EMPTY)
    out = dict(_EMPTY)
    out.update(data)
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


def record_event(kind, slot=None, group=None):
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
        data["last_hit"] = hit
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
        return data


def clear_session_flag():
    with _lock:
        data = read_activity()
        data["needs_session_reset"] = False
        _write(data)
        return data
