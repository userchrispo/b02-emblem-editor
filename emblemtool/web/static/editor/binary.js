// Client-side BO2 emblem .bin encoder/decoder (1408-byte payload, 32 x 44-byte layers).
// Matches emblemtool/shapes/render.py byte-for-byte so .bin export/import works
// both with the Python backend and in the standalone static GitHub Pages demo.

export const LAYER_SIZE = 44;
export const NUM_LAYERS = 32;
export const EMPTY_SHAPE = 65535;

export function stripHttp(bytes) {
  for (let i = 0; i + 3 < bytes.length; i++) {
    if (bytes[i] === 13 && bytes[i + 1] === 10 && bytes[i + 2] === 13 && bytes[i + 3] === 10) {
      return bytes.subarray(i + 4);
    }
  }
  return bytes;
}

export function parseSlotBytes(bytes) {
  const body = stripHttp(bytes);
  const view = new DataView(body.buffer, body.byteOffset, body.byteLength);
  const count = Math.min(NUM_LAYERS, Math.floor(body.byteLength / LAYER_SIZE));
  const layers = [];
  for (let i = 0; i < count; i++) {
    const off = i * LAYER_SIZE;
    const shape = view.getUint16(off, true);
    if (shape === EMPTY_SHAPE) continue;
    layers.push({
      index: i,
      shape,
      r: view.getFloat32(off + 4, true),
      g: view.getFloat32(off + 8, true),
      b: view.getFloat32(off + 12, true),
      a: view.getFloat32(off + 16, true),
      x: view.getFloat32(off + 20, true),
      y: view.getFloat32(off + 24, true),
      sx: view.getFloat32(off + 28, true),
      sy: view.getFloat32(off + 32, true),
      rot: view.getFloat32(off + 36, true),
      outlined: Boolean(body[off + 40]),
      flipped: Boolean(body[off + 41]),
    });
  }
  return layers;
}

function clamp01(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 1.0;
  return Math.max(0, Math.min(1, n));
}

function f32(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export function encodeLayers(layers) {
  const slots = new Array(NUM_LAYERS).fill(null);
  for (const raw of layers || []) {
    const idx = Number.parseInt(raw.index, 10);
    if (Number.isInteger(idx) && idx >= 0 && idx < NUM_LAYERS) {
      slots[idx] = raw;
    }
  }
  const buf = new ArrayBuffer(NUM_LAYERS * LAYER_SIZE);
  const out = new Uint8Array(buf);
  const view = new DataView(buf);
  for (let i = 0; i < NUM_LAYERS; i++) {
    const off = i * LAYER_SIZE;
    const raw = slots[i];
    if (!raw) {
      view.setUint16(off, EMPTY_SHAPE, true);
      continue;
    }
    let shape = Number.parseInt(raw.shape, 10);
    if (!Number.isInteger(shape) || shape < 0 || shape > 65535) {
      shape = EMPTY_SHAPE;
    }
    view.setUint16(off, shape, true);
    view.setFloat32(off + 4, clamp01(raw.r), true);
    view.setFloat32(off + 8, clamp01(raw.g), true);
    view.setFloat32(off + 12, clamp01(raw.b), true);
    view.setFloat32(off + 16, clamp01(raw.a), true);
    view.setFloat32(off + 20, f32(raw.x, 0), true);
    view.setFloat32(off + 24, f32(raw.y, 0), true);
    view.setFloat32(off + 28, f32(raw.sx, -1), true);
    view.setFloat32(off + 32, f32(raw.sy, -1), true);
    view.setFloat32(off + 36, f32(raw.rot, 0), true);
    out[off + 40] = raw.outlined ? 1 : 0;
    out[off + 41] = raw.flipped ? 1 : 0;
  }
  return out;
}

export function wrapSlotHttp(body) {
  const headerStr =
    "HTTP/1.1 200 OK\r\n" +
    `Content-Length: ${body.byteLength}\r\n` +
    "Content-Type: application/octet-stream\r\n" +
    "Connection: close\r\n\r\n";
  const headerBytes = new TextEncoder().encode(headerStr);
  const combined = new Uint8Array(headerBytes.byteLength + body.byteLength);
  combined.set(headerBytes, 0);
  combined.set(body, headerBytes.byteLength);
  return combined;
}

export function encodeSlotFileBytes(layers) {
  return wrapSlotHttp(encodeLayers(layers));
}
