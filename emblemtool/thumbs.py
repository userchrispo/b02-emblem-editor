"""On-disk cache of emblem preview PNGs.

The control panel used to rasterize every card on every refresh. Caching by
the slot file's mtime keeps clicks from waiting behind a pile of Pillow work.
"""
import os
import threading

from .storage import group_dir, slot_path

THUMB_SIZE = 220
_write_lock = threading.Lock()


def thumb_path(group, slot, size=THUMB_SIZE):
    return os.path.join(group_dir(group), f"slot_{slot}.thumb{size}.png")


def thumb_etag(group, slot, size=THUMB_SIZE):
    mtime_ns = os.stat(slot_path(group, slot)).st_mtime_ns
    return f'"{mtime_ns:x}-{int(size)}"'


def _read_if_fresh(out, bin_path):
    bin_mtime = os.path.getmtime(bin_path)
    if os.path.isfile(out) and os.path.getmtime(out) >= bin_mtime:
        with open(out, "rb") as f:
            return f.read()
    return None


def cached_thumb_png(group, slot, size=THUMB_SIZE):
    """Return the PNG bytes for a slot, regenerating only when the .bin changes."""
    bin_path = slot_path(group, slot)
    out = thumb_path(group, slot, size)
    try:
        hit = _read_if_fresh(out, bin_path)
        if hit is not None:
            return hit
    except OSError:
        pass
    from .shapes.render import render_file_png_bytes
    png = render_file_png_bytes(bin_path, size=size)
    with _write_lock:
        try:
            hit = _read_if_fresh(out, bin_path)
            if hit is not None:
                return hit
        except OSError:
            pass
        try:
            os.makedirs(os.path.dirname(out), exist_ok=True)
            tmp = out + ".tmp"
            with open(tmp, "wb") as f:
                f.write(png)
            os.replace(tmp, out)
        except OSError:
            pass
    return png
