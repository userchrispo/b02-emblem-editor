// Global Multi-Layer Image-to-BO2-Emblem Vectorizer.
//
// Two-phase optimization running 100% client-side (local desktop app + GitHub Pages):
//   Phase 1 — Saliency-Weighted Greedy Build:
//     - Inset border-ring vs global-mean Layer-0 background fill.
//     - Per-pixel saliency weights combining Sobel edge magnitude + chroma saliency.
//     - Error-weighted importance sampling + top-2 beam hill climbing + exact
//       single-pass closed-form least-squares optimal RGB solver.
//   Phase 2 — Global Associative Multi-Layer Joint Refinement:
//     - Uses Porter-Duff source-over associativity (opaque below[i] + premultiplied
//       above[i]) so EVERY layer in the 32-layer stack can be re-optimized with exact
//       closed-form optimal RGB and exact global delta-error across all 32 layers.

export const SHAPE_SETS = {
  basic: [192, 196, 144, 187, 137],
  geometric: [
    192, 196, 144, 187, 194, 137, 138, 195, 180, 141,
    177, 162, 140, 178, 183,
  ],
  all_tools: null,
};

export const QUALITY_PRESETS = {
  fast: { grid: 56, candidates: 70, hillSteps: 60, globalPasses: 1 },
  balanced: { grid: 64, candidates: 100, hillSteps: 85, globalPasses: 3 },
  high: { grid: 76, candidates: 140, hillSteps: 110, globalPasses: 5 },
};

const BG_SHAPE_ID = 196; // Square Full (always unlocked)
const WR = 2.0;
const WG = 4.0;
const WB = 3.0;
const WSUM = WR + WG + WB;

function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

/**
 * Downsample source image into a square GxG Float32 RGB buffer with optional zoom/pan crop.
 */
export function prepareTargetPixels(source, grid, fit = "cover", crop = null) {
  const c = document.createElement("canvas");
  c.width = grid;
  c.height = grid;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  const sw = source.naturalWidth || source.width;
  const sh = source.naturalHeight || source.height;

  const probe = document.createElement("canvas");
  probe.width = 16;
  probe.height = 16;
  const pctx = probe.getContext("2d", { willReadFrequently: true });
  pctx.drawImage(source, 0, 0, 16, 16);
  const pdata = pctx.getImageData(0, 0, 16, 16).data;
  let br = 0, bg = 0, bb = 0, bc = 0;
  for (let y = 0; y < 16; y++) {
    for (let x = 0; x < 16; x++) {
      if (x !== 0 && x !== 15 && y !== 0 && y !== 15) continue;
      const i = (y * 16 + x) * 4;
      if (pdata[i + 3] < 16) continue;
      br += pdata[i];
      bg += pdata[i + 1];
      bb += pdata[i + 2];
      bc++;
    }
  }
  const fillR = bc ? Math.round(br / bc) : 24;
  const fillG = bc ? Math.round(bg / bc) : 24;
  const fillB = bc ? Math.round(bb / bc) : 24;
  ctx.fillStyle = `rgb(${fillR},${fillG},${fillB})`;
  ctx.fillRect(0, 0, grid, grid);

  const zoom = crop && crop.zoom ? clamp(crop.zoom, 1, 3) : 1;
  const panX = crop && crop.panX ? clamp(crop.panX, -0.5, 0.5) : 0;
  const panY = crop && crop.panY ? clamp(crop.panY, -0.5, 0.5) : 0;

  if (fit === "stretch") {
    ctx.drawImage(source, 0, 0, grid, grid);
  } else if (fit === "contain" && zoom <= 1.01) {
    const scale = Math.min(grid / sw, grid / sh);
    const dw = sw * scale;
    const dh = sh * scale;
    ctx.drawImage(source, (grid - dw) / 2, (grid - dh) / 2, dw, dh);
  } else {
    const side = Math.min(sw, sh) / zoom;
    const maxShiftX = Math.max(0, sw - side);
    const maxShiftY = Math.max(0, sh - side);
    const sx = clamp((sw - side) / 2 + panX * maxShiftX, 0, maxShiftX);
    const sy = clamp((sh - side) / 2 + panY * maxShiftY, 0, maxShiftY);
    ctx.drawImage(source, sx, sy, side, side, 0, 0, grid, grid);
  }

  const rgba = ctx.getImageData(0, 0, grid, grid).data;
  const n = grid * grid;
  const r = new Float32Array(n);
  const g = new Float32Array(n);
  const b = new Float32Array(n);
  for (let i = 0, p = 0; i < n; i++, p += 4) {
    r[i] = rgba[p];
    g[i] = rgba[p + 1];
    b[i] = rgba[p + 2];
  }
  return { r, g, b, grid, bgRgb: [fillR, fillG, fillB], canvas: c };
}

function computeSaliencyWeights(target, enabled = true) {
  const G = target.grid;
  const n = G * G;
  const weights = new Float32Array(n);
  if (!enabled) {
    weights.fill(1.0);
    return weights;
  }
  const lum = new Float32Array(n);
  const tR = target.r, tG = target.g, tB = target.b;
  for (let i = 0; i < n; i++) {
    lum[i] = 0.299 * tR[i] + 0.587 * tG[i] + 0.114 * tB[i];
  }
  for (let y = 0; y < G; y++) {
    const ym = y > 0 ? y - 1 : 0;
    const yp = y < G - 1 ? y + 1 : G - 1;
    for (let x = 0; x < G; x++) {
      const xm = x > 0 ? x - 1 : 0;
      const xp = x < G - 1 ? x + 1 : G - 1;
      const gx =
        -lum[ym * G + xm] + lum[ym * G + xp]
        - 2 * lum[y * G + xm] + 2 * lum[y * G + xp]
        - lum[yp * G + xm] + lum[yp * G + xp];
      const gy =
        -lum[ym * G + xm] - 2 * lum[ym * G + x] - lum[ym * G + xp]
        + lum[yp * G + xm] + 2 * lum[yp * G + x] + lum[yp * G + xp];
      const edge = Math.hypot(gx, gy) / 255;
      const i = y * G + x;
      const mx = Math.max(tR[i], tG[i], tB[i]);
      const mn = Math.min(tR[i], tG[i], tB[i]);
      const chroma = (mx - mn) / 255;
      const damp = (x < 2 || x >= G - 2 || y < 2 || y >= G - 2) ? 0.35 : 1.0;
      weights[i] = damp * (1.0 + 1.8 * Math.min(edge, 1.5) + 3.2 * chroma);
    }
  }
  return weights;
}

function buildMaskBank(atlas, shapeIds) {
  const cell = atlas.cell;
  const bank = new Map();
  for (const sid of shapeIds) {
    const tile = atlas.tiles[String(sid)];
    if (!tile) continue;
    const mask = new Float32Array(cell * cell);
    let nonZero = 0;
    for (let y = 0; y < cell; y++) {
      const rowOffset = (tile.y + y) * atlas.width + tile.x;
      const dstOffset = y * cell;
      for (let x = 0; x < cell; x++) {
        const a = atlas.pixels[(rowOffset + x) * 4 + 3] / 255;
        mask[dstOffset + x] = a;
        if (a > 0.05) nonZero++;
      }
    }
    if (nonZero > 4) {
      bank.set(sid, { id: sid, cell, mask });
    }
  }
  return bank;
}

/**
 * Exact single-pass global evaluation of `cand` sandwiched between:
 *   - below: opaque RGB (below.r, below.g, below.b)
 *   - above: premultiplied RGBA (above.r, above.g, above.b, above.a)
 */
function evaluateInStack(cand, maskObj, target, weights, below, above) {
  const G = target.grid;
  const cell = maskObj.cell;
  const mask = maskObj.mask;
  const w = Math.pow(2, cand.sx);
  const h = Math.pow(2, cand.sy);
  if (w < 0.02 || h < 0.02) return null;

  const rad = (-cand.rot * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const halfW = 0.5 * (Math.abs(w * cos) + Math.abs(h * sin));
  const halfH = 0.5 * (Math.abs(w * sin) + Math.abs(h * cos));

  const px0 = Math.max(0, Math.floor((cand.x - halfW + 0.5) * G));
  const px1 = Math.min(G - 1, Math.ceil((cand.x + halfW + 0.5) * G));
  const py0 = Math.max(0, Math.floor((cand.y - halfH + 0.5) * G));
  const py1 = Math.min(G - 1, Math.ceil((cand.y + halfH + 0.5) * G));
  if (px0 > px1 || py0 > py1) return null;

  const invW = 1 / w;
  const invH = 1 / h;
  const invG = 1 / G;
  const flipSign = cand.flipped ? -1 : 1;
  const opacity = cand.a;

  let sWW = 0;
  let sWuR = 0, sWuG = 0, sWuB = 0;
  let sUuR = 0, sUuG = 0, sUuB = 0;
  let sDdR = 0, sDdG = 0, sDdB = 0;

  const tR = target.r, tG = target.g, tB = target.b;
  const bR = below.r, bG = below.g, bB = below.b;
  const aR = above.r, aG = above.g, aB = above.b, aA = above.a;

  for (let py = py0; py <= py1; py++) {
    const cy = (py + 0.5) * invG - 0.5 - cand.y;
    const row = py * G;
    for (let px = px0; px <= px1; px++) {
      const cx = (px + 0.5) * invG - 0.5 - cand.x;
      const rx = (cx * cos - cy * sin) * flipSign;
      const ry = cx * sin + cy * cos;
      const u = rx * invW + 0.5;
      if (u < 0 || u >= 1) continue;
      const v = ry * invH + 0.5;
      if (v < 0 || v >= 1) continue;

      const ma = mask[((v * cell) | 0) * cell + ((u * cell) | 0)];
      if (ma <= 0.01) continue;
      const idx = row + px;
      const trans = 1 - aA[idx];
      if (trans <= 0.005) continue;

      const wi = trans * ma * opacity;
      const wp = weights[idx];
      const br = bR[idx], bg = bG[idx], bb = bB[idx];

      const dr = tR[idx] - (aR[idx] + trans * br);
      const dg = tG[idx] - (aG[idx] + trans * bg);
      const db = tB[idx] - (aB[idx] + trans * bb);

      const ur = dr + wi * br;
      const ug = dg + wi * bg;
      const ub = db + wi * bb;

      const wWi = wp * wi;
      sWW += wWi * wi;
      sWuR += wWi * ur;
      sWuG += wWi * ug;
      sWuB += wWi * ub;
      sUuR += wp * ur * ur;
      sUuG += wp * ug * ug;
      sUuB += wp * ub * ub;
      sDdR += wp * dr * dr;
      sDdG += wp * dg * dg;
      sDdB += wp * db * db;
    }
  }

  if (sWW < 0.12) return null;

  const optR = clamp(sWuR / sWW, 0, 255);
  const optG = clamp(sWuG / sWW, 0, 255);
  const optB = clamp(sWuB / sWW, 0, 255);

  const delta =
    WR * (sDdR - (sUuR - 2 * optR * sWuR + optR * optR * sWW)) +
    WG * (sDdG - (sUuG - 2 * optG * sWuG + optG * optG * sWW)) +
    WB * (sDdB - (sUuB - 2 * optB * sWuB + optB * optB * sWW));

  return { delta, r: optR / 255, g: optG / 255, b: optB / 255 };
}

function compositeLayerOver(layer, maskObj, current, grid) {
  const cell = maskObj.cell;
  const mask = maskObj.mask;
  const w = Math.pow(2, layer.sx);
  const h = Math.pow(2, layer.sy);
  const rad = (-layer.rot * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const halfW = 0.5 * (Math.abs(w * cos) + Math.abs(h * sin));
  const halfH = 0.5 * (Math.abs(w * sin) + Math.abs(h * cos));

  const px0 = Math.max(0, Math.floor((layer.x - halfW + 0.5) * grid));
  const px1 = Math.min(grid - 1, Math.ceil((layer.x + halfW + 0.5) * grid));
  const py0 = Math.max(0, Math.floor((layer.y - halfH + 0.5) * grid));
  const py1 = Math.min(grid - 1, Math.ceil((layer.y + halfH + 0.5) * grid));

  const invW = 1 / w, invH = 1 / h, invG = 1 / grid;
  const flipSign = layer.flipped ? -1 : 1;
  const lr = layer.r * 255, lg = layer.g * 255, lb = layer.b * 255;
  const opacity = layer.a;
  const cR = current.r, cG = current.g, cB = current.b;

  for (let py = py0; py <= py1; py++) {
    const cy = (py + 0.5) * invG - 0.5 - layer.y;
    const row = py * grid;
    for (let px = px0; px <= px1; px++) {
      const cx = (px + 0.5) * invG - 0.5 - layer.x;
      const rx = (cx * cos - cy * sin) * flipSign;
      const ry = cx * sin + cy * cos;
      const u = rx * invW + 0.5;
      if (u < 0 || u >= 1) continue;
      const v = ry * invH + 0.5;
      if (v < 0 || v >= 1) continue;
      const ma = mask[((v * cell) | 0) * cell + ((u * cell) | 0)];
      if (ma <= 0.01) continue;
      const alpha = ma * opacity;
      const invA = 1 - alpha;
      const idx = row + px;
      cR[idx] = invA * cR[idx] + alpha * lr;
      cG[idx] = invA * cG[idx] + alpha * lg;
      cB[idx] = invA * cB[idx] + alpha * lb;
    }
  }
}

function compositeLayerUnderPremul(layer, maskObj, above, grid) {
  const cell = maskObj.cell;
  const mask = maskObj.mask;
  const w = Math.pow(2, layer.sx);
  const h = Math.pow(2, layer.sy);
  const rad = (-layer.rot * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const halfW = 0.5 * (Math.abs(w * cos) + Math.abs(h * sin));
  const halfH = 0.5 * (Math.abs(w * sin) + Math.abs(h * cos));

  const px0 = Math.max(0, Math.floor((layer.x - halfW + 0.5) * grid));
  const px1 = Math.min(grid - 1, Math.ceil((layer.x + halfW + 0.5) * grid));
  const py0 = Math.max(0, Math.floor((layer.y - halfH + 0.5) * grid));
  const py1 = Math.min(grid - 1, Math.ceil((layer.y + halfH + 0.5) * grid));

  const invW = 1 / w, invH = 1 / h, invG = 1 / grid;
  const flipSign = layer.flipped ? -1 : 1;
  const lr = layer.r * 255, lg = layer.g * 255, lb = layer.b * 255;
  const opacity = layer.a;
  const aR = above.r, aG = above.g, aB = above.b, aA = above.a;

  for (let py = py0; py <= py1; py++) {
    const cy = (py + 0.5) * invG - 0.5 - layer.y;
    const row = py * grid;
    for (let px = px0; px <= px1; px++) {
      const cx = (px + 0.5) * invG - 0.5 - layer.x;
      const rx = (cx * cos - cy * sin) * flipSign;
      const ry = cx * sin + cy * cos;
      const u = rx * invW + 0.5;
      if (u < 0 || u >= 1) continue;
      const v = ry * invH + 0.5;
      if (v < 0 || v >= 1) continue;
      const ma = mask[((v * cell) | 0) * cell + ((u * cell) | 0)];
      if (ma <= 0.01) continue;
      const idx = row + px;
      const trans = 1 - aA[idx];
      if (trans <= 0.001) continue;
      const a = ma * opacity * trans;
      aR[idx] += a * lr;
      aG[idx] += a * lg;
      aB[idx] += a * lb;
      aA[idx] += a;
    }
  }
}

function buildWeightedErrorCDF(target, current, weights) {
  const n = target.r.length;
  const cdf = new Float32Array(n);
  let total = 0;
  let rawSq = 0;
  for (let i = 0; i < n; i++) {
    const dr = target.r[i] - current.r[i];
    const dg = target.g[i] - current.g[i];
    const db = target.b[i] - current.b[i];
    const e = WR * dr * dr + WG * dg * dg + WB * db * db;
    rawSq += e;
    total += weights[i] * e;
    cdf[i] = total;
  }
  const rmse = Math.sqrt(rawSq / (n * WSUM));
  return { cdf, total, rmse };
}

function sampleErrorPixel(cdf, total, rng, grid) {
  if (total <= 0) return { x: rng() - 0.5, y: rng() - 0.5 };
  const target = rng() * total;
  let lo = 0, hi = cdf.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (cdf[mid] < target) lo = mid + 1;
    else hi = mid;
  }
  const py = (lo / grid) | 0;
  const px = lo - py * grid;
  return { x: (px + 0.5) / grid - 0.5, y: (py + 0.5) / grid - 0.5 };
}

function randomCandidate(shapeIds, coreIds, cdf, total, grid, progress, allowAlpha, rng) {
  const pt = sampleErrorPixel(cdf, total, rng, grid);
  const maxLog = 0.1 - progress * 0.8;
  const minLog = -2.3 - progress * 1.1;
  const sx = minLog + rng() * (maxLog - minLog);
  const sy = clamp(sx + (rng() - 0.5) * 1.7, -3.5, 0.3);
  const pool = rng() < 0.45 ? coreIds : shapeIds;
  const shape = pool[(rng() * pool.length) | 0];
  const rot = rng() < 0.4 ? [0, 90, 180, 270][(rng() * 4) | 0] : Math.round(rng() * 360);
  const a = allowAlpha && rng() < 0.2 ? 0.55 + rng() * 0.35 : 1.0;
  return { shape, x: pt.x, y: pt.y, sx, sy, rot, a, flipped: rng() < 0.25 };
}

function mutateCandidate(base, shapeIds, stepScale, allowAlpha, rng) {
  const c = { ...base };
  const mode = (rng() * 7) | 0;
  if (mode === 0) {
    c.x = clamp(c.x + (rng() - 0.5) * 0.28 * stepScale, -0.58, 0.58);
    c.y = clamp(c.y + (rng() - 0.5) * 0.28 * stepScale, -0.58, 0.58);
  } else if (mode === 1) {
    c.sx = clamp(c.sx + (rng() - 0.5) * 0.9 * stepScale, -3.6, 0.35);
    c.sy = clamp(c.sy + (rng() - 0.5) * 0.9 * stepScale, -3.6, 0.35);
  } else if (mode === 2) {
    const d = (rng() - 0.5) * 0.7 * stepScale;
    c.sx = clamp(c.sx + d, -3.6, 0.35);
    c.sy = clamp(c.sy + d, -3.6, 0.35);
  } else if (mode === 3) {
    c.rot = ((c.rot + (rng() - 0.5) * 90 * stepScale) % 360 + 360) % 360;
  } else if (mode === 4) {
    c.shape = shapeIds[(rng() * shapeIds.length) | 0];
    if (rng() < 0.35) c.flipped = !c.flipped;
  } else if (mode === 5) {
    c.x = clamp(c.x + (rng() - 0.5) * 0.08 * stepScale, -0.58, 0.58);
    c.y = clamp(c.y + (rng() - 0.5) * 0.08 * stepScale, -0.58, 0.58);
    c.sx = clamp(c.sx + (rng() - 0.5) * 0.24 * stepScale, -3.6, 0.35);
    c.sy = clamp(c.sy + (rng() - 0.5) * 0.24 * stepScale, -3.6, 0.35);
  } else {
    c.rot = ((c.rot + (rng() - 0.5) * 20 * stepScale) % 360 + 360) % 360;
    if (allowAlpha) {
      c.a = clamp(c.a + (rng() - 0.5) * 0.35 * stepScale, 0.25, 1.0);
    }
  }
  return c;
}

function refineOne(cand, res, usableIds, bank, target, weights, below, above, steps, allowAlpha, rng) {
  let curCand = { ...cand };
  let curEval = res;
  for (let step = 0; step < steps; step++) {
    const stepScale = 1.0 - 0.85 * (step / Math.max(1, steps));
    const next = mutateCandidate(curCand, usableIds, stepScale, allowAlpha, rng);
    const r = evaluateInStack(next, bank.get(next.shape), target, weights, below, above);
    if (r && r.delta > curEval.delta) {
      curCand = next;
      curEval = r;
    }
  }
  return { cand: curCand, res: curEval };
}

function resolveShapeIds(shapeSet, shapes) {
  if (shapeSet === "all_tools") {
    const ids = shapes.filter((s) => s.category === "tools").map((s) => s.id);
    if (ids.length) return ids;
  }
  return SHAPE_SETS[shapeSet] || SHAPE_SETS.geometric;
}

async function runGlobalPasses({
  layers,
  passes,
  refineSteps,
  usableIds,
  bank,
  target,
  weights,
  allowAlpha,
  rng,
  signal,
  onPassProgress,
}) {
  const G = target.grid;
  const n = G * G;
  const out = layers.map((L) => ({ ...L }));

  for (let p = 0; p < passes; p++) {
    if (signal && signal.aborted) break;
    const len = out.length;
    if (len <= 1) break;

    const aboveList = new Array(len);
    const curAbove = {
      r: new Float32Array(n),
      g: new Float32Array(n),
      b: new Float32Array(n),
      a: new Float32Array(n),
    };
    for (let i = len - 1; i >= 1; i--) {
      aboveList[i] = {
        r: curAbove.r.slice(),
        g: curAbove.g.slice(),
        b: curAbove.b.slice(),
        a: curAbove.a.slice(),
      };
      const m = bank.get(out[i].shape);
      if (m) compositeLayerUnderPremul(out[i], m, curAbove, G);
    }

    const below = {
      r: new Float32Array(n).fill(out[0].r * 255),
      g: new Float32Array(n).fill(out[0].g * 255),
      b: new Float32Array(n).fill(out[0].b * 255),
    };

    for (let i = 1; i < len; i++) {
      if (signal && signal.aborted) break;
      const curL = out[i];
      const mask = bank.get(curL.shape);
      if (!mask) continue;
      let curRes = evaluateInStack(curL, mask, target, weights, below, aboveList[i]);
      if (!curRes) curRes = { delta: -1, r: curL.r, g: curL.g, b: curL.b };
      const best = refineOne(
        curL, curRes, usableIds, bank, target, weights,
        below, aboveList[i], refineSteps, allowAlpha, rng
      );
      out[i] = {
        index: i,
        shape: best.cand.shape,
        r: best.res.r,
        g: best.res.g,
        b: best.res.b,
        a: best.cand.a,
        x: Math.round(best.cand.x * 1000) / 1000,
        y: Math.round(best.cand.y * 1000) / 1000,
        sx: Math.round(best.cand.sx * 1000) / 1000,
        sy: Math.round(best.cand.sy * 1000) / 1000,
        rot: Math.round(best.cand.rot * 10) / 10,
        outlined: false,
        flipped: !!best.cand.flipped,
      };
      compositeLayerOver(out[i], bank.get(out[i].shape), below, G);
    }

    const stats = buildWeightedErrorCDF(target, below, weights);
    if (onPassProgress) {
      onPassProgress({
        passIndex: p + 1,
        totalPasses: passes,
        layers: out.slice(),
        rmse: stats.rmse,
        similarity: clamp((1 - stats.rmse / 255) * 100, 0, 100),
      });
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  return out;
}

/**
 * Convert an image element into up to `maxLayers` BO2 emblem layers with global joint refinement.
 */
function pcaRegionCandidates(target, current, weights, grid, corePool, nRegions = 3) {
  const n = grid * grid;
  const order = new Int32Array(n);
  const errs = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    order[i] = i;
    const dr = target.r[i] - current.r[i];
    const dg = target.g[i] - current.g[i];
    const db = target.b[i] - current.b[i];
    errs[i] = (WR * dr * dr + WG * dg * dg + WB * db * db) * weights[i];
  }
  // Pick top-K candidate starts by partial scan (fast O(n))
  const visited = new Uint8Array(n);
  const out = [];
  const stack = new Int32Array(n);
  const comp = new Int32Array(n);
  const maxComp = n >> 1;

  for (let reg = 0; reg < nRegions; reg++) {
    let bestIdx = -1;
    let bestErr = -1;
    for (let i = 0; i < n; i++) {
      if (!visited[i] && errs[i] > bestErr) {
        bestErr = errs[i];
        bestIdx = i;
      }
    }
    if (bestIdx < 0 || bestErr <= 0) break;

    const r0 = target.r[bestIdx];
    const g0 = target.g[bestIdx];
    const b0 = target.b[bestIdx];
    let sp = 0;
    let cp = 0;
    stack[sp++] = bestIdx;
    visited[bestIdx] = 1;

    while (sp > 0 && cp < maxComp) {
      const idx = stack[--sp];
      comp[cp++] = idx;
      const x = idx % grid;
      const y = (idx - x) / grid;
      if (x > 0) {
        const ni = idx - 1;
        if (!visited[ni]) {
          const dr = target.r[ni] - r0, dg = target.g[ni] - g0, db = target.b[ni] - b0;
          if (dr * dr + dg * dg + db * db < 1444) { visited[ni] = 1; stack[sp++] = ni; }
        }
      }
      if (x + 1 < grid) {
        const ni = idx + 1;
        if (!visited[ni]) {
          const dr = target.r[ni] - r0, dg = target.g[ni] - g0, db = target.b[ni] - b0;
          if (dr * dr + dg * dg + db * db < 1444) { visited[ni] = 1; stack[sp++] = ni; }
        }
      }
      if (y > 0) {
        const ni = idx - grid;
        if (!visited[ni]) {
          const dr = target.r[ni] - r0, dg = target.g[ni] - g0, db = target.b[ni] - b0;
          if (dr * dr + dg * dg + db * db < 1444) { visited[ni] = 1; stack[sp++] = ni; }
        }
      }
      if (y + 1 < grid) {
        const ni = idx + grid;
        if (!visited[ni]) {
          const dr = target.r[ni] - r0, dg = target.g[ni] - g0, db = target.b[ni] - b0;
          if (dr * dr + dg * dg + db * db < 1444) { visited[ni] = 1; stack[sp++] = ni; }
        }
      }
    }

    if (cp < 4) continue;
    let sumX = 0, sumY = 0;
    for (let k = 0; k < cp; k++) {
      const x = comp[k] % grid;
      sumX += x;
      sumY += (comp[k] - x) / grid;
    }
    const cx = sumX / cp;
    const cy = sumY / cp;
    let mu20 = 0, mu02 = 0, mu11 = 0;
    for (let k = 0; k < cp; k++) {
      const x = comp[k] % grid;
      const y = (comp[k] - x) / grid;
      const dx = x - cx;
      const dy = y - cy;
      mu20 += dx * dx;
      mu02 += dy * dy;
      mu11 += dx * dy;
    }
    mu20 = mu20 / cp + 0.25;
    mu02 = mu02 / cp + 0.25;
    mu11 = mu11 / cp;
    const tr = mu20 + mu02;
    const det = Math.sqrt(Math.max(0, (mu20 - mu02) * (mu20 - mu02) + 4 * mu11 * mu11));
    const l1 = Math.max(0.5, 0.5 * (tr + det));
    const l2 = Math.max(0.5, 0.5 * (tr - det));
    const w = Math.max(2, Math.min(grid * 1.4, 3.5 * Math.sqrt(l1)));
    const h = Math.max(2, Math.min(grid * 1.4, 3.5 * Math.sqrt(l2)));
    const rot = (0.5 * Math.atan2(2 * mu11, mu20 - mu02) * 180) / Math.PI;
    const nx = (cx + 0.5) / grid - 0.5;
    const ny = (cy + 0.5) / grid - 0.5;
    const sx = clamp(Math.log2(w / grid), -3.5, 0.35);
    const sy = clamp(Math.log2(h / grid), -3.5, 0.35);

    for (const sid of corePool) {
      out.push({
        shape: sid,
        x: nx,
        y: ny,
        sx,
        sy,
        rot,
        a: 1.0,
        flipped: false,
      });
    }
  }
  return out;
}

export async function vectorizeImage({
  source,
  atlas,
  shapes = [],
  maxLayers = 32,
  shapeSet = "geometric",
  quality = "balanced",
  fit = "cover",
  crop = null,
  saliency = true,
  allowAlpha = true,
  seed = 1337,
  signal = null,
  onProgress = null,
}) {
  const preset = QUALITY_PRESETS[quality] || QUALITY_PRESETS.balanced;
  const grid = preset.grid;
  const totalLayers = clamp(Math.round(maxLayers), 2, 32);
  const shapeIds = resolveShapeIds(shapeSet, shapes);

  const bank = buildMaskBank(atlas, [...new Set([BG_SHAPE_ID, ...shapeIds])]);
  const usableIds = shapeIds.filter((id) => bank.has(id));
  if (!usableIds.length) throw new Error("Shape atlas is missing required primitive shapes");
  const coreIds = SHAPE_SETS.basic.filter((id) => bank.has(id));
  const corePool = coreIds.length ? coreIds : usableIds;

  const target = prepareTargetPixels(source, grid, fit, crop);
  const weights = computeSaliencyWeights(target, saliency);
  const n = grid * grid;
  const emptyAbove = {
    r: new Float32Array(n),
    g: new Float32Array(n),
    b: new Float32Array(n),
    a: new Float32Array(n),
  };

  // Layer 0: inset border ring vs global mean
  let sumR = 0, sumG = 0, sumB = 0;
  for (let i = 0; i < n; i++) {
    sumR += target.r[i]; sumG += target.g[i]; sumB += target.b[i];
  }
  const meanR = sumR / n, meanG = sumG / n, meanB = sumB / n;
  const inset = grid >= 28 ? Math.max(2, (grid / 14) | 0) : 0;
  let br = 0, bg = 0, bb = 0, bc = 0;
  for (let y = inset; y < grid - inset; y++) {
    for (let x = inset; x < grid - inset; x++) {
      if (x !== inset && x !== grid - 1 - inset && y !== inset && y !== grid - 1 - inset) continue;
      const i = y * grid + x;
      br += target.r[i]; bg += target.g[i]; bb += target.b[i]; bc++;
    }
  }
  const borderR = bc ? br / bc : meanR;
  const borderG = bc ? bg / bc : meanG;
  const borderB = bc ? bb / bc : meanB;
  let bvar = 0;
  for (let y = inset; y < grid - inset; y++) {
    for (let x = inset; x < grid - inset; x++) {
      if (x !== inset && x !== grid - 1 - inset && y !== inset && y !== grid - 1 - inset) continue;
      const i = y * grid + x;
      bvar += (target.r[i] - borderR) ** 2 + (target.g[i] - borderG) ** 2 + (target.b[i] - borderB) ** 2;
    }
  }
  const bstd = bc ? Math.sqrt(bvar / (3 * bc)) : 999;
  const bgR = bstd < 28 ? borderR : meanR;
  const bgG = bstd < 28 ? borderG : meanG;
  const bgB = bstd < 28 ? borderB : meanB;

  const current = {
    r: new Float32Array(n).fill(bgR),
    g: new Float32Array(n).fill(bgG),
    b: new Float32Array(n).fill(bgB),
  };

  let layers = [
    {
      index: 0,
      shape: BG_SHAPE_ID,
      r: bgR / 255,
      g: bgG / 255,
      b: bgB / 255,
      a: 1.0,
      x: 0.0,
      y: 0.0,
      sx: 0.35,
      sy: 0.35,
      rot: 0.0,
      outlined: false,
      flipped: false,
    },
  ];

  const rng = mulberry32(seed);
  const initStats = buildWeightedErrorCDF(target, current, weights);
  if (onProgress) {
    onProgress({
      phase: "build",
      layerIndex: 1,
      totalLayers,
      layers: layers.slice(),
      rmse: initStats.rmse,
      similarity: clamp((1 - initStats.rmse / 255) * 100, 0, 100),
      targetCanvas: target.canvas,
    });
  }

  // Phase 1: Saliency-weighted greedy build
  for (let li = 1; li < totalLayers; li++) {
    if (signal && signal.aborted) break;
    const { cdf, total, rmse } = buildWeightedErrorCDF(target, current, weights);
    if (rmse < 2.0) break;

    const progress = li / totalLayers;
    const top = [];
    const pcaSeeds = pcaRegionCandidates(target, current, weights, grid, corePool, 3);
    for (const cand of pcaSeeds) {
      const res = evaluateInStack(cand, bank.get(cand.shape), target, weights, current, emptyAbove);
      if (res && res.delta > 0) {
        if (top.length < 2) {
          top.push({ cand, res });
          top.sort((a, b) => b.res.delta - a.res.delta);
        } else if (res.delta > top[1].res.delta) {
          top[1] = { cand, res };
          top.sort((a, b) => b.res.delta - a.res.delta);
        }
      }
    }
    for (let k = 0; k < preset.candidates; k++) {
      const cand = randomCandidate(usableIds, corePool, cdf, total, grid, progress, allowAlpha, rng);
      const res = evaluateInStack(cand, bank.get(cand.shape), target, weights, current, emptyAbove);
      if (res && res.delta > 0) {
        if (top.length < 2) {
          top.push({ cand, res });
          top.sort((a, b) => b.res.delta - a.res.delta);
        } else if (res.delta > top[1].res.delta) {
          top[1] = { cand, res };
          top.sort((a, b) => b.res.delta - a.res.delta);
        }
      }
    }
    if (!top.length) break;

    let best = refineOne(
      top[0].cand, top[0].res, usableIds, bank, target, weights,
      current, emptyAbove, preset.hillSteps, allowAlpha, rng
    );
    if (top.length > 1) {
      const alt = refineOne(
        top[1].cand, top[1].res, usableIds, bank, target, weights,
        current, emptyAbove, Math.max(20, (preset.hillSteps / 2) | 0), allowAlpha, rng
      );
      if (alt.res.delta > best.res.delta) best = alt;
    }

    const committed = {
      index: layers.length,
      shape: best.cand.shape,
      r: best.res.r,
      g: best.res.g,
      b: best.res.b,
      a: best.cand.a,
      x: Math.round(best.cand.x * 1000) / 1000,
      y: Math.round(best.cand.y * 1000) / 1000,
      sx: Math.round(best.cand.sx * 1000) / 1000,
      sy: Math.round(best.cand.sy * 1000) / 1000,
      rot: Math.round(best.cand.rot * 10) / 10,
      outlined: false,
      flipped: !!best.cand.flipped,
    };
    compositeLayerOver(committed, bank.get(committed.shape), current, grid);
    layers.push(committed);

    const post = buildWeightedErrorCDF(target, current, weights);
    if (onProgress) {
      onProgress({
        phase: "build",
        layerIndex: layers.length,
        totalLayers,
        layers: layers.slice(),
        rmse: post.rmse,
        similarity: clamp((1 - post.rmse / 255) * 100, 0, 100),
        targetCanvas: target.canvas,
      });
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  // Phase 2: Global associative multi-layer joint refinement
  if (!(signal && signal.aborted) && preset.globalPasses > 0 && layers.length > 1) {
    layers = await runGlobalPasses({
      layers,
      passes: preset.globalPasses,
      refineSteps: Math.max(30, Math.round(preset.hillSteps * 0.7)),
      usableIds,
      bank,
      target,
      weights,
      allowAlpha,
      rng,
      signal,
      onPassProgress: (info) => {
        if (onProgress) {
          onProgress({
            phase: "refine",
            layerIndex: layers.length,
            totalLayers,
            passIndex: info.passIndex,
            totalPasses: info.totalPasses,
            layers: info.layers,
            rmse: info.rmse,
            similarity: info.similarity,
            targetCanvas: target.canvas,
          });
        }
      },
    });
  }

  return layers;
}

/**
 * Run additional global associative refinement passes on an existing layer stack.
 */
export async function refineExistingLayers({
  layers,
  source,
  atlas,
  shapes = [],
  shapeSet = "geometric",
  quality = "balanced",
  fit = "cover",
  crop = null,
  saliency = true,
  allowAlpha = true,
  passes = 2,
  seed = Date.now(),
  signal = null,
  onProgress = null,
}) {
  const preset = QUALITY_PRESETS[quality] || QUALITY_PRESETS.balanced;
  const shapeIds = resolveShapeIds(shapeSet, shapes);
  const allIds = [...new Set([BG_SHAPE_ID, ...shapeIds, ...layers.map((L) => L.shape)])];
  const bank = buildMaskBank(atlas, allIds);
  const usableIds = shapeIds.filter((id) => bank.has(id));
  const target = prepareTargetPixels(source, preset.grid, fit, crop);
  const weights = computeSaliencyWeights(target, saliency);
  const rng = mulberry32(seed);

  return runGlobalPasses({
    layers,
    passes,
    refineSteps: Math.max(40, Math.round(preset.hillSteps * 0.85)),
    usableIds,
    bank,
    target,
    weights,
    allowAlpha,
    rng,
    signal,
    onPassProgress: (info) => {
      if (onProgress) {
        onProgress({
          phase: "refine",
          layerIndex: layers.length,
          totalLayers: layers.length,
          passIndex: info.passIndex,
          totalPasses: info.totalPasses,
          layers: info.layers,
          rmse: info.rmse,
          similarity: info.similarity,
          targetCanvas: target.canvas,
        });
      }
    },
  });
}
