"""Captured emblem groups: one folder per target player under saved/,
each holding meta.json (userhash, capture time) and one slot_<N>.bin per
saved emblem slot that player's console requested.
"""
import json
import os
import re
import shutil
import threading
import time

from . import config

_capture_lock = threading.Lock()
_warm_threads = []

_SLOT_RE = re.compile(r"^slot_(\d+)\.bin$")
_GROUP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_group_name(name, *, allow_active=False):
    """Ensure a group name is a single safe directory component under SAVED_DIR."""
    if not isinstance(name, str) or not _GROUP_NAME_RE.match(name):
        raise ValueError(f"invalid group name: {name!r}")
    if not allow_active and name == config.ACTIVE_NAME:
        raise ValueError(f"reserved group name: {name!r}")
    return name


def group_dir(name, *, allow_active=False):
    safe = validate_group_name(name, allow_active=allow_active)
    root = os.path.abspath(config.SAVED_DIR)
    target = os.path.abspath(os.path.join(root, safe))
    if os.path.commonpath([root, target]) != root:
        raise ValueError(f"path escapes saved directory: {name!r}")
    return target


def read_meta(name):
    path = os.path.join(group_dir(name), "meta.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def write_meta(name, meta):
    d = group_dir(name)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "meta.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    os.replace(tmp, path)


def list_groups():
    """Real captured groups, excluding the reserved active-set pseudo-group."""
    os.makedirs(config.SAVED_DIR, exist_ok=True)
    names = [
        d for d in os.listdir(config.SAVED_DIR)
        if d != config.ACTIVE_NAME
        and _GROUP_NAME_RE.match(d)
        and os.path.isdir(group_dir(d))
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
                "risks": _slot_risks(path),
            })
    emblems.sort(key=lambda e: e["captured_at"], reverse=True)
    return emblems


def _slot_risks(path):
    try:
        from .shapes.render import parse_slot_file
        from .shapes.unlocks import layer_risks
        return layer_risks(parse_slot_file(path))
    except Exception:
        return []


def slot_path(group, slot):
    slot_int = int(slot)
    if slot_int < 0:
        raise ValueError(f"invalid slot: {slot!r}")
    return os.path.join(group_dir(group), f"slot_{slot_int}.bin")


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


def delete_emblem(group, slot):
    """Remove one saved slot. Deletes the group folder if it is then empty."""
    path = slot_path(group, slot)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    try:
        from .thumbs import THUMB_SIZE, thumb_path
        tp = thumb_path(group, slot, THUMB_SIZE)
        if os.path.isfile(tp):
            os.remove(tp)
    except OSError:
        pass
    set_emblem_label(group, slot, "")
    if not group_slots(group):
        shutil.rmtree(group_dir(group), ignore_errors=True)
    return True


def import_slot_bytes(data, label=None):
    """Save a captured or exported .bin into the library.

    Accepts HTTP-wrapped files or a raw 1408-byte body.
    """
    from .shapes.render import (
        LAYER_SIZE,
        NUM_LAYERS,
        encode_layers,
        parse_slot_bytes,
        strip_http,
        wrap_slot_http,
    )
    if not data:
        raise ValueError("That is not an emblem file")
    body = strip_http(data)
    if len(body) < LAYER_SIZE:
        raise ValueError("That is not an emblem file")
    layers = parse_slot_bytes(data)
    blob = wrap_slot_http(encode_layers(layers) if layers else body[: NUM_LAYERS * LAYER_SIZE])
    return save_authored_emblem(blob, label or "Imported emblem")
