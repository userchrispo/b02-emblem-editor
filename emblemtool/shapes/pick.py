"""Pick the front-most emblem layer under a canvas point.

Coordinates match the editor canvas: origin at the emblem centre, +Y down,
x/y in -0.5..0.5, size 2**scale. Rotation is clockwise degrees, same as BO2.
"""
import math

ALPHA_MIN = 12


def layer_uv(layer, x, y):
    """Return (u, v) in 0..1 across the glyph, or None if outside its box."""
    w = 2 ** float(layer["sx"])
    h = 2 ** float(layer["sy"])
    if w <= 0 or h <= 0:
        return None
    dx = float(x) - float(layer["x"])
    dy = float(y) - float(layer["y"])
    rad = -float(layer.get("rot") or 0) * math.pi / 180.0
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    rx = dx * cos_a - dy * sin_a
    ry = dx * sin_a + dy * cos_a
    if layer.get("flipped"):
        rx = -rx
    u = rx / w + 0.5
    v = ry / h + 0.5
    if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0:
        return None
    return u, v


def hit_layer_index(layers, x, y, alpha_fn):
    """Index of the front-most layer with opaque glyph pixels at (x, y).

    `alpha_fn(shape_id, u, v)` returns 0..255. Front = highest `index`.
    """
    order = sorted(
        range(len(layers)),
        key=lambda i: int(layers[i].get("index", i)),
        reverse=True,
    )
    for i in order:
        uv = layer_uv(layers[i], x, y)
        if uv is None:
            continue
        if alpha_fn(layers[i]["shape"], uv[0], uv[1]) >= ALPHA_MIN:
            return i
    return -1
