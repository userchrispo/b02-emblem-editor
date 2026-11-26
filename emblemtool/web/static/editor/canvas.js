// Canvas rendering, LRU glyph tinting, pixel-accurate picking,
// on-canvas resize/rotate handles, and center/grid snapping.
import { fetchAtlasMeta } from "./api.js";

export const ALPHA_MIN = 12;
const TINT_CACHE_MAX = 200;
const tintCache = new Map();

export function rgbHex(L) {
  const h = (v) => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, "0");
  return `#${h(L.r)}${h(L.g)}${h(L.b)}`;
}

export function hexRgb(hex) {
  const n = hex.replace("#", "");
  return {
    r: parseInt(n.slice(0, 2), 16) / 255,
    g: parseInt(n.slice(2, 4), 16) / 255,
    b: parseInt(n.slice(4, 6), 16) / 255,
  };
}

export async function loadAtlas() {
  const meta = await fetchAtlasMeta();
  const img = new Image();
  img.crossOrigin = "anonymous";
  const ready = new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = reject;
  });
  img.src = meta.url || "/api/shapes/atlas.png";
  await ready;
  const c = document.createElement("canvas");
  c.width = meta.width;
  c.height = meta.height;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  return {
    ...meta,
    img,
    pixels: ctx.getImageData(0, 0, meta.width, meta.height).data,
  };
}

export function atlasTile(atlas, shapeId) {
  return atlas && atlas.tiles[String(shapeId)];
}

function outlineGlyph(src, radius) {
  const w = src.width;
  const h = src.height;
  const sctx = src.getContext("2d");
  const img = sctx.getImageData(0, 0, w, h);
  const a = new Uint8ClampedArray(w * h);
  for (let i = 0, p = 3; i < a.length; i++, p += 4) a[i] = img.data[p];
  const r = Math.max(1, radius | 0);
  const dil = new Uint8ClampedArray(a.length);
  const ero = new Uint8ClampedArray(a.length);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let mx = 0;
      let mn = 255;
      for (let dy = -r; dy <= r; dy++) {
        for (let dx = -r; dx <= r; dx++) {
          const xx = x + dx;
          const yy = y + dy;
          if (xx < 0 || yy < 0 || xx >= w || yy >= h) {
            mn = 0;
            continue;
          }
          const v = a[yy * w + xx];
          if (v > mx) mx = v;
          if (v < mn) mn = v;
        }
      }
      dil[y * w + x] = mx;
      ero[y * w + x] = mn;
    }
  }
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const octx = out.getContext("2d");
  const oimg = octx.createImageData(w, h);
  for (let i = 0, p = 0; i < a.length; i++, p += 4) {
    oimg.data[p] = img.data[p];
    oimg.data[p + 1] = img.data[p + 1];
    oimg.data[p + 2] = img.data[p + 2];
    oimg.data[p + 3] = Math.max(0, dil[i] - ero[i]);
  }
  octx.putImageData(oimg, 0, 0);
  return out;
}

function lruGet(key) {
  if (!tintCache.has(key)) return undefined;
  const val = tintCache.get(key);
  tintCache.delete(key);
  tintCache.set(key, val);
  return val;
}

function lruSet(key, val) {
  if (tintCache.has(key)) tintCache.delete(key);
  tintCache.set(key, val);
  if (tintCache.size > TINT_CACHE_MAX) {
    const oldest = tintCache.keys().next().value;
    tintCache.delete(oldest);
  }
}

export function tintedGlyph(atlas, L) {
  const tile = atlasTile(atlas, L.shape);
  if (!atlas || !tile) return null;
  const key = `${L.shape}|${Math.round(L.r * 255)}|${Math.round(L.g * 255)}|${Math.round(L.b * 255)}|${L.outlined ? 1 : 0}`;
  const cached = lruGet(key);
  if (cached) return cached;
  const src = document.createElement("canvas");
  src.width = atlas.cell;
  src.height = atlas.cell;
  const sctx = src.getContext("2d");
  sctx.drawImage(atlas.img, tile.x, tile.y, atlas.cell, atlas.cell, 0, 0, atlas.cell, atlas.cell);
  sctx.globalCompositeOperation = "multiply";
  sctx.fillStyle = `rgb(${Math.round(L.r * 255)},${Math.round(L.g * 255)},${Math.round(L.b * 255)})`;
  sctx.fillRect(0, 0, src.width, src.height);
  sctx.globalCompositeOperation = "destination-in";
  sctx.drawImage(atlas.img, tile.x, tile.y, atlas.cell, atlas.cell, 0, 0, atlas.cell, atlas.cell);
  const glyph = L.outlined ? outlineGlyph(src, 2) : src;
  lruSet(key, glyph);
  return glyph;
}

export function glyphAlpha(atlas, shapeId, u, v) {
  const tile = atlasTile(atlas, shapeId);
  if (!atlas || !tile) return 255;
  const cell = atlas.cell;
  let best = 0;
  for (let dy = -1; dy <= 1; dy++) {
    for (let dx = -1; dx <= 1; dx++) {
      const x = Math.min(cell - 1, Math.max(0, Math.floor(u * cell) + dx));
      const y = Math.min(cell - 1, Math.max(0, Math.floor(v * cell) + dy));
      const i = ((tile.y + y) * atlas.width + (tile.x + x)) * 4;
      best = Math.max(best, atlas.pixels[i + 3]);
    }
  }
  return best;
}

export function layerUV(L, x, y) {
  const w = Math.pow(2, L.sx);
  const h = Math.pow(2, L.sy);
  if (w <= 0 || h <= 0) return null;
  const dx = x - L.x;
  const dy = y - L.y;
  const rad = (-L.rot * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  let rx = dx * cos - dy * sin;
  let ry = dx * sin + dy * cos;
  if (L.flipped) rx = -rx;
  const u = rx / w + 0.5;
  const v = ry / h + 0.5;
  if (u < 0 || u > 1 || v < 0 || v > 1) return null;
  return { u, v };
}

export function hitTest(layers, atlas, x, y) {
  const order = layers
    .map((L, i) => i)
    .sort((a, b) => (layers[b].index - layers[a].index) || (b - a));
  for (const i of order) {
    const L = layers[i];
    if (L.hidden || L.locked) continue;
    const uv = layerUV(L, x, y);
    if (!uv) continue;
    if (glyphAlpha(atlas, L.shape, uv.u, uv.v) >= ALPHA_MIN) return i;
  }
  return -1;
}

// ---------- on-canvas transform handles ----------

function layerToCanvas(L, lx, ly) {
  const rad = (L.rot * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  return {
    x: L.x + lx * cos - ly * sin,
    y: L.y + lx * sin + ly * cos,
  };
}

export function getHandles(L) {
  const hw = Math.pow(2, L.sx) / 2;
  const hh = Math.pow(2, L.sy) / 2;
  const rotOffset = Math.max(0.06, Math.min(0.1, hh * 0.25 + 0.04));
  return [
    { kind: "resize", corner: "nw", ...layerToCanvas(L, -hw, -hh) },
    { kind: "resize", corner: "ne", ...layerToCanvas(L, hw, -hh) },
    { kind: "resize", corner: "se", ...layerToCanvas(L, hw, hh) },
    { kind: "resize", corner: "sw", ...layerToCanvas(L, -hw, hh) },
    { kind: "rotate", corner: "rot", ...layerToCanvas(L, 0, -hh - rotOffset) },
  ];
}

export function hitHandle(L, x, y, radius = 0.028) {
  if (!L || L.hidden || L.locked) return null;
  for (const h of getHandles(L)) {
    const dist = Math.hypot(x - h.x, y - h.y);
    if (dist <= radius) return h;
  }
  return null;
}

const SNAP_TARGETS = [-0.25, 0, 0.25];
const SNAP_EPS = 0.015;

export function snapPoint(x, y, enabled) {
  if (!enabled) return { x, y, snapX: null, snapY: null };
  let sx = x;
  let sy = y;
  let snapX = null;
  let snapY = null;
  for (const t of SNAP_TARGETS) {
    if (Math.abs(x - t) <= SNAP_EPS) { sx = t; snapX = t; break; }
  }
  for (const t of SNAP_TARGETS) {
    if (Math.abs(y - t) <= SNAP_EPS) { sy = t; snapY = t; break; }
  }
  return { x: sx, y: sy, snapX, snapY };
}

export function drawCanvas(canvas, state, atlas, { includeOverlay = true } = {}) {
  const ctx = canvas.getContext("2d");
  const size = canvas.width;
  ctx.clearRect(0, 0, size, size);

  if (includeOverlay && state.showGrid) {
    ctx.save();
    ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
    ctx.lineWidth = 1;
    for (const frac of [0.25, 0.5, 0.75]) {
      const p = Math.round(size * frac) + 0.5;
      ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, size); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, p); ctx.lineTo(size, p); ctx.stroke();
    }
    ctx.restore();
  }

  const ordered = [...state.layers].sort((a, b) => a.index - b.index);
  for (const L of ordered) {
    if (L.hidden) continue;
    const w = Math.max(1, Math.round(Math.pow(2, L.sx) * size));
    const h = Math.max(1, Math.round(Math.pow(2, L.sy) * size));
    ctx.save();
    ctx.translate(size / 2 + L.x * size, size / 2 + L.y * size);
    ctx.rotate((L.rot * Math.PI) / 180);
    if (L.flipped) ctx.scale(-1, 1);
    ctx.globalAlpha = Math.max(0, Math.min(1, L.a));
    const glyph = tintedGlyph(atlas, L);
    if (glyph) {
      ctx.drawImage(glyph, -w / 2, -h / 2, w, h);
    } else {
      ctx.fillStyle = rgbHex(L);
      ctx.fillRect(-w / 2, -h / 2, w, h);
    }
    ctx.restore();
  }

  if (!includeOverlay) return;

  if (state.traceImage && state.traceOpacity > 0) {
    ctx.save();
    ctx.globalAlpha = state.traceOpacity;
    ctx.drawImage(state.traceImage, 0, 0, size, size);
    ctx.restore();
  }

  // Snap guides while dragging
  if (state.snapGuides) {
    ctx.save();
    ctx.strokeStyle = "rgba(56, 189, 248, 0.85)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    if (state.snapGuides.x !== null) {
      const gx = size / 2 + state.snapGuides.x * size;
      ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, size); ctx.stroke();
    }
    if (state.snapGuides.y !== null) {
      const gy = size / 2 + state.snapGuides.y * size;
      ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(size, gy); ctx.stroke();
    }
    ctx.restore();
  }

  if (state.selected >= 0 && state.layers[state.selected]) {
    const L = state.layers[state.selected];
    if (L.hidden) return;
    const w = Math.pow(2, L.sx) * size;
    const h = Math.pow(2, L.sy) * size;
    ctx.save();
    ctx.translate(size / 2 + L.x * size, size / 2 + L.y * size);
    ctx.rotate((L.rot * Math.PI) / 180);
    ctx.strokeStyle = L.locked ? "#94a3b8" : "#ff8000";
    ctx.lineWidth = 1.75;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(-w / 2, -h / 2, w, h);
    ctx.restore();

    if (!L.locked) {
      const handles = getHandles(L);
      const topMid = layerToCanvas(L, 0, -Math.pow(2, L.sy) / 2);
      const rotHandle = handles.find((item) => item.kind === "rotate");
      ctx.save();
      if (rotHandle) {
        ctx.strokeStyle = "#ff8000";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(size / 2 + topMid.x * size, size / 2 + topMid.y * size);
        ctx.lineTo(size / 2 + rotHandle.x * size, size / 2 + rotHandle.y * size);
        ctx.stroke();
      }
      for (const item of handles) {
        const px = size / 2 + item.x * size;
        const py = size / 2 + item.y * size;
        ctx.fillStyle = item.kind === "rotate" ? "#38bdf8" : "#ff8000";
        ctx.strokeStyle = "#0b0d10";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        if (item.kind === "rotate") {
          ctx.arc(px, py, 6, 0, Math.PI * 2);
        } else {
          ctx.rect(px - 5, py - 5, 10, 10);
        }
        ctx.fill();
        ctx.stroke();
      }
      ctx.restore();
    }
  }
}
