"""Image-to-BO2-Emblem global multi-layer shape-fitting vectorizer (pure Python + Pillow).

Converts an arbitrary raster image into <= 32 Black Ops II emblem layers in two phases:
  Phase 1 (Saliency-Weighted Greedy Build):
    - Smart Layer-0 background fill (inset border detection vs global mean).
    - Per-pixel saliency weights combining Sobel edge magnitude + chroma saliency.
    - Error-weighted importance sampling with top-2 beam hill climbing and
      exact single-pass closed-form least-squares optimal RGB solving.
  Phase 2 (Global Associative Multi-Layer Joint Refinement):
    - Exploits Porter-Duff source-over associativity: caches opaque `below[i]`
      (layers 0..i-1) and premultiplied `above[i]` (layers i+1..N-1) so every
      middle layer can be re-optimized with exact closed-form optimal RGB and
      exact global delta-error across the entire 32-layer stack.

Only unlocked `tools` shapes are used by default so generated emblems never
fail in-game unlock checks.
"""
import io
import math
import random

from PIL import Image

from .render import NUM_LAYERS, _load_shape_image
from .shape_id_map import known_ids

BG_SHAPE_ID = 196  # Square Full (always unlocked)
WR, WG, WB = 2.0, 4.0, 3.0
WSUM = WR + WG + WB

SHAPE_SETS = {
    "basic": [192, 196, 144, 187, 137],
    "geometric": [
        192, 196, 144, 187, 194, 137, 138, 195, 180, 141,
        177, 162, 140, 178, 183,
    ],
}

QUALITY_PRESETS = {
    "fast": {"grid": 44, "candidates": 45, "hill_steps": 40, "global_passes": 1},
    "balanced": {"grid": 52, "candidates": 70, "hill_steps": 60, "global_passes": 2},
    "high": {"grid": 56, "candidates": 90, "hill_steps": 80, "global_passes": 4},
}

_MASK_CACHE = {}
_MASK_SIZE = 32


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _get_shape_mask(shape_id):
    if shape_id in _MASK_CACHE:
        return _MASK_CACHE[shape_id]
    img = _load_shape_image(shape_id)
    if img is None:
        return None
    _, alpha = img.split()
    small = alpha.resize((_MASK_SIZE, _MASK_SIZE), Image.BILINEAR)
    mask = [b / 255.0 for b in small.tobytes()]
    _MASK_CACHE[shape_id] = mask
    return mask


def _resolve_shape_ids(shape_set):
    if shape_set == "all_tools":
        ids = [sid for sid, label in known_ids.items() if label.startswith("tools/")]
        return sorted(ids) or list(SHAPE_SETS["geometric"])
    return list(SHAPE_SETS.get(shape_set, SHAPE_SETS["geometric"]))


def _prepare_target(image, grid, fit="cover"):
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    sw, sh = image.size

    probe = image.resize((16, 16), Image.BILINEAR)
    px = probe.load()
    br = bg = bb = bc = 0
    for y in range(16):
        for x in range(16):
            if x not in (0, 15) and y not in (0, 15):
                continue
            r, g, b, a = px[x, y]
            if a < 16:
                continue
            br += r
            bg += g
            bb += b
            bc += 1
    fill = (round(br / bc), round(bg / bc), round(bb / bc), 255) if bc else (24, 24, 24, 255)
    canvas = Image.new("RGBA", (grid, grid), fill)

    if fit == "stretch":
        canvas.alpha_composite(image.resize((grid, grid), Image.LANCZOS), (0, 0))
    elif fit == "contain":
        scale = min(grid / max(1, sw), grid / max(1, sh))
        dw = max(1, int(round(sw * scale)))
        dh = max(1, int(round(sh * scale)))
        canvas.alpha_composite(image.resize((dw, dh), Image.LANCZOS), ((grid - dw) // 2, (grid - dh) // 2))
    else:
        side = min(sw, sh)
        left = (sw - side) // 2
        top = (sh - side) // 2
        cropped = image.crop((left, top, left + side, top + side)).resize((grid, grid), Image.LANCZOS)
        canvas.alpha_composite(cropped, (0, 0))

    data = canvas.convert("RGB").tobytes()
    n = grid * grid
    tr = [float(data[i * 3]) for i in range(n)]
    tg = [float(data[i * 3 + 1]) for i in range(n)]
    tb = [float(data[i * 3 + 2]) for i in range(n)]
    return tr, tg, tb


def _compute_saliency(tr, tg, tb, grid):
    """Sobel edge magnitude + chroma saliency + scan-margin damping."""
    n = grid * grid
    lum = [0.299 * tr[i] + 0.587 * tg[i] + 0.114 * tb[i] for i in range(n)]
    weights = [1.0] * n
    for y in range(grid):
        ym, yp = max(0, y - 1), min(grid - 1, y + 1)
        for x in range(grid):
            xm, xp = max(0, x - 1), min(grid - 1, x + 1)
            gx = (
                -lum[ym * grid + xm] + lum[ym * grid + xp]
                - 2.0 * lum[y * grid + xm] + 2.0 * lum[y * grid + xp]
                - lum[yp * grid + xm] + lum[yp * grid + xp]
            )
            gy = (
                -lum[ym * grid + xm] - 2.0 * lum[ym * grid + x] - lum[ym * grid + xp]
                + lum[yp * grid + xm] + 2.0 * lum[yp * grid + x] + lum[yp * grid + xp]
            )
            edge = math.hypot(gx, gy) / 255.0
            i = y * grid + x
            chroma = (max(tr[i], tg[i], tb[i]) - min(tr[i], tg[i], tb[i])) / 255.0
            damp = 0.35 if (x < 2 or x >= grid - 2 or y < 2 or y >= grid - 2) else 1.0
            weights[i] = damp * (1.0 + 1.8 * min(edge, 1.5) + 3.2 * chroma)
    return weights


def _eval_in_stack(cand, mask, grid, tr, tg, tb, weights, br, bg, bb, ar, ag, ab, aa):
    """Exact single-pass global evaluation of `cand` between `below` and premul `above`."""
    w = 2.0 ** cand["sx"]
    h = 2.0 ** cand["sy"]
    if w < 0.02 or h < 0.02:
        return None
    rad = -math.radians(cand["rot"])
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)
    half_w = 0.5 * (abs(w * cos_r) + abs(h * sin_r))
    half_h = 0.5 * (abs(w * sin_r) + abs(h * cos_r))

    px0 = max(0, int(math.floor((cand["x"] - half_w + 0.5) * grid)))
    px1 = min(grid - 1, int(math.ceil((cand["x"] + half_w + 0.5) * grid)))
    py0 = max(0, int(math.floor((cand["y"] - half_h + 0.5) * grid)))
    py1 = min(grid - 1, int(math.ceil((cand["y"] + half_h + 0.5) * grid)))
    if px0 > px1 or py0 > py1:
        return None

    inv_w, inv_h, inv_g = 1.0 / w, 1.0 / h, 1.0 / grid
    flip_sign = -1.0 if cand["flipped"] else 1.0
    opacity, lx, ly = cand["a"], cand["x"], cand["y"]
    ms = _MASK_SIZE

    s_ww = s_wur = s_wug = s_wub = 0.0
    s_uur = s_uug = s_uub = 0.0
    s_ddr = s_ddg = s_ddb = 0.0

    for py in range(py0, py1 + 1):
        cy = (py + 0.5) * inv_g - 0.5 - ly
        row = py * grid
        for px in range(px0, px1 + 1):
            cx = (px + 0.5) * inv_g - 0.5 - lx
            rx = (cx * cos_r - cy * sin_r) * flip_sign
            ry = cx * sin_r + cy * cos_r
            u = rx * inv_w + 0.5
            if u < 0.0 or u >= 1.0:
                continue
            v = ry * inv_h + 0.5
            if v < 0.0 or v >= 1.0:
                continue
            ma = mask[int(v * ms) * ms + int(u * ms)]
            if ma <= 0.01:
                continue
            idx = row + px
            trans = 1.0 - aa[idx]
            if trans <= 0.005:
                continue
            wi = trans * ma * opacity
            wp = weights[idx]

            b_r, b_g, b_b = br[idx], bg[idx], bb[idx]
            dr = tr[idx] - (ar[idx] + trans * b_r)
            dg = tg[idx] - (ag[idx] + trans * b_g)
            db = tb[idx] - (ab[idx] + trans * b_b)

            ur = dr + wi * b_r
            ug = dg + wi * b_g
            ub = db + wi * b_b

            w_wi = wp * wi
            s_ww += w_wi * wi
            s_wur += w_wi * ur
            s_wug += w_wi * ug
            s_wub += w_wi * ub
            s_uur += wp * ur * ur
            s_uug += wp * ug * ug
            s_uub += wp * ub * ub
            s_ddr += wp * dr * dr
            s_ddg += wp * dg * dg
            s_ddb += wp * db * db

    if s_ww < 0.12:
        return None

    opt_r = _clamp(s_wur / s_ww, 0.0, 255.0)
    opt_g = _clamp(s_wug / s_ww, 0.0, 255.0)
    opt_b = _clamp(s_wub / s_ww, 0.0, 255.0)

    delta = (
        WR * (s_ddr - (s_uur - 2.0 * opt_r * s_wur + opt_r * opt_r * s_ww))
        + WG * (s_ddg - (s_uug - 2.0 * opt_g * s_wug + opt_g * opt_g * s_ww))
        + WB * (s_ddb - (s_uub - 2.0 * opt_b * s_wub + opt_b * opt_b * s_ww))
    )
    return delta, opt_r / 255.0, opt_g / 255.0, opt_b / 255.0


def _composite_over(layer, mask, grid, cr, cg, cb):
    w, h = 2.0 ** layer["sx"], 2.0 ** layer["sy"]
    rad = -math.radians(layer["rot"])
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    hw = 0.5 * (abs(w * cos_r) + abs(h * sin_r))
    hh = 0.5 * (abs(w * sin_r) + abs(h * cos_r))
    px0 = max(0, int(math.floor((layer["x"] - hw + 0.5) * grid)))
    px1 = min(grid - 1, int(math.ceil((layer["x"] + hw + 0.5) * grid)))
    py0 = max(0, int(math.floor((layer["y"] - hh + 0.5) * grid)))
    py1 = min(grid - 1, int(math.ceil((layer["y"] + hh + 0.5) * grid)))
    inv_w, inv_h, inv_g = 1.0 / w, 1.0 / h, 1.0 / grid
    fs = -1.0 if layer["flipped"] else 1.0
    lr, lg, lb = layer["r"] * 255.0, layer["g"] * 255.0, layer["b"] * 255.0
    op, lx, ly = layer["a"], layer["x"], layer["y"]
    ms = _MASK_SIZE
    for py in range(py0, py1 + 1):
        cy, row = (py + 0.5) * inv_g - 0.5 - ly, py * grid
        for px in range(px0, px1 + 1):
            cx = (px + 0.5) * inv_g - 0.5 - lx
            u = ((cx * cos_r - cy * sin_r) * fs) * inv_w + 0.5
            if u < 0.0 or u >= 1.0:
                continue
            v = (cx * sin_r + cy * cos_r) * inv_h + 0.5
            if v < 0.0 or v >= 1.0:
                continue
            ma = mask[int(v * ms) * ms + int(u * ms)]
            if ma <= 0.01:
                continue
            a = ma * op
            ia = 1.0 - a
            idx = row + px
            cr[idx] = ia * cr[idx] + a * lr
            cg[idx] = ia * cg[idx] + a * lg
            cb[idx] = ia * cb[idx] + a * lb


def _composite_under_premul(layer, mask, grid, ar, ag, ab, aa):
    w, h = 2.0 ** layer["sx"], 2.0 ** layer["sy"]
    rad = -math.radians(layer["rot"])
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    hw = 0.5 * (abs(w * cos_r) + abs(h * sin_r))
    hh = 0.5 * (abs(w * sin_r) + abs(h * cos_r))
    px0 = max(0, int(math.floor((layer["x"] - hw + 0.5) * grid)))
    px1 = min(grid - 1, int(math.ceil((layer["x"] + hw + 0.5) * grid)))
    py0 = max(0, int(math.floor((layer["y"] - hh + 0.5) * grid)))
    py1 = min(grid - 1, int(math.ceil((layer["y"] + hh + 0.5) * grid)))
    inv_w, inv_h, inv_g = 1.0 / w, 1.0 / h, 1.0 / grid
    fs = -1.0 if layer["flipped"] else 1.0
    lr, lg, lb = layer["r"] * 255.0, layer["g"] * 255.0, layer["b"] * 255.0
    op, lx, ly = layer["a"], layer["x"], layer["y"]
    ms = _MASK_SIZE
    for py in range(py0, py1 + 1):
        cy, row = (py + 0.5) * inv_g - 0.5 - ly, py * grid
        for px in range(px0, px1 + 1):
            cx = (px + 0.5) * inv_g - 0.5 - lx
            u = ((cx * cos_r - cy * sin_r) * fs) * inv_w + 0.5
            if u < 0.0 or u >= 1.0:
                continue
            v = (cx * sin_r + cy * cos_r) * inv_h + 0.5
            if v < 0.0 or v >= 1.0:
                continue
            ma = mask[int(v * ms) * ms + int(u * ms)]
            if ma <= 0.01:
                continue
            idx = row + px
            trans = 1.0 - aa[idx]
            if trans <= 0.001:
                continue
            a = ma * op * trans
            ar[idx] += a * lr
            ag[idx] += a * lg
            ab[idx] += a * lb
            aa[idx] += a


def _weighted_error_cdf(tr, tg, tb, cr, cg, cb, weights):
    n = len(tr)
    cdf = [0.0] * n
    tot = raw = 0.0
    for i in range(n):
        dr, dg, db = tr[i] - cr[i], tg[i] - cg[i], tb[i] - cb[i]
        e = WR * dr * dr + WG * dg * dg + WB * db * db
        raw += e
        tot += weights[i] * e
        cdf[i] = tot
    return cdf, tot, math.sqrt(raw / (n * WSUM))


def _sample_pixel(cdf, total, rng, grid):
    if total <= 0.0:
        return rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5)
    target = rng.random() * total
    lo, hi = 0, len(cdf) - 1
    while lo < hi:
        mid = (lo + hi) >> 1
        if cdf[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    py, px = divmod(lo, grid)
    return (px + 0.5) / grid - 0.5, (py + 0.5) / grid - 0.5


def _refine(cand, res, usable, masks, grid, tr, tg, tb, weights, br, bg, bb, ar, ag, ab, aa, steps, rng, allow_alpha):
    cc, cr = dict(cand), res
    for step in range(steps):
        sc = 1.0 - 0.85 * (step / max(1, steps))
        nxt = dict(cc)
        mode = rng.randrange(7)
        if mode == 0:
            nxt["x"] = _clamp(nxt["x"] + rng.uniform(-0.14, 0.14) * sc, -0.58, 0.58)
            nxt["y"] = _clamp(nxt["y"] + rng.uniform(-0.14, 0.14) * sc, -0.58, 0.58)
        elif mode == 1:
            nxt["sx"] = _clamp(nxt["sx"] + rng.uniform(-0.45, 0.45) * sc, -3.6, 0.35)
            nxt["sy"] = _clamp(nxt["sy"] + rng.uniform(-0.45, 0.45) * sc, -3.6, 0.35)
        elif mode == 2:
            d = rng.uniform(-0.35, 0.35) * sc
            nxt["sx"] = _clamp(nxt["sx"] + d, -3.6, 0.35)
            nxt["sy"] = _clamp(nxt["sy"] + d, -3.6, 0.35)
        elif mode == 3:
            nxt["rot"] = (nxt["rot"] + rng.uniform(-45.0, 45.0) * sc) % 360.0
        elif mode == 4:
            nxt["shape"] = rng.choice(usable)
            if rng.random() < 0.35:
                nxt["flipped"] = not nxt["flipped"]
        elif mode == 5:
            nxt["x"] = _clamp(nxt["x"] + rng.uniform(-0.04, 0.04) * sc, -0.58, 0.58)
            nxt["y"] = _clamp(nxt["y"] + rng.uniform(-0.04, 0.04) * sc, -0.58, 0.58)
            nxt["sx"] = _clamp(nxt["sx"] + rng.uniform(-0.12, 0.12) * sc, -3.6, 0.35)
            nxt["sy"] = _clamp(nxt["sy"] + rng.uniform(-0.12, 0.12) * sc, -3.6, 0.35)
        else:
            nxt["rot"] = (nxt["rot"] + rng.uniform(-10.0, 10.0) * sc) % 360.0
            if allow_alpha:
                nxt["a"] = _clamp(nxt["a"] + rng.uniform(-0.2, 0.2) * sc, 0.25, 1.0)
        r = _eval_in_stack(nxt, masks[nxt["shape"]], grid, tr, tg, tb, weights, br, bg, bb, ar, ag, ab, aa)
        if r and r[0] > cr[0]:
            cc, cr = nxt, r
    return cc, cr


def _pca_region_candidates(tr, tg, tb, cr, cg, cb, weights, grid, core_usable, n_regions=3):
    """Segment highest-error coherent color regions via flood-fill + 2nd-order PCA moments."""
    n = grid * grid
    # Pick top error seeds spaced across the canvas
    errs = [
        (
            (WR * (tr[i] - cr[i]) ** 2 + WG * (tg[i] - cg[i]) ** 2 + WB * (tb[i] - cb[i]) ** 2)
            * weights[i],
            i,
        )
        for i in range(n)
    ]
    errs.sort(reverse=True)
    visited = bytearray(n)
    out = []
    found = 0
    for _, start in errs:
        if found >= n_regions:
            break
        if visited[start]:
            continue
        r0, g0, b0 = tr[start], tg[start], tb[start]
        stack = [start]
        visited[start] = 1
        comp = []
        while stack and len(comp) < n // 2:
            idx = stack.pop()
            comp.append(idx)
            y, x = divmod(idx, grid)
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < grid and 0 <= ny < grid:
                    ni = ny * grid + nx
                    if not visited[ni]:
                        dr = tr[ni] - r0
                        dg = tg[ni] - g0
                        db = tb[ni] - b0
                        if dr * dr + dg * dg + db * db < 38.0 * 38.0:
                            visited[ni] = 1
                            stack.append(ni)
        m = len(comp)
        if m < 4:
            continue
        found += 1
        cx = sum(i % grid for i in comp) / m
        cy = sum(i // grid for i in comp) / m
        mu20 = sum(((i % grid) - cx) ** 2 for i in comp) / m + 0.25
        mu02 = sum(((i // grid) - cy) ** 2 for i in comp) / m + 0.25
        mu11 = sum(((i % grid) - cx) * ((i // grid) - cy) for i in comp) / m
        tr_m = mu20 + mu02
        det = math.sqrt(max(0.0, (mu20 - mu02) ** 2 + 4.0 * mu11 * mu11))
        l1 = max(0.5, 0.5 * (tr_m + det))
        l2 = max(0.5, 0.5 * (tr_m - det))
        w = max(2.0, min(grid * 1.4, 3.5 * math.sqrt(l1)))
        h = max(2.0, min(grid * 1.4, 3.5 * math.sqrt(l2)))
        rot = 0.5 * math.degrees(math.atan2(2.0 * mu11, mu20 - mu02))
        nx_c = (cx + 0.5) / grid - 0.5
        ny_c = (cy + 0.5) / grid - 0.5
        sx = _clamp(math.log2(w / grid), -3.5, 0.35)
        sy = _clamp(math.log2(h / grid), -3.5, 0.35)
        for sid in core_usable:
            out.append({
                "shape": sid,
                "x": nx_c,
                "y": ny_c,
                "sx": sx,
                "sy": sy,
                "rot": rot,
                "a": 1.0,
                "flipped": False,
            })
    return out


def vectorize_image(
    image,
    *,
    max_layers=32,
    shape_set="geometric",
    quality="balanced",
    fit="cover",
    allow_alpha=True,
    global_passes=None,
    seed=1337,
):
    """Fit up to `max_layers` unlocked BO2 shapes to a PIL Image.

    Returns dict: {"layers": [...], "rmse": float, "similarity": float}.
    """
    preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["balanced"])
    grid = preset["grid"]
    passes = preset["global_passes"] if global_passes is None else max(0, int(global_passes))
    total_layers = max(2, min(NUM_LAYERS, int(max_layers)))

    shape_ids = _resolve_shape_ids(shape_set)
    masks = {}
    for sid in set([BG_SHAPE_ID] + shape_ids):
        m = _get_shape_mask(sid)
        if m is not None:
            masks[sid] = m
    usable = [sid for sid in shape_ids if sid in masks]
    if not usable:
        raise RuntimeError("No calibrated shape masks available for vectorization")
    core_usable = [sid for sid in SHAPE_SETS["basic"] if sid in masks] or usable

    tr, tg, tb = _prepare_target(image, grid, fit=fit)
    weights = _compute_saliency(tr, tg, tb, grid)
    n = grid * grid
    zeros = [0.0] * n

    # Layer 0: inset border ring (skips thin white scan margins) vs global mean
    mean_rgb = (sum(tr) / n, sum(tg) / n, sum(tb) / n)
    inset = max(2, grid // 14) if grid >= 28 else 0
    ring = [
        y * grid + x
        for y in range(inset, grid - inset)
        for x in range(inset, grid - inset)
        if x in (inset, grid - 1 - inset) or y in (inset, grid - 1 - inset)
    ]
    if ring:
        border_rgb = (
            sum(tr[i] for i in ring) / len(ring),
            sum(tg[i] for i in ring) / len(ring),
            sum(tb[i] for i in ring) / len(ring),
        )
        bvar = sum(
            (tr[i] - border_rgb[0]) ** 2 + (tg[i] - border_rgb[1]) ** 2 + (tb[i] - border_rgb[2]) ** 2
            for i in ring
        )
        bg_r, bg_g, bg_b = border_rgb if math.sqrt(bvar / (3.0 * len(ring))) < 28.0 else mean_rgb
    else:
        bg_r, bg_g, bg_b = mean_rgb

    cr, cg, cb = [bg_r] * n, [bg_g] * n, [bg_b] * n
    layers = [{
        "index": 0,
        "shape": BG_SHAPE_ID,
        "r": round(bg_r / 255.0, 4),
        "g": round(bg_g / 255.0, 4),
        "b": round(bg_b / 255.0, 4),
        "a": 1.0,
        "x": 0.0,
        "y": 0.0,
        "sx": 0.35,
        "sy": 0.35,
        "rot": 0.0,
        "outlined": False,
        "flipped": False,
    }]

    rng = random.Random(seed)
    # Phase 1: Saliency-weighted greedy build with top-2 beam refinement
    for li in range(1, total_layers):
        cdf, total, rmse = _weighted_error_cdf(tr, tg, tb, cr, cg, cb, weights)
        if rmse < 2.0:
            break
        progress = li / total_layers
        max_log = 0.1 - progress * 0.8
        min_log = -2.3 - progress * 1.1
        top = []
        # PCA connected-component region seeds first (directly targets coherent image blobs)
        for cand in _pca_region_candidates(tr, tg, tb, cr, cg, cb, weights, grid, core_usable):
            res = _eval_in_stack(
                cand, masks[cand["shape"]], grid, tr, tg, tb, weights,
                cr, cg, cb, zeros, zeros, zeros, zeros,
            )
            if res and res[0] > 0.0:
                if len(top) < 2:
                    top.append((res, cand))
                    top.sort(key=lambda item: item[0][0], reverse=True)
                elif res[0] > top[1][0][0]:
                    top[1] = (res, cand)
                    top.sort(key=lambda item: item[0][0], reverse=True)
        for _ in range(preset["candidates"]):
            px, py = _sample_pixel(cdf, total, rng, grid)
            sx = rng.uniform(min_log, max_log)
            sy = _clamp(sx + rng.uniform(-0.85, 0.85), -3.5, 0.3)
            rot = float(rng.choice((0, 90, 180, 270))) if rng.random() < 0.4 else float(rng.randrange(360))
            shape = rng.choice(core_usable) if rng.random() < 0.45 else rng.choice(usable)
            cand = {
                "shape": shape,
                "x": px,
                "y": py,
                "sx": sx,
                "sy": sy,
                "rot": rot,
                "a": rng.uniform(0.55, 0.9) if (allow_alpha and rng.random() < 0.2) else 1.0,
                "flipped": rng.random() < 0.25,
            }
            res = _eval_in_stack(cand, masks[shape], grid, tr, tg, tb, weights, cr, cg, cb, zeros, zeros, zeros, zeros)
            if res and res[0] > 0.0:
                if len(top) < 2:
                    top.append((res, cand))
                    top.sort(key=lambda item: item[0][0], reverse=True)
                elif res[0] > top[1][0][0]:
                    top[1] = (res, cand)
                    top.sort(key=lambda item: item[0][0], reverse=True)
        if not top:
            break
        best_c, best_r = _refine(
            top[0][1], top[0][0], usable, masks, grid, tr, tg, tb, weights,
            cr, cg, cb, zeros, zeros, zeros, zeros, preset["hill_steps"], rng, allow_alpha,
        )
        if len(top) > 1:
            c2, r2 = _refine(
                top[1][1], top[1][0], usable, masks, grid, tr, tg, tb, weights,
                cr, cg, cb, zeros, zeros, zeros, zeros, max(12, preset["hill_steps"] // 2), rng, allow_alpha,
            )
            if r2[0] > best_r[0]:
                best_c, best_r = c2, r2
        committed = {
            "index": len(layers),
            "shape": best_c["shape"],
            "r": round(best_r[1], 4),
            "g": round(best_r[2], 4),
            "b": round(best_r[3], 4),
            "a": round(best_c["a"], 4),
            "x": round(best_c["x"], 3),
            "y": round(best_c["y"], 3),
            "sx": round(best_c["sx"], 3),
            "sy": round(best_c["sy"], 3),
            "rot": round(best_c["rot"], 1),
            "outlined": False,
            "flipped": bool(best_c["flipped"]),
        }
        _composite_over(committed, masks[committed["shape"]], grid, cr, cg, cb)
        layers.append(committed)

    # Phase 2: Global associative multi-layer joint refinement
    refine_steps = max(25, int(preset["hill_steps"] * 0.7))
    for _ in range(passes):
        ln = len(layers)
        if ln <= 1:
            break
        ab_r, ab_g, ab_b, ab_a = [None] * ln, [None] * ln, [None] * ln, [None] * ln
        ar, ag, ab, aa = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
        for i in range(ln - 1, 0, -1):
            ab_r[i], ab_g[i], ab_b[i], ab_a[i] = ar[:], ag[:], ab[:], aa[:]
            _composite_under_premul(layers[i], masks[layers[i]["shape"]], grid, ar, ag, ab, aa)
        cr = [layers[0]["r"] * 255.0] * n
        cg = [layers[0]["g"] * 255.0] * n
        cb = [layers[0]["b"] * 255.0] * n
        for i in range(1, ln):
            cur = layers[i]
            cres = _eval_in_stack(
                cur, masks[cur["shape"]], grid, tr, tg, tb, weights,
                cr, cg, cb, ab_r[i], ab_g[i], ab_b[i], ab_a[i],
            )
            if cres is None:
                cres = (-1.0, cur["r"], cur["g"], cur["b"])
            bc, br_ = _refine(
                cur, cres, usable, masks, grid, tr, tg, tb, weights,
                cr, cg, cb, ab_r[i], ab_g[i], ab_b[i], ab_a[i], refine_steps, rng, allow_alpha,
            )
            layers[i] = {
                "index": i,
                "shape": bc["shape"],
                "r": round(br_[1], 4),
                "g": round(br_[2], 4),
                "b": round(br_[3], 4),
                "a": round(bc["a"], 4),
                "x": round(bc["x"], 3),
                "y": round(bc["y"], 3),
                "sx": round(bc["sx"], 3),
                "sy": round(bc["sy"], 3),
                "rot": round(bc["rot"], 1),
                "outlined": False,
                "flipped": bool(bc["flipped"]),
            }
            _composite_over(layers[i], masks[layers[i]["shape"]], grid, cr, cg, cb)

    _, _, final_rmse = _weighted_error_cdf(tr, tg, tb, cr, cg, cb, weights)
    similarity = round(_clamp((1.0 - final_rmse / 255.0) * 100.0, 0.0, 100.0), 2)
    return {
        "layers": layers,
        "rmse": round(final_rmse, 2),
        "similarity": similarity,
    }


def vectorize_image_bytes(raw_bytes, **kwargs):
    """Decode raw PNG/JPG/WebP bytes and vectorize into BO2 layers."""
    img = Image.open(io.BytesIO(raw_bytes))
    img.load()
    return vectorize_image(img, **kwargs)
