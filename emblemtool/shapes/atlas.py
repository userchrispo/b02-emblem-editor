"""One packed PNG of every calibrated shape glyph, for a fast editor load."""
import io
import math
import threading

from PIL import Image

from .render import _load_shape_image
from .shape_id_map import list_shapes

ATLAS_CELL = 128
ATLAS_URL = "/api/shapes/atlas.png"

_lock = threading.Lock()
_png = None
_meta = None
_image = None


def _build():
    shapes = list_shapes()
    n = max(1, len(shapes))
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    cell = ATLAS_CELL
    img = Image.new("RGBA", (cols * cell, rows * cell), (0, 0, 0, 0))
    tiles = {}
    for i, s in enumerate(shapes):
        col, row = i % cols, i // cols
        x, y = col * cell, row * cell
        tiles[str(s["id"])] = {"x": x, "y": y, "col": col, "row": row}
        glyph = _load_shape_image(s["id"])
        if glyph is None:
            continue
        lum, alpha = glyph.split()
        rgba = Image.merge("RGBA", (lum, lum, lum, alpha)).resize(
            (cell, cell), Image.BILINEAR,
        )
        img.paste(rgba, (x, y), rgba)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    meta = {
        "url": ATLAS_URL,
        "cell": cell,
        "cols": cols,
        "rows": rows,
        "width": img.width,
        "height": img.height,
        "tiles": tiles,
    }
    return buf.getvalue(), meta, img


def get_shape_atlas():
    """Return (png_bytes, meta). Built once, then reused."""
    global _png, _meta, _image
    with _lock:
        if _png is None:
            _png, _meta, _image = _build()
        return _png, _meta


def atlas_alpha(shape_id, u, v):
    """Glyph alpha 0..255 at normalized (u, v). Missing tile counts as solid."""
    get_shape_atlas()
    tile = _meta["tiles"].get(str(int(shape_id)))
    if not tile:
        return 255
    cell = _meta["cell"]
    px = tile["x"] + min(cell - 1, max(0, int(u * cell)))
    py = tile["y"] + min(cell - 1, max(0, int(v * cell)))
    return _image.getpixel((px, py))[3]


def warm_atlas():
    threading.Thread(target=get_shape_atlas, daemon=True).start()
