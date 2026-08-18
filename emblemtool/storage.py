"""Captured emblem groups: one folder per target player under saved/,
each holding meta.json (userhash, capture time) and one slot_<N>.bin per
saved emblem slot that player's console requested.
"""
import json
import os
import re
import threading
import time

from . import config

_capture_lock = threading.Lock()
_warm_threads = []

_SLOT_RE = re.compile(r"^slot_(\d+)\.bin$")


def group_dir(name):
    return os.path.join(config.SAVED_DIR, name)


def read_meta(name):
    path = os.path.join(group_dir(name), "meta.json")
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def write_meta(name, meta):
    os.makedirs(group_dir(name), exist_ok=True)
    with open(os.path.join(group_dir(name), "meta.json"), "w") as f:
        json.dump(meta, f)


def list_groups():
    """Real captured groups, excluding the reserved active-set pseudo-group."""
    os.makedirs(config.SAVED_DIR, exist_ok=True)
    names = [
        d for d in os.listdir(config.SAVED_DIR)
        if os.path.isdir(group_dir(d)) and d != config.ACTIVE_NAME
    ]

    def sort_key(n):
        return (0, int(n)) if n.isdigit() else (1, n)
    return sorted(names, key=sort_key)


def group_slots(name):
    d = group_dir(name)
    if not os.path.isdir(d):
        return []
    return sorted(
        int(m.group(1)) for f in os.listdir(d) if (m := _SLOT_RE.match(f))
    )


def next_group_index():
    nums = [int(n) for n in list_groups() if n.isdigit()]
    return (max(nums) + 1) if nums else 1


def get_or_create_capture_group(userhash):
    """Reuse the most recently created group if it's the same target
    (so a burst of slot requests for one player lands in one group),
    otherwise start a new group."""
    with _capture_lock:
        groups = list_groups()
        if groups:
            last = groups[-1]
            meta = read_meta(last)
            if meta and meta.get("userhash") == userhash:
                return last
        idx = next_group_index()
        name = f"{idx:03d}"
        write_meta(name, {
            "userhash": userhash,
            "first_captured": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        return name


# ---------- per-emblem labels ----------
# Users think of each captured slot as one "emblem" - they never need to know
# it's technically a (group, slot) pair. Labels are keyed by slot number
# (as a string) inside the group's own meta.json.

def get_emblem_label(group, slot):
    meta = read_meta(group) or {}
    return meta.get("labels", {}).get(str(slot), "")


def set_emblem_label(group, slot, label):
    meta = read_meta(group) or {}
    labels = meta.setdefault("labels", {})
    if label:
        labels[str(slot)] = label
    else:
        labels.pop(str(slot), None)
    write_meta(group, meta)


def list_emblems():
    """Every captured emblem as a flat list, newest first - one entry per
    (group, slot), with no group/slot concepts exposed beyond an opaque id."""
    emblems = []
    for group in list_groups():
        meta = read_meta(group) or {}
        for slot in group_slots(group):
            path = slot_path(group, slot)
            try:
                mtime = int(os.path.getmtime(path))
            except OSError:
                mtime = 0
            emblems.append({
                "id": f"{group}:{slot}",
                "group": group,
                "slot": slot,
                "label": meta.get("labels", {}).get(str(slot), ""),
                "captured_at": meta.get("first_captured", ""),
                "source": meta.get("source", "capture"),
                "mtime": mtime,
            })
    emblems.sort(key=lambda e: e["captured_at"], reverse=True)
    return emblems


def slot_path(group, slot):
    return os.path.join(group_dir(group), f"slot_{slot}.bin")


def write_slot_bytes(group, slot, data):
    os.makedirs(group_dir(group), exist_ok=True)
    with open(slot_path(group, slot), "wb") as f:
        f.write(data)
    thread = threading.Thread(target=_warm_thumb, args=(group, slot), daemon=True)
    thread.start()
    _warm_threads[:] = [t for t in _warm_threads if t.is_alive()]
    _warm_threads.append(thread)


def wait_warm_thumbs(timeout=5):
    """Wait for background thumbnail renders. Tests use this before cleanup."""
    while _warm_threads:
        _warm_threads.pop(0).join(timeout=timeout)


def _warm_thumb(group, slot):
    try:
        from .thumbs import cached_thumb_png
        cached_thumb_png(group, slot)
    except Exception:
        pass


def save_authored_emblem(data, label, group=None, slot=0):
    """Write an HTTP-wrapped emblem blob as a saved slot.

    If `group` already exists, overwrite that slot and update its label.
    Otherwise create a new numbered group tagged source=editor.
    """
    label = (label or "").strip() or "Untitled emblem"
    with _capture_lock:
        if group and os.path.isdir(group_dir(group)):
            write_slot_bytes(group, slot, data)
            set_emblem_label(group, slot, label)
            return group, slot
        idx = next_group_index()
        name = f"{idx:03d}"
        write_meta(name, {
            "userhash": "editor",
            "source": "editor",
            "first_captured": time.strftime("%Y-%m-%d %H:%M:%S"),
            "labels": {str(slot): label},
        })
        write_slot_bytes(name, slot, data)
        return name, slot
