// Image-to-BO2-Emblem shape-fitting vectorizer.
//
// Converts any raster image into <= 32 Black Ops II emblem layers using:
//   1. Optimal background fill (Layer 0: Square Full).
//   2. Per-pixel perceptual error map + importance-sampled candidate shapes
//      drawn from the calibrated BO2 sprite atlas.
//   3. Exact single-pass closed-form least-squares optimal RGB color solve
//      per candidate placement (convex in R, G, B with [0, 255] clamping).
//   4. Multi-scale hill-climbing refinement over (x, y, sx, sy, rot, alpha, flipped).
//
// Runs 100% client-side in both the local desktop app and the static GitHub Pages demo.

export const SHAPE_SETS = {
  basic: [192, 196, 144, 187, 137],
  geometric: [192, 196, 144, 187, 194, 137, 138, 195, 183, 162, 140, 177, 180, 185],
  all_tools: null, // populated dynamically from shapes catalog (category === "tools")
};

export const QUALITY_PRESETS = {
  fast: { grid: 56, candidates: 70, hillSteps: 60 },
  balanced: { grid: 64, candidates: 110, hillSteps: 100 },
  high: { grid: 80, candidates: 160, hillSteps: 150 },
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
 * Downsample an HTMLImageElement / ImageBitmap / Canvas into a square GxG Float32 RGB buffer.
 */
export function prepareTargetPixels(source, grid, fit = "cover") {
  const c = document.createElement("canvas");
  c.width = grid;
  c.height = grid;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  const sw = source.naturalWidth || source.width;
  const sh = source.naturalHeight || source.height;

  // Estimate border background color for contain mode or transparent PNGs.
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

  if (fit === "stretch") {
    ctx.drawImage(source, 0, 0, grid, grid);
  } else if (fit === "contain") {
    const scale = Math.min(grid / sw, grid / sh);
    const dw = sw * scale;
    const dh = sh * scale;
    ctx.drawImage(source, (grid - dw) / 2, (grid - dh) / 2, dw, dh);
  } else {
    // cover: center-crop to square
    const side = Math.min(sw, sh);
    const sx = (sw - side) / 2;
    const sy = (sh - side) / 2;
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

/**
 * Extract normalized [0..1] alpha masks from the loaded BO2 sprite atlas for each shapeId.
 */
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
 * Evaluate a candidate layer in ONE pass over its rotated bounding box.
 * Computes exact closed-form optimal RGB color (clamped to [0, 255]) and exact perceptual error delta.
 */
function evaluateCandidate(cand, maskObj, target, current) {
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

  let sAA = 0;
  let sAuR = 0, sAuG = 0, sAuB = 0;
  let sUuR = 0, sUuG = 0, sUuB = 0;
  let sDdR = 0, sDdG = 0, sDdB = 0;

  const tR = target.r, tG = target.g, tB = target.b;
  const cR = current.r, cG = current.g, cB = current.b;

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
      const alpha = ma * opacity;
      const idx = row + px;

      const br = cR[idx], bg = cG[idx], bb = cB[idx];
      const dr = tR[idx] - br;
      const dg = tG[idx] - bg;
      const db = tB[idx] - bb;

      const ur = dr + alpha * br;
      const ug = dg + alpha * bg;
      const ub = db + alpha * bb;

      sAA += alpha * alpha;
      sAuR += alpha * ur;
      sAuG += alpha * ug;
      sAuB += alpha * ub;
      sUuR += ur * ur;
      sUuG += ug * ug;
      sUuB += ub * ub;
      sDdR += dr * dr;
      sDdG += dg * dg;
      sDdB += db * db;
    }
  }

  if (sAA < 0.25) return null;

  const optR = clamp(sAuR / sAA, 0, 255);
  const optG = clamp(sAuG / sAA, 0, 255);
  const optB = clamp(sAuB / sAA, 0, 255);

  const errAfterR = sUuR - 2 * optR * sAuR + optR * optR * sAA;
  const errAfterG = sUuG - 2 * optG * sAuG + optG * optG * sAA;
  const errAfterB = sUuB - 2 * optB * sAuB + optB * optB * sAA;

  const delta =
    WR * (sDdR - errAfterR) +
    WG * (sDdG - errAfterG) +
    WB * (sDdB - errAfterB);

  return {
    delta,
    r: optR / 255,
    g: optG / 255,
    b: optB / 255,
  };
}

/**
 * Composite a committed layer onto the working RGB buffer.
 */
function applyLayerToBuffer(layer, maskObj, current, grid) {
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

  const invW = 1 / w;
  const invH = 1 / h;
  const invG = 1 / grid;
  const flipSign = layer.flipped ? -1 : 1;
  const lr = layer.r * 255;
  const lg = layer.g * 255;
  const lb = layer.b * 255;
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

function buildErrorCDF(target, current) {
  const n = target.r.length;
  const cdf = new Float32Array(n);
  let total = 0;
  for (let i = 0; i < n; i++) {
    const dr = target.r[i] - current.r[i];
    const dg = target.g[i] - current.g[i];
    const db = target.b[i] - current.b[i];
    const e = WR * dr * dr + WG * dg * dg + WB * db * db;
    total += e;
    cdf[i] = total;
  }
  const rmse = Math.sqrt(total / (n * WSUM));
  return { cdf, total, rmse };
}

function sampleErrorPixel(cdf, total, rng, grid) {
  if (total <= 0) {
    return { x: (rng() - 0.5), y: (rng() - 0.5) };
  }
  const target = rng() * total;
  let lo = 0;
  let hi = cdf.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (cdf[mid] < target) lo = mid + 1;
    else hi = mid;
  }
  const py = (lo / grid) | 0;
  const px = lo - py * grid;
  return {
    x: (px + 0.5) / grid - 0.5,
    y: (py + 0.5) / grid - 0.5,
  };
}

function randomCandidate(shapeIds, coreIds, cdf, total, grid, progress, allowAlpha, rng) {
  const pt = sampleErrorPixel(cdf, total, rng, grid);
  // Early layers favor larger structural forms; later layers favor finer detail.
  const maxLog = 0.1 - progress * 0.75;
  const minLog = -2.2 - progress * 1.1;
  const sx = minLog + rng() * (maxLog - minLog);
  const aspectJitter = (rng() - 0.5) * 1.7;
  const sy = clamp(sx + aspectJitter, -3.4, 0.3);
  const pool = rng() < 0.45 ? coreIds : shapeIds;
  const shape = pool[(rng() * pool.length) | 0];
  const rot = rng() < 0.4 ? [0, 90, 180, 270][(rng() * 4) | 0] : Math.round(rng() * 360);
  const a = allowAlpha && rng() < 0.22 ? 0.55 + rng() * 0.35 : 1.0;
  const flipped = rng() < 0.25;
  return { shape, x: pt.x, y: pt.y, sx, sy, rot, a, flipped };
}

function mutateCandidate(base, shapeIds, stepScale, allowAlpha, rng) {
  const c = { ...base };
  const mode = (rng() * 6) | 0;
  if (mode === 0) {
    c.x = clamp(c.x + (rng() - 0.5) * 0.26 * stepScale, -0.58, 0.58);
    c.y = clamp(c.y + (rng() - 0.5) * 0.26 * stepScale, -0.58, 0.58);
  } else if (mode === 1) {
    c.sx = clamp(c.sx + (rng() - 0.5) * 0.9 * stepScale, -3.3, 0.3);
    c.sy = clamp(c.sy + (rng() - 0.5) * 0.9 * stepScale, -3.3, 0.3);
  } else if (mode === 2) {
    const d = (rng() - 0.5) * 0.7 * stepScale;
    c.sx = clamp(c.sx + d, -3.3, 0.3);
    c.sy = clamp(c.sy + d, -3.3, 0.3);
  } else if (mode === 3) {
    c.rot = ((c.rot + (rng() - 0.5) * 90 * stepScale) % 360 + 360) % 360;
  } else if (mode === 4) {
    c.shape = shapeIds[(rng() * shapeIds.length) | 0];
    if (rng() < 0.35) c.flipped = !c.flipped;
  } else {
    c.x = clamp(c.x + (rng() - 0.5) * 0.12 * stepScale, -0.58, 0.58);
    c.y = clamp(c.y + (rng() - 0.5) * 0.12 * stepScale, -0.58, 0.58);
    c.rot = ((c.rot + (rng() - 0.5) * 35 * stepScale) % 360 + 360) % 360;
    if (allowAlpha && rng() < 0.4) {
      c.a = clamp(c.a + (rng() - 0.5) * 0.35 * stepScale, 0.25, 1.0);
    }
  }
  return c;
}

/**
 * Convert an image element into up to `maxLayers` BO2 emblem layers.
 */
export async function vectorizeImage({
  source,
  atlas,
  shapes = [],
  maxLayers = 32,
  shapeSet = "geometric",
  quality = "balanced",
  fit = "cover",
  allowAlpha = true,
  seed = 1337,
  signal = null,
  onProgress = null,
}) {
  const preset = QUALITY_PRESETS[quality] || QUALITY_PRESETS.balanced;
  const grid = preset.grid;
  const totalLayers = clamp(Math.round(maxLayers), 2, 32);

  let shapeIds;
  if (shapeSet === "all_tools") {
    shapeIds = shapes.filter((s) => s.category === "tools").map((s) => s.id);
    if (!shapeIds.length) shapeIds = SHAPE_SETS.geometric;
  } else {
    shapeIds = SHAPE_SETS[shapeSet] || SHAPE_SETS.geometric;
  }

  const bank = buildMaskBank(atlas, [...new Set([BG_SHAPE_ID, ...shapeIds])]);
  const usableIds = shapeIds.filter((id) => bank.has(id));
  if (!usableIds.length) {
    throw new Error("Shape atlas is missing required primitive shapes");
  }

  const target = prepareTargetPixels(source, grid, fit);
  const n = grid * grid;

  // Compute Layer 0 background: prefer uniform inset border color when clean, else global mean.
  let sumR = 0, sumG = 0, sumB = 0;
  for (let i = 0; i < n; i++) {
    sumR += target.r[i];
    sumG += target.g[i];
    sumB += target.b[i];
  }
  const meanR = sumR / n, meanG = sumG / n, meanB = sumB / n;
  const inset = Math.min(2, Math.max(0, (grid / 16) | 0));
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

  const coreIds = SHAPE_SETS.basic.filter((id) => bank.has(id));
  const corePool = coreIds.length ? coreIds : usableIds;

  const current = {
    r: new Float32Array(n).fill(bgR),
    g: new Float32Array(n).fill(bgG),
    b: new Float32Array(n).fill(bgB),
  };

  const layers = [
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
  const initialStats = buildErrorCDF(target, current);
  if (onProgress) {
    onProgress({
      layerIndex: 1,
      totalLayers,
      layers: layers.slice(),
      rmse: initialStats.rmse,
      similarity: clamp((1 - initialStats.rmse / 255) * 100, 0, 100),
      targetCanvas: target.canvas,
    });
  }

  for (let li = 1; li < totalLayers; li++) {
    if (signal && signal.aborted) break;

    const { cdf, total, rmse } = buildErrorCDF(target, current);
    if (rmse < 2.5) break;

    const progress = li / totalLayers;

    // 1. Error-weighted random search (keep top 2 candidates for beam refinement)
    const top = [];
    for (let k = 0; k < preset.candidates; k++) {
      const cand = randomCandidate(usableIds, corePool, cdf, total, grid, progress, allowAlpha, rng);
      const res = evaluateCandidate(cand, bank.get(cand.shape), target, current);
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

    // 2. Multi-scale hill-climbing refinement on top 2 candidates
    let bestCand = top[0].cand;
    let bestEval = top[0].res;
    for (let ti = 0; ti < top.length; ti++) {
      let curCand = { ...top[ti].cand };
      let curEval = top[ti].res;
      const steps = ti === 0 ? preset.hillSteps : Math.max(20, (preset.hillSteps / 2) | 0);
      for (let step = 0; step < steps; step++) {
        const stepScale = 1.0 - 0.82 * (step / steps);
        const next = mutateCandidate(curCand, usableIds, stepScale, allowAlpha, rng);
        const res = evaluateCandidate(next, bank.get(next.shape), target, current);
        if (res && res.delta > curEval.delta) {
          curCand = next;
          curEval = res;
        }
      }
      if (curEval.delta > bestEval.delta) {
        bestCand = curCand;
        bestEval = curEval;
      }
    }

    const committed = {
      index: layers.length,
      shape: bestCand.shape,
      r: bestEval.r,
      g: bestEval.g,
      b: bestEval.b,
      a: bestCand.a,
      x: Math.round(bestCand.x * 1000) / 1000,
      y: Math.round(bestCand.y * 1000) / 1000,
      sx: Math.round(bestCand.sx * 1000) / 1000,
      sy: Math.round(bestCand.sy * 1000) / 1000,
      rot: Math.round(bestCand.rot * 10) / 10,
      outlined: false,
      flipped: !!bestCand.flipped,
    };

    applyLayerToBuffer(committed, bank.get(committed.shape), current, grid);
    layers.push(committed);

    const post = buildErrorCDF(target, current);
    if (onProgress) {
      onProgress({
        layerIndex: layers.length,
        totalLayers,
        layers: layers.slice(),
        rmse: post.rmse,
        similarity: clamp((1 - post.rmse / 255) * 100, 0, 100),
        targetCanvas: target.canvas,
      });
    }

    // Yield to the browser so the live preview stays smooth and Stop clicks respond immediately.
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  return layers;
}
