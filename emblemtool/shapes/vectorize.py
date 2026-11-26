"""Image-to-BO2-Emblem shape-fitting vectorizer (pure Python + Pillow).

Converts an arbitrary raster image into <= 32 Black Ops II emblem layers using:
  1. Optimal Layer-0 background fill (`Square Full`, shape 196).
  2. Perceptual weighted-RGB residual error map with importance sampling.
  3. Exact single-pass closed-form least-squares RGB color solve per candidate.
  4. Multi-scale hill-climbing refinement over (x, y, sx, sy, rot, alpha, flipped).

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
    "geometric": [192, 196, 144, 187, 194, 137, 138, 195, 183, 162, 140, 177, 180, 185],
}

QUALITY_PRESETS = {
    "fast": {"grid": 40, "candidates": 36, "hill_steps": 36},
    "balanced": {"grid": 48, "candidates": 56, "hill_steps": 56},
    "high": {"grid": 56, "candidates": 80, "hill_steps": 80},
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
    raw = small.tobytes()
    mask = [b / 255.0 for b in raw]
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

    # Estimate border color for contain letterboxing / transparent PNGs.
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
        resized = image.resize((grid, grid), Image.LANCZOS)
        canvas.alpha_composite(resized, (0, 0))
    elif fit == "contain":
        scale = min(grid / max(1, sw), grid / max(1, sh))
        dw = max(1, int(round(sw * scale)))
        dh = max(1, int(round(sh * scale)))
        resized = image.resize((dw, dh), Image.LANCZOS)
        canvas.alpha_composite(resized, ((grid - dw) // 2, (grid - dh) // 2))
    else:
        side = min(sw, sh)
        left = (sw - side) // 2
        top = (sh - side) // 2
        cropped = image.crop((left, top, left + side, top + side)).resize((grid, grid), Image.LANCZOS)
        canvas.alpha_composite(cropped, (0, 0))

    rgb = canvas.convert("RGB")
    data = rgb.tobytes()
    n = grid * grid
    tr = [float(data[i * 3]) for i in range(n)]
    tg = [float(data[i * 3 + 1]) for i in range(n)]
    tb = [float(data[i * 3 + 2]) for i in range(n)]
    return tr, tg, tb


def _evaluate_candidate(cand, mask, grid, tr, tg, tb, cr, cg, cb):
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

    inv_w = 1.0 / w
    inv_h = 1.0 / h
    inv_g = 1.0 / grid
    flip_sign = -1.0 if cand["flipped"] else 1.0
    opacity = cand["a"]
    lx, ly = cand["x"], cand["y"]
    ms = _MASK_SIZE

    s_aa = 0.0
    s_aur = s_aug = s_aub = 0.0
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
            alpha = ma * opacity
            idx = row + px

            br, bg, bb = cr[idx], cg[idx], cb[idx]
            dr = tr[idx] - br
            dg = tg[idx] - bg
            db = tb[idx] - bb

            ur = dr + alpha * br
            ug = dg + alpha * bg
            ub = db + alpha * bb

            s_aa += alpha * alpha
            s_aur += alpha * ur
            s_aug += alpha * ug
            s_aub += alpha * ub
            s_uur += ur * ur
            s_uug += ug * ug
            s_uub += ub * ub
            s_ddr += dr * dr
            s_ddg += dg * dg
            s_ddb += db * db

    if s_aa < 0.25:
        return None

    opt_r = _clamp(s_aur / s_aa, 0.0, 255.0)
    opt_g = _clamp(s_aug / s_aa, 0.0, 255.0)
    opt_b = _clamp(s_aub / s_aa, 0.0, 255.0)

    err_r = s_uur - 2.0 * opt_r * s_aur + opt_r * opt_r * s_aa
    err_g = s_uug - 2.0 * opt_g * s_aug + opt_g * opt_g * s_aa
    err_b = s_uub - 2.0 * opt_b * s_aub + opt_b * opt_b * s_aa

    delta = WR * (s_ddr - err_r) + WG * (s_ddg - err_g) + WB * (s_ddb - err_b)
    return delta, opt_r / 255.0, opt_g / 255.0, opt_b / 255.0


def _apply_layer(layer, mask, grid, cr, cg, cb):
    w = 2.0 ** layer["sx"]
    h = 2.0 ** layer["sy"]
    rad = -math.radians(layer["rot"])
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)
    half_w = 0.5 * (abs(w * cos_r) + abs(h * sin_r))
    half_h = 0.5 * (abs(w * sin_r) + abs(h * cos_r))

    px0 = max(0, int(math.floor((layer["x"] - half_w + 0.5) * grid)))
    px1 = min(grid - 1, int(math.ceil((layer["x"] + half_w + 0.5) * grid)))
    py0 = max(0, int(math.floor((layer["y"] - half_h + 0.5) * grid)))
    py1 = min(grid - 1, int(math.ceil((layer["y"] + half_h + 0.5) * grid)))

    inv_w = 1.0 / w
    inv_h = 1.0 / h
    inv_g = 1.0 / grid
    flip_sign = -1.0 if layer["flipped"] else 1.0
    lr, lg, lb = layer["r"] * 255.0, layer["g"] * 255.0, layer["b"] * 255.0
    opacity = layer["a"]
    lx, ly = layer["x"], layer["y"]
    ms = _MASK_SIZE

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
            alpha = ma * opacity
            inv_a = 1.0 - alpha
            idx = row + px
            cr[idx] = inv_a * cr[idx] + alpha * lr
            cg[idx] = inv_a * cg[idx] + alpha * lg
            cb[idx] = inv_a * cb[idx] + alpha * lb


def _error_cdf(tr, tg, tb, cr, cg, cb):
    n = len(tr)
    cdf = [0.0] * n
    total = 0.0
    for i in range(n):
        dr = tr[i] - cr[i]
        dg = tg[i] - cg[i]
        db = tb[i] - cb[i]
        total += WR * dr * dr + WG * dg * dg + WB * db * db
        cdf[i] = total
    rmse = math.sqrt(total / (n * WSUM))
    return cdf, total, rmse


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


def vectorize_image(
    image,
    *,
    max_layers=32,
    shape_set="geometric",
    quality="balanced",
    fit="cover",
    allow_alpha=True,
    seed=1337,
):
    """Fit up to `max_layers` unlocked BO2 shapes to a PIL Image.

    Returns dict: {"layers": [...], "rmse": float, "similarity": float}.
    """
    preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["balanced"])
    grid = preset["grid"]
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

    tr, tg, tb = _prepare_target(image, grid, fit=fit)
    n = grid * grid
    mean_rgb = (sum(tr) / n, sum(tg) / n, sum(tb) / n)
    # Use inset border ring (2px from edge) so thin scan margins don't skew border fill.
    inset = min(2, max(0, grid // 16))
    br = bg = bb = bc = 0.0
    for y in range(inset, grid - inset):
        for x in range(inset, grid - inset):
            if x not in (inset, grid - 1 - inset) and y not in (inset, grid - 1 - inset):
                continue
            i = y * grid + x
            br += tr[i]
            bg += tg[i]
            bb += tb[i]
            bc += 1
    border_rgb = (br / bc, bg / bc, bb / bc) if bc else mean_rgb
    bvar = 0.0
    for y in range(inset, grid - inset):
        for x in range(inset, grid - inset):
            if x not in (inset, grid - 1 - inset) and y not in (inset, grid - 1 - inset):
                continue
            i = y * grid + x
            bvar += (tr[i] - border_rgb[0]) ** 2 + (tg[i] - border_rgb[1]) ** 2 + (tb[i] - border_rgb[2]) ** 2
    bstd = math.sqrt(bvar / (3.0 * bc)) if bc else 999.0
    bg_r, bg_g, bg_b = border_rgb if bstd < 28.0 else mean_rgb
    cr = [bg_r] * n
    cg = [bg_g] * n
    cb = [bg_b] * n

    layers = [{
        "index": 0,
        "shape": BG_SHAPE_ID,
        "r": bg_r / 255.0,
        "g": bg_g / 255.0,
        "b": bg_b / 255.0,
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
    core_usable = [sid for sid in SHAPE_SETS["basic"] if sid in masks] or usable
    for li in range(1, total_layers):
        cdf, total, rmse = _error_cdf(tr, tg, tb, cr, cg, cb)
        if rmse < 2.5:
            break
        progress = li / total_layers
        max_log = 0.1 - progress * 0.75
        min_log = -2.2 - progress * 1.1

        top = []
        for _ in range(preset["candidates"]):
            px, py = _sample_pixel(cdf, total, rng, grid)
            sx = rng.uniform(min_log, max_log)
            sy = _clamp(sx + rng.uniform(-0.85, 0.85), -3.4, 0.3)
            rot = float(rng.choice((0, 90, 180, 270))) if rng.random() < 0.4 else float(rng.randrange(0, 360))
            shape = rng.choice(core_usable) if rng.random() < 0.45 else rng.choice(usable)
            cand = {
                "shape": shape,
                "x": px,
                "y": py,
                "sx": sx,
                "sy": sy,
                "rot": rot,
                "a": rng.uniform(0.55, 0.9) if (allow_alpha and rng.random() < 0.22) else 1.0,
                "flipped": rng.random() < 0.25,
            }
            res = _evaluate_candidate(cand, masks[shape], grid, tr, tg, tb, cr, cg, cb)
            if res and res[0] > 0.0:
                if len(top) < 2:
                    top.append((res, cand))
                    top.sort(key=lambda item: item[0][0], reverse=True)
                elif res[0] > top[-1][0][0]:
                    top[-1] = (res, cand)
                    top.sort(key=lambda item: item[0][0], reverse=True)

        if not top:
            break

        best_res, best_cand = top[0]
        for idx_t, (t_res, t_cand) in enumerate(top):
            cur_res, cur_cand = t_res, dict(t_cand)
            steps = preset["hill_steps"] if idx_t == 0 else max(12, preset["hill_steps"] // 2)
            for step in range(steps):
                scale = 1.0 - 0.82 * (step / steps)
                nxt = dict(cur_cand)
                mode = rng.randrange(6)
                if mode == 0:
                    nxt["x"] = _clamp(nxt["x"] + rng.uniform(-0.14, 0.14) * scale, -0.58, 0.58)
                    nxt["y"] = _clamp(nxt["y"] + rng.uniform(-0.14, 0.14) * scale, -0.58, 0.58)
                elif mode == 1:
                    nxt["sx"] = _clamp(nxt["sx"] + rng.uniform(-0.45, 0.45) * scale, -3.5, 0.35)
                    nxt["sy"] = _clamp(nxt["sy"] + rng.uniform(-0.45, 0.45) * scale, -3.5, 0.35)
                elif mode == 2:
                    d = rng.uniform(-0.35, 0.35) * scale
                    nxt["sx"] = _clamp(nxt["sx"] + d, -3.5, 0.35)
                    nxt["sy"] = _clamp(nxt["sy"] + d, -3.5, 0.35)
                elif mode == 3:
                    nxt["rot"] = (nxt["rot"] + rng.uniform(-45.0, 45.0) * scale) % 360.0
                elif mode == 4:
                    nxt["shape"] = rng.choice(usable)
                    if rng.random() < 0.35:
                        nxt["flipped"] = not nxt["flipped"]
                else:
                    nxt["x"] = _clamp(nxt["x"] + rng.uniform(-0.05, 0.05) * scale, -0.58, 0.58)
                    nxt["y"] = _clamp(nxt["y"] + rng.uniform(-0.05, 0.05) * scale, -0.58, 0.58)
                    nxt["rot"] = (nxt["rot"] + rng.uniform(-12.0, 12.0) * scale) % 360.0
                    if allow_alpha and rng.random() < 0.4:
                        nxt["a"] = _clamp(nxt["a"] + rng.uniform(-0.18, 0.18) * scale, 0.25, 1.0)

                res = _evaluate_candidate(nxt, masks[nxt["shape"]], grid, tr, tg, tb, cr, cg, cb)
                if res and res[0] > cur_res[0]:
                    cur_res, cur_cand = res, nxt
            if cur_res[0] > best_res[0]:
                best_res, best_cand = cur_res, cur_cand

        committed = {
            "index": len(layers),
            "shape": best_cand["shape"],
            "r": round(best_res[1], 4),
            "g": round(best_res[2], 4),
            "b": round(best_res[3], 4),
            "a": round(best_cand["a"], 4),
            "x": round(best_cand["x"], 3),
            "y": round(best_cand["y"], 3),
            "sx": round(best_cand["sx"], 3),
            "sy": round(best_cand["sy"], 3),
            "rot": round(best_cand["rot"], 1),
            "outlined": False,
            "flipped": bool(best_cand["flipped"]),
        }
        _apply_layer(committed, masks[committed["shape"]], grid, cr, cg, cb)
        layers.append(committed)

    _, _, final_rmse = _error_cdf(tr, tg, tb, cr, cg, cb)
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
