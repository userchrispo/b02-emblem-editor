// Backend API adapter with automatic static-demo fallback (for GitHub Pages).
import { encodeSlotFileBytes, parseSlotBytes } from "./binary.js";

const DEMO_STORAGE_KEY = "bo2.emblemDemo.saved";

export function isDemoMode() {
  return Boolean(window.EMBLEM_DEMO_MODE);
}

function demoBase() {
  return window.EMBLEM_DEMO_BASE || "./demo-data";
}

export async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("application/json")) return r.json();
  if (!r.ok) throw new Error(await r.text());
  return r;
}

export async function fetchShapes() {
  if (isDemoMode()) return getJSON(`${demoBase()}/shapes.json`);
  return getJSON("/api/shapes");
}

export async function fetchAtlasMeta() {
  if (isDemoMode()) {
    const meta = await getJSON(`${demoBase()}/atlas.json`);
    return { ...meta, url: `${demoBase()}/atlas.png` };
  }
  return getJSON("/api/shapes/atlas.json");
}

function readDemoStore() {
  try {
    return JSON.parse(localStorage.getItem(DEMO_STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function writeDemoStore(items) {
  try {
    localStorage.setItem(DEMO_STORAGE_KEY, JSON.stringify(items));
  } catch {
    // ignore storage quota errors
  }
}

export async function fetchEmblemList() {
  if (isDemoMode()) {
    const presets = await getJSON(`${demoBase()}/presets.json`).catch(() => []);
    const local = readDemoStore();
    return [...local, ...presets];
  }
  return getJSON("/api/emblems");
}

export async function fetchEmblemLayers(group, slot) {
  if (isDemoMode()) {
    const all = await fetchEmblemList();
    const found = all.find((e) => e.group === group && Number(e.slot) === Number(slot));
    if (!found) throw new Error("Emblem not found");
    return {
      group: found.group,
      slot: found.slot,
      label: found.label,
      source: found.source || "editor",
      layers: found.layers || [],
    };
  }
  return getJSON(`/api/emblems/${encodeURIComponent(group)}/${slot}/layers`);
}

export async function saveEmblemRequest(payload) {
  if (isDemoMode()) {
    const items = readDemoStore();
    const group = payload.group || `demo_${Date.now().toString(36)}`;
    const entry = {
      group,
      slot: 0,
      label: payload.label || "Untitled emblem",
      source: "editor",
      layers: payload.layers,
      mtime: Math.floor(Date.now() / 1000),
    };
    const idx = items.findIndex((x) => x.group === group);
    if (idx >= 0) items[idx] = entry;
    else items.unshift(entry);
    writeDemoStore(items);
    return { ok: true, group, slot: 0, id: `${group}:0` };
  }
  return postJSON("/api/editor/save", payload);
}

export async function exportBinBlob(payload) {
  if (isDemoMode()) {
    return new Blob([encodeSlotFileBytes(payload.layers)], {
      type: "application/octet-stream",
    });
  }
  const r = await fetch("/api/editor/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error("export failed");
  return r.blob();
}

export async function exportPngBlob(payload, canvasEl) {
  if (!isDemoMode()) {
    const r = await fetch("/api/editor/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layers: payload.layers, size: 512 }),
    });
    if (r.ok) return r.blob();
  }
  return new Promise((resolve, reject) => {
    canvasEl.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("PNG export failed"))), "image/png");
  });
}

export async function importBinBytes(bytes, label) {
  if (isDemoMode()) {
    const layers = parseSlotBytes(bytes);
    if (!layers.length && bytes.length < 44) {
      throw new Error("That is not an emblem file");
    }
    const saved = await saveEmblemRequest({ layers, label });
    return { ...saved, layers };
  }
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  const res = await postJSON("/api/emblems/import", {
    data_base64: btoa(bin),
    label,
  });
  if (!res.ok) throw new Error(res.error || "import failed");
  return res;
}
