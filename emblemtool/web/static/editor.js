// PC emblem editor (modular ES entry point, zero build step).
import {
  exportBinBlob,
  exportPngBlob,
  fetchEmblemLayers,
  fetchEmblemList,
  fetchShapes,
  importBinBytes,
  isDemoMode,
  saveEmblemRequest,
} from "./editor/api.js";
import {
  atlasTile,
  drawCanvas,
  hexRgb,
  hitHandle,
  hitTest,
  loadAtlas,
  rgbHex,
  snapPoint,
} from "./editor/canvas.js";
import { createHistory } from "./editor/history.js";
import { prepareTargetPixels, refineExistingLayers, vectorizeImage } from "./editor/vectorizer.js";

const MAX_LAYERS = 32;
const CAT_LABELS = {
  tools: "Shapes",
  type: "Letters",
  ranks: "Ranks",
  gear: "Weapons",
  emblems: "Emblems",
};

// Classic BO2-inspired tactical color swatches
const PRESET_SWATCHES = [
  "#ffffff", "#d4d4d8", "#71717a", "#18181b",
  "#ef4444", "#f97316", "#facc15", "#22c55e",
  "#06b6d4", "#3b82f6", "#8b5cf6", "#ec4899",
  "#991b1b", "#9a3412", "#854d0e", "#166534",
];

const state = {
  layers: [],
  selected: -1,
  label: "",
  group: null,
  slot: 0,
  dirty: false,
  shapes: [],
  category: "tools",
  query: "",
  uniform: true,
  showGrid: true,
  snapEnabled: true,
  snapGuides: null,
  interaction: null, // { mode: 'move' | 'resize' | 'rotate', ... }
  recentColors: [],
  traceImage: null,
  traceOpacity: 0.3,
};

const $ = (id) => document.getElementById(id);
const history = createHistory(80);
let atlas = null;
let layerClip = null;

function scaleToPct(s) { return Math.pow(2, s) * 100; }
function pctToScale(p) { return Math.log2(Math.max(1, Number(p)) / 100); }

function shapeName(id) {
  const s = state.shapes.find((x) => x.id === id);
  return s ? s.name : `Shape ${id}`;
}

function addRecentColor(hex) {
  const norm = hex.toLowerCase();
  if (PRESET_SWATCHES.includes(norm)) return;
  state.recentColors = [norm, ...state.recentColors.filter((c) => c !== norm)].slice(0, 8);
  renderRecentSwatches();
}

function markDirty(opts) {
  state.dirty = true;
  if (opts && opts.layers) {
    renderLayers();
  } else {
    updateSelectedSwatch();
  }
  renderInspector();
  setStatus();
  redraw();
}

function redraw() {
  drawCanvas($("emblemCanvas"), state, atlas);
}

function updateSelectedSwatch() {
  const L = state.layers[state.selected];
  const li = document.querySelector(`.layer-item[data-index="${state.selected}"]`);
  if (!L || !li) return;
  li.querySelector(".layer-swatch").style.background = rgbHex(L);
  const name = shapeName(L.shape) + (L.outlined ? " · outline" : "");
  li.querySelector(".layer-name").textContent = name;
}

function setStatus() {
  const bits = [];
  if (isDemoMode()) {
    bits.push("Static Demo");
  } else {
    bits.push(state.group ? `Saved ${state.group}` : "Unsaved");
  }
  if (state.dirty) bits.push("unsaved changes");
  bits.push(`${state.layers.length} / ${MAX_LAYERS} layers`);
  $("statusLine").textContent = bits.join(" · ");
}

function snapshot() {
  return {
    layers: state.layers.map((L) => ({ ...L })),
    selected: state.selected,
    label: state.label,
  };
}

function pushUndo() {
  history.push(snapshot());
}

function applySnap(snap) {
  if (!snap) return;
  state.layers = snap.layers.map((L) => ({ ...L }));
  state.selected = snap.selected;
  state.label = snap.label;
  state.dirty = true;
  renderAll();
}

function undo() { applySnap(history.undo(snapshot())); }
function redo() { applySnap(history.redo(snapshot())); }

function reindex() {
  state.layers.forEach((L, i) => { L.index = i; });
}

function addLayer(shapeId) {
  if (state.layers.length >= MAX_LAYERS) {
    setStatus();
    $("statusLine").textContent = "32 layer limit — remove one first";
    return;
  }
  pushUndo();
  state.layers.push({
    index: state.layers.length,
    shape: shapeId,
    r: 1, g: 1, b: 1, a: 1,
    x: 0, y: 0,
    sx: -1, sy: -1,
    rot: 0,
    outlined: false,
    flipped: false,
    hidden: false,
    locked: false,
  });
  state.selected = state.layers.length - 1;
  markDirty({ layers: true });
}

function removeSelected() {
  if (state.selected < 0) return;
  pushUndo();
  state.layers.splice(state.selected, 1);
  reindex();
  state.selected = Math.min(state.selected, state.layers.length - 1);
  markDirty({ layers: true });
}

function exportableLayers() {
  return state.layers.filter((L) => !L.hidden).map((L, idx) => ({
    index: idx,
    shape: L.shape,
    r: L.r, g: L.g, b: L.b, a: L.a,
    x: L.x, y: L.y,
    sx: L.sx, sy: L.sy,
    rot: L.rot,
    outlined: !!L.outlined,
    flipped: !!L.flipped,
  }));
}

function payload() {
  return {
    layers: exportableLayers(),
    label: $("emblemName").value.trim() || "Untitled emblem",
    group: state.group,
    slot: state.slot,
  };
}

async function saveEmblem(extra) {
  const body = { ...payload(), ...extra };
  const res = await saveEmblemRequest(body);
  state.group = res.group;
  state.slot = res.slot;
  state.dirty = false;
  state.label = body.label;
  setStatus();
  return res;
}

function renderPalette() {
  const tabs = $("catTabs");
  const cats = [...new Set(state.shapes.map((s) => s.category))];
  tabs.innerHTML = "";
  for (const cat of cats) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cat-tab";
    btn.textContent = CAT_LABELS[cat] || cat;
    btn.setAttribute("aria-selected", cat === state.category ? "true" : "false");
    btn.addEventListener("click", () => {
      state.category = cat;
      renderPalette();
    });
    tabs.appendChild(btn);
  }

  const q = state.query.trim().toLowerCase();
  const items = state.shapes.filter((s) => {
    if (s.category !== state.category) return false;
    if (q && !s.name.toLowerCase().includes(q)) return false;
    return true;
  });
  $("shapeCount").textContent = `${items.length}`;
  const grid = $("shapeGrid");
  grid.innerHTML = "";
  for (const s of items) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "shape-cell";
    btn.title = s.name;
    btn.setAttribute("aria-label", s.name);
    if (atlas) {
      const c = document.createElement("canvas");
      c.width = 64;
      c.height = 64;
      c.setAttribute("aria-hidden", "true");
      const t = atlasTile(atlas, s.id);
      if (t) {
        const ctx = c.getContext("2d");
        ctx.drawImage(atlas.img, t.x, t.y, atlas.cell, atlas.cell, 0, 0, 64, 64);
      }
      btn.appendChild(c);
    } else {
      const img = document.createElement("img");
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      img.src = `/api/shapes/${s.id}.png`;
      btn.appendChild(img);
    }
    btn.addEventListener("click", () => addLayer(s.id));
    grid.appendChild(btn);
  }
}

function renderLayers() {
  const empty = $("layersEmpty");
  const list = $("layerList");
  empty.style.display = state.layers.length ? "none" : "";
  list.innerHTML = "";
  const frontFirst = [...state.layers].reverse();
  for (const L of frontFirst) {
    const i = state.layers.indexOf(L);
    const li = document.createElement("li");
    li.className = "layer-item" + (i === state.selected ? " selected" : "") + (L.hidden ? " is-hidden" : "");
    li.draggable = true;
    li.dataset.index = String(i);
    li.innerHTML = `
      <span class="layer-handle" aria-hidden="true">⋮⋮</span>
      <span class="layer-swatch"></span>
      <span class="layer-name"></span>
      <button class="layer-icon-btn layer-vis" type="button" title="Toggle visibility"></button>
      <button class="layer-icon-btn layer-lock" type="button" title="Toggle lock"></button>`;
    li.querySelector(".layer-swatch").style.background = rgbHex(L);
    li.querySelector(".layer-name").textContent = shapeName(L.shape) + (L.outlined ? " · outline" : "");
    const visBtn = li.querySelector(".layer-vis");
    visBtn.textContent = L.hidden ? "○" : "●";
    visBtn.classList.toggle("active", !L.hidden);
    visBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      pushUndo();
      L.hidden = !L.hidden;
      markDirty({ layers: true });
    });
    const lockBtn = li.querySelector(".layer-lock");
    lockBtn.textContent = L.locked ? "■" : "□";
    lockBtn.classList.toggle("active", !!L.locked);
    lockBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      L.locked = !L.locked;
      renderLayers();
      redraw();
    });
    li.addEventListener("click", () => {
      state.selected = i;
      renderAll();
    });
    li.addEventListener("dragstart", (ev) => {
      ev.dataTransfer.setData("text/plain", String(i));
      ev.dataTransfer.effectAllowed = "move";
    });
    li.addEventListener("dragover", (ev) => ev.preventDefault());
    li.addEventListener("drop", (ev) => {
      ev.preventDefault();
      const from = Number(ev.dataTransfer.getData("text/plain"));
      const to = i;
      if (Number.isNaN(from) || from === to) return;
      pushUndo();
      const [moved] = state.layers.splice(from, 1);
      state.layers.splice(to, 0, moved);
      reindex();
      state.selected = to;
      markDirty({ layers: true });
    });
    list.appendChild(li);
  }
}

function renderSwatches(container, colors) {
  container.innerHTML = "";
  for (const hex of colors) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "swatch-btn";
    btn.style.background = hex;
    btn.title = hex;
    btn.setAttribute("aria-label", `Color ${hex}`);
    btn.addEventListener("click", () => {
      const L = state.layers[state.selected];
      if (!L) return;
      pushUndo();
      Object.assign(L, hexRgb(hex));
      addRecentColor(hex);
      markDirty();
    });
    container.appendChild(btn);
  }
}

function renderRecentSwatches() {
  const el = $("recentSwatches");
  if (!el) return;
  el.hidden = !state.recentColors.length;
  renderSwatches(el, state.recentColors);
}

function renderInspector() {
  const L = state.layers[state.selected];
  $("inspectorEmpty").hidden = !!L;
  $("inspectorBody").hidden = !L;
  if (!L) return;
  $("propColor").value = rgbHex(L);
  $("propA").value = String(Math.round(L.a * 100));
  $("propALabel").textContent = `${Math.round(L.a * 100)}%`;
  $("propW").value = String(Math.round(scaleToPct(L.sx)));
  $("propH").value = String(Math.round(scaleToPct(L.sy)));
  $("propWLabel").textContent = `${Math.round(scaleToPct(L.sx))}%`;
  $("propHLabel").textContent = `${Math.round(scaleToPct(L.sy))}%`;
  $("propRot").value = String(Math.round(((L.rot % 360) + 360) % 360));
  $("propRotLabel").textContent = `${Math.round(L.rot)}°`;
  $("propOutline").checked = !!L.outlined;
  $("propFlip").checked = !!L.flipped;
  $("propUniform").checked = state.uniform;
}

function renderAll() {
  $("emblemName").value = state.label;
  renderLayers();
  renderInspector();
  setStatus();
  redraw();
}

function bindInspector() {
  renderSwatches($("swatchStrip"), PRESET_SWATCHES);
  $("propColor").addEventListener("input", () => {
    const L = state.layers[state.selected];
    if (!L) return;
    Object.assign(L, hexRgb($("propColor").value));
    markDirty();
  });
  $("propColor").addEventListener("change", () => {
    addRecentColor($("propColor").value);
  });
  $("propA").addEventListener("input", () => {
    const L = state.layers[state.selected];
    if (!L) return;
    L.a = Number($("propA").value) / 100;
    markDirty();
  });
  function onScale(which) {
    const L = state.layers[state.selected];
    if (!L) return;
    const val = pctToScale($(which === "x" ? "propW" : "propH").value);
    if (state.uniform) {
      L.sx = val;
      L.sy = val;
    } else if (which === "x") L.sx = val;
    else L.sy = val;
    markDirty();
  }
  $("propW").addEventListener("input", () => onScale("x"));
  $("propH").addEventListener("input", () => onScale("y"));
  $("propUniform").addEventListener("change", () => {
    state.uniform = $("propUniform").checked;
  });
  $("propRot").addEventListener("input", () => {
    const L = state.layers[state.selected];
    if (!L) return;
    L.rot = Number($("propRot").value);
    markDirty();
  });
  $("propOutline").addEventListener("change", () => {
    const L = state.layers[state.selected];
    if (!L) return;
    pushUndo();
    L.outlined = $("propOutline").checked;
    markDirty();
  });
  $("propFlip").addEventListener("change", () => {
    const L = state.layers[state.selected];
    if (!L) return;
    pushUndo();
    L.flipped = $("propFlip").checked;
    markDirty();
  });
  $("deleteLayerBtn").addEventListener("click", removeSelected);
  $("duplicateLayerBtn").addEventListener("click", duplicateSelected);
  ["propColor", "propA", "propW", "propH", "propRot"].forEach((id) => {
    $(id).addEventListener("pointerdown", () => pushUndo());
  });
}

function canvasPos(ev) {
  const canvas = $("emblemCanvas");
  const rect = canvas.getBoundingClientRect();
  return {
    x: (ev.clientX - rect.left) / rect.width - 0.5,
    y: (ev.clientY - rect.top) / rect.height - 0.5,
  };
}

function bindCanvas() {
  const canvas = $("emblemCanvas");
  canvas.addEventListener("pointerdown", (ev) => {
    canvas.setPointerCapture(ev.pointerId);
    const p = canvasPos(ev);
    const sel = state.layers[state.selected];
    const handle = hitHandle(sel, p.x, p.y);
    if (handle) {
      pushUndo();
      if (handle.kind === "rotate") {
        const startAngle = Math.atan2(p.y - sel.y, p.x - sel.x);
        state.interaction = { mode: "rotate", startAngle, startRot: sel.rot };
      } else {
        const startDist = Math.max(0.02, Math.hypot(p.x - sel.x, p.y - sel.y));
        state.interaction = {
          mode: "resize",
          startDist,
          startSx: sel.sx,
          startSy: sel.sy,
        };
      }
      return;
    }

    const hit = hitTest(state.layers, atlas, p.x, p.y);
    state.selected = hit;
    if (hit >= 0) {
      pushUndo();
      const L = state.layers[hit];
      state.interaction = { mode: "move", dx: p.x - L.x, dy: p.y - L.y };
    } else {
      state.interaction = null;
    }
    renderAll();
  });

  canvas.addEventListener("pointermove", (ev) => {
    const p = canvasPos(ev);
    if (!state.interaction) {
      const sel = state.layers[state.selected];
      const h = hitHandle(sel, p.x, p.y);
      canvas.style.cursor = h ? (h.kind === "rotate" ? "grab" : "nwse-resize") : "default";
      return;
    }
    const L = state.layers[state.selected];
    if (!L) return;

    if (state.interaction.mode === "move") {
      const snapped = snapPoint(p.x - state.interaction.dx, p.y - state.interaction.dy, state.snapEnabled && !ev.altKey);
      L.x = snapped.x;
      L.y = snapped.y;
      state.snapGuides = (snapped.snapX !== null || snapped.snapY !== null)
        ? { x: snapped.snapX, y: snapped.snapY }
        : null;
    } else if (state.interaction.mode === "resize") {
      const dist = Math.max(0.02, Math.hypot(p.x - L.x, p.y - L.y));
      const deltaLog = Math.log2(dist / state.interaction.startDist);
      const clampS = (s) => Math.max(-4.32, Math.min(2.0, s));
      L.sx = clampS(state.interaction.startSx + deltaLog);
      L.sy = clampS(state.interaction.startSy + deltaLog);
      renderInspector();
    } else if (state.interaction.mode === "rotate") {
      const angle = Math.atan2(p.y - L.y, p.x - L.x);
      let deg = state.interaction.startRot + ((angle - state.interaction.startAngle) * 180) / Math.PI;
      if (ev.shiftKey) deg = Math.round(deg / 15) * 15;
      L.rot = ((deg % 360) + 360) % 360;
      renderInspector();
    }
    state.dirty = true;
    redraw();
    setStatus();
  });

  canvas.addEventListener("pointerup", () => {
    state.snapGuides = null;
    if (state.interaction) renderAll();
    state.interaction = null;
  });
}

function duplicateSelected() {
  if (state.selected < 0) return;
  if (state.layers.length >= MAX_LAYERS) {
    $("statusLine").textContent = "32 layer limit — remove one first";
    return;
  }
  pushUndo();
  const src = state.layers[state.selected];
  const copy = { ...src, x: src.x + 0.04, y: src.y + 0.04 };
  state.layers.splice(state.selected + 1, 0, copy);
  reindex();
  state.selected += 1;
  markDirty({ layers: true });
}

function copySelected() {
  if (state.selected < 0) return;
  layerClip = { ...state.layers[state.selected] };
}

function pasteLayer() {
  if (!layerClip) return;
  if (state.layers.length >= MAX_LAYERS) {
    $("statusLine").textContent = "32 layer limit — remove one first";
    return;
  }
  pushUndo();
  const copy = { ...layerClip, x: layerClip.x + 0.04, y: layerClip.y + 0.04 };
  const at = state.selected < 0 ? state.layers.length : state.selected + 1;
  state.layers.splice(at, 0, copy);
  reindex();
  state.selected = at;
  markDirty({ layers: true });
}

function bindKeys() {
  document.addEventListener("keydown", (ev) => {
    const tag = (ev.target && ev.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    const cmd = ev.ctrlKey || ev.metaKey;
    if (cmd && ev.key.toLowerCase() === "z") {
      ev.preventDefault();
      if (ev.shiftKey) redo();
      else undo();
      return;
    }
    if (cmd && ev.key.toLowerCase() === "y") { ev.preventDefault(); redo(); return; }
    if (cmd && ev.key.toLowerCase() === "d") { ev.preventDefault(); duplicateSelected(); return; }
    if (cmd && ev.key.toLowerCase() === "c") { ev.preventDefault(); copySelected(); return; }
    if (cmd && ev.key.toLowerCase() === "v") { ev.preventDefault(); pasteLayer(); return; }
    if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); removeSelected(); return; }
    const L = state.layers[state.selected];
    if (!L || L.locked) return;
    const step = ev.shiftKey ? 0.05 : 0.01;
    if (ev.key === "ArrowLeft") { pushUndo(); L.x -= step; markDirty(); ev.preventDefault(); }
    if (ev.key === "ArrowRight") { pushUndo(); L.x += step; markDirty(); ev.preventDefault(); }
    if (ev.key === "ArrowUp") { pushUndo(); L.y -= step; markDirty(); ev.preventDefault(); }
    if (ev.key === "ArrowDown") { pushUndo(); L.y += step; markDirty(); ev.preventDefault(); }
    if (ev.key === "[") { pushUndo(); L.rot = (L.rot - (ev.shiftKey ? 15 : 5) + 360) % 360; markDirty(); }
    if (ev.key === "]") { pushUndo(); L.rot = (L.rot + (ev.shiftKey ? 15 : 5)) % 360; markDirty(); }
  });
}

function newEmblem() {
  if (state.dirty && !confirm("Discard unsaved changes?")) return;
  state.layers = [];
  state.selected = -1;
  state.label = "";
  state.group = null;
  state.slot = 0;
  state.dirty = false;
  history.clear();
  renderAll();
}

async function openEmblem(group, slot) {
  const data = await fetchEmblemLayers(group, slot);
  state.layers = (data.layers || []).map((L) => ({ hidden: false, locked: false, ...L }));
  reindex();
  state.selected = state.layers.length ? state.layers.length - 1 : -1;
  state.label = data.label || "";
  state.group = data.source === "editor" ? data.group : null;
  state.slot = data.source === "editor" ? data.slot : 0;
  state.dirty = false;
  const dlg = $("openDialog");
  if (dlg.open) dlg.close();
  history.clear();
  renderAll();
}

async function showOpenDialog() {
  const emblems = await fetchEmblemList();
  $("openEmpty").style.display = emblems.length ? "none" : "";
  const grid = $("openGrid");
  grid.innerHTML = "";
  for (const e of emblems) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "open-card";
    if (!isDemoMode()) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = `/api/render/${encodeURIComponent(e.group)}/${e.slot}.png${e.mtime ? `?v=${e.mtime}` : ""}`;
      btn.appendChild(img);
    }
    const span = document.createElement("span");
    span.textContent = e.label || (e.source === "editor" ? "Untitled" : "Capture");
    btn.appendChild(span);
    btn.addEventListener("click", () => openEmblem(e.group, e.slot));
    grid.appendChild(btn);
  }
  $("openDialog").showModal();
}

function triggerDownload(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

function safeFileBase() {
  return (payload().label || "emblem").replace(/[^\w\- ]+/g, "").trim() || "emblem";
}

async function downloadBin() {
  const blob = await exportBinBlob(payload());
  triggerDownload(blob, `${safeFileBase()}.bin`);
}

async function downloadPng() {
  const canvas = $("emblemCanvas");
  // Render clean frame without selection box for client-side fallback
  drawCanvas(canvas, state, atlas, { includeOverlay: false });
  try {
    const blob = await exportPngBlob(payload(), canvas);
    triggerDownload(blob, `${safeFileBase()}.png`);
  } finally {
    redraw();
  }
}

function bindVectorizerModal() {
  const dlg = $("img2emblemDialog");
  const openBtn = $("img2emblemBtn");
  if (!dlg || !openBtn) return;

  const dropzone = $("vecDropzone");
  const fileInput = $("vecFileInput");
  const dropLabel = $("vecDropLabel");
  const origCanvas = $("vecOrigCanvas");
  const outCanvas = $("vecOutCanvas");
  const progressFill = $("vecProgressFill");
  const stats = $("vecStats");
  const layersSlider = $("vecLayers");
  const layersVal = $("vecLayersVal");
  const zoomSlider = $("vecZoom");
  const zoomVal = $("vecZoomVal");
  const qualitySel = $("vecQuality");
  const shapeSetSel = $("vecShapeSet");
  const fitSel = $("vecFit");
  const saliencyCheck = $("vecSaliency");
  const alphaCheck = $("vecAlpha");
  const keepTraceCheck = $("vecKeepTrace");
  const runBtn = $("vecRunBtn");
  const refineBtn = $("vecRefineBtn");
  const stopBtn = $("vecStopBtn");
  const applyBtn = $("vecApplyBtn");

  let loadedImg = null;
  let loadedName = "";
  let generatedLayers = null;
  let lastPreparedCanvas = null;
  let controller = null;

  function currentCrop() {
    return { zoom: Number(zoomSlider.value) / 100, panX: 0, panY: 0 };
  }

  function drawOrigPreview() {
    if (!loadedImg) return;
    const prepared = prepareTargetPixels(loadedImg, 256, fitSel.value, currentCrop());
    lastPreparedCanvas = prepared.canvas;
    const octx = origCanvas.getContext("2d");
    octx.clearRect(0, 0, origCanvas.width, origCanvas.height);
    octx.drawImage(prepared.canvas, 0, 0, origCanvas.width, origCanvas.height);
  }

  function renderGeneratedPreview(layers) {
    drawCanvas(
      outCanvas,
      { layers, selected: -1, showGrid: false, snapGuides: null },
      atlas,
      { includeOverlay: false }
    );
  }

  async function runConversion() {
    if (!loadedImg || !atlas) return;
    if (controller) controller.abort();
    controller = new AbortController();
    const sig = controller.signal;

    runBtn.disabled = true;
    refineBtn.disabled = true;
    stopBtn.disabled = false;
    applyBtn.disabled = true;
    progressFill.style.width = "0%";
    drawOrigPreview();

    const maxLayers = Number(layersSlider.value);
    const started = performance.now();
    stats.textContent = "Phase 1: Fitting shapes…";

    try {
      const result = await vectorizeImage({
        source: loadedImg,
        atlas,
        shapes: state.shapes,
        maxLayers,
        shapeSet: shapeSetSel.value,
        quality: qualitySel.value,
        fit: fitSel.value,
        crop: currentCrop(),
        saliency: saliencyCheck.checked,
        allowAlpha: alphaCheck.checked,
        signal: sig,
        onProgress: (ev) => {
          generatedLayers = ev.layers;
          if (ev.phase === "refine") {
            progressFill.style.width = `${Math.round((ev.passIndex / ev.totalPasses) * 100)}%`;
            stats.textContent = `Global refine ${ev.passIndex}/${ev.totalPasses} · ${ev.similarity.toFixed(1)}% match`;
          } else {
            progressFill.style.width = `${Math.round((ev.layerIndex / ev.totalLayers) * 100)}%`;
            stats.textContent = `Layer ${ev.layerIndex}/${ev.totalLayers} · ${ev.similarity.toFixed(1)}% match`;
          }
          renderGeneratedPreview(ev.layers);
        },
      });
      generatedLayers = result;
      renderGeneratedPreview(result);
      const elapsed = ((performance.now() - started) / 1000).toFixed(2);
      const suffix = sig.aborted ? " (stopped early)" : "";
      stats.textContent = `${result.length} layers in ${elapsed}s${suffix}`;
      applyBtn.disabled = !result.length;
      refineBtn.disabled = !result.length;
    } catch (err) {
      stats.textContent = err.message || "Conversion failed";
      console.error(err);
    } finally {
      runBtn.disabled = !loadedImg;
      stopBtn.disabled = true;
      if (controller && controller.signal === sig) controller = null;
    }
  }

  async function runExtraRefine() {
    if (!loadedImg || !atlas || !generatedLayers || !generatedLayers.length) return;
    if (controller) controller.abort();
    controller = new AbortController();
    const sig = controller.signal;

    runBtn.disabled = true;
    refineBtn.disabled = true;
    stopBtn.disabled = false;
    const started = performance.now();
    stats.textContent = "Running +2 global joint refinement passes…";

    try {
      const refined = await refineExistingLayers({
        layers: generatedLayers,
        source: loadedImg,
        atlas,
        shapes: state.shapes,
        shapeSet: shapeSetSel.value,
        quality: qualitySel.value,
        fit: fitSel.value,
        crop: currentCrop(),
        saliency: saliencyCheck.checked,
        allowAlpha: alphaCheck.checked,
        passes: 2,
        signal: sig,
        onProgress: (ev) => {
          generatedLayers = ev.layers;
          progressFill.style.width = `${Math.round((ev.passIndex / ev.totalPasses) * 100)}%`;
          stats.textContent = `Global refine +${ev.passIndex}/${ev.totalPasses} · ${ev.similarity.toFixed(1)}% match`;
          renderGeneratedPreview(ev.layers);
        },
      });
      generatedLayers = refined;
      renderGeneratedPreview(refined);
      const elapsed = ((performance.now() - started) / 1000).toFixed(2);
      stats.textContent = `Refined ${refined.length} layers (+2 passes in ${elapsed}s)`;
    } catch (err) {
      stats.textContent = err.message || "Refinement failed";
      console.error(err);
    } finally {
      runBtn.disabled = !loadedImg;
      refineBtn.disabled = !generatedLayers || !generatedLayers.length;
      stopBtn.disabled = true;
      if (controller && controller.signal === sig) controller = null;
    }
  }

  function loadImageFile(file) {
    if (!file || !file.type.startsWith("image/")) {
      stats.textContent = "Please choose a valid image file (PNG, JPG, WebP).";
      return;
    }
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      loadedImg = img;
      loadedName = file.name.replace(/\.[^.]+$/, "").trim() || "Converted image";
      dropLabel.textContent = `Selected: ${file.name} (${img.naturalWidth}×${img.naturalHeight})`;
      runBtn.disabled = false;
      drawOrigPreview();
      runConversion();
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      stats.textContent = "Could not decode that image.";
    };
    img.src = url;
  }

  openBtn.addEventListener("click", () => {
    dlg.showModal();
  });
  $("closeVecBtn").addEventListener("click", () => {
    if (controller) controller.abort();
    dlg.close();
  });
  dlg.addEventListener("close", () => {
    if (controller) controller.abort();
  });

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      fileInput.click();
    }
  });
  dropzone.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    dropzone.classList.add("is-dragover");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-dragover"));
  dropzone.addEventListener("drop", (ev) => {
    ev.preventDefault();
    dropzone.classList.remove("is-dragover");
    const file = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
    if (file) loadImageFile(file);
  });
  fileInput.addEventListener("change", (ev) => {
    const file = ev.target.files && ev.target.files[0];
    ev.target.value = "";
    if (file) loadImageFile(file);
  });

  layersSlider.addEventListener("input", () => {
    layersVal.textContent = layersSlider.value;
  });
  zoomSlider.addEventListener("input", () => {
    zoomVal.textContent = `${(Number(zoomSlider.value) / 100).toFixed(1)}×`;
    drawOrigPreview();
  });
  fitSel.addEventListener("change", drawOrigPreview);
  runBtn.addEventListener("click", runConversion);
  refineBtn.addEventListener("click", runExtraRefine);
  stopBtn.addEventListener("click", () => {
    if (controller) controller.abort();
  });
  applyBtn.addEventListener("click", () => {
    if (!generatedLayers || !generatedLayers.length) return;
    pushUndo();
    state.layers = generatedLayers.map((L, idx) => ({ ...L, index: idx }));
    state.selected = state.layers.length - 1;
    state.group = null;
    state.slot = 0;
    if (loadedName) {
      state.label = loadedName;
      $("emblemName").value = loadedName;
    }
    if (keepTraceCheck.checked && lastPreparedCanvas) {
      state.traceImage = lastPreparedCanvas;
      state.traceOpacity = Number($("traceOpacity").value) / 100;
      $("traceWrap").hidden = false;
    }
    state.dirty = true;
    dlg.close();
    renderAll();
    $("statusLine").textContent = `Loaded ${state.layers.length} converted layers — tweak or Save`;
  });
}

function bindToolbar() {
  if (isDemoMode()) {
    const showBtn = $("showBtn");
    if (showBtn) showBtn.hidden = true;
    const backLink = $("backLink");
    if (backLink) {
      backLink.href = "https://github.com/userchrispo/b02-emblem-editor";
      backLink.textContent = "← GitHub repo";
    }
  }
  $("gridToggle").addEventListener("change", () => {
    state.showGrid = $("gridToggle").checked;
    redraw();
  });
  $("snapToggle").addEventListener("change", () => {
    state.snapEnabled = $("snapToggle").checked;
  });
  $("traceOpacity").addEventListener("input", () => {
    state.traceOpacity = Number($("traceOpacity").value) / 100;
    redraw();
  });
  $("traceClearBtn").addEventListener("click", () => {
    state.traceImage = null;
    $("traceWrap").hidden = true;
    redraw();
  });
  $("newBtn").addEventListener("click", newEmblem);
  $("openBtn").addEventListener("click", showOpenDialog);
  $("closeOpenBtn").addEventListener("click", () => $("openDialog").close());
  bindVectorizerModal();
  $("saveBtn").addEventListener("click", async () => {
    try {
      await saveEmblem({});
    } catch (err) {
      $("statusLine").textContent = "Save failed";
      console.error(err);
    }
  });
  $("showBtn").addEventListener("click", async () => {
    try {
      await saveEmblem({ show: true });
      $("statusLine").textContent = "Saved and armed for Show — open your emblem editor on the PS5";
    } catch (err) {
      $("statusLine").textContent = "Could not arm Show mode";
      console.error(err);
    }
  });
  $("downloadBtn").addEventListener("click", () => downloadBin().catch(console.error));
  $("exportPngBtn").addEventListener("click", () => downloadPng().catch(console.error));
  $("importBtn").addEventListener("click", () => $("importFile").click());
  $("importFile").addEventListener("change", async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    ev.target.value = "";
    if (!file) return;
    const bytes = new Uint8Array(await file.arrayBuffer());
    const label = file.name.replace(/\.bin$/i, "").trim() || "Imported emblem";
    try {
      const res = await importBinBytes(bytes, label);
      await openEmblem(res.group, res.slot);
    } catch (err) {
      $("statusLine").textContent = err.message || "Could not import that file";
    }
  });
  $("emblemName").addEventListener("input", () => {
    state.label = $("emblemName").value;
    state.dirty = true;
    setStatus();
  });
  $("shapeSearch").addEventListener("input", () => {
    state.query = $("shapeSearch").value;
    renderPalette();
  });
  window.addEventListener("beforeunload", (ev) => {
    if (!state.dirty) return;
    ev.preventDefault();
    ev.returnValue = "";
  });
}

async function init() {
  bindInspector();
  bindCanvas();
  bindKeys();
  bindToolbar();
  $("statusLine").textContent = "Loading shapes…";
  const [shapes, loadedAtlas] = await Promise.all([
    fetchShapes(),
    loadAtlas().catch((err) => {
      console.error(err);
      return null;
    }),
  ]);
  state.shapes = shapes;
  atlas = loadedAtlas;
  if (!state.shapes.some((s) => s.category === state.category) && state.shapes[0]) {
    state.category = state.shapes[0].category;
  }
  renderPalette();

  const params = new URLSearchParams(location.search);
  const group = params.get("group");
  const slot = params.get("slot");
  if (group != null && slot != null) {
    try {
      await openEmblem(group, slot);
      return;
    } catch (err) {
      console.error(err);
      $("statusLine").textContent = "Could not open that emblem — starting a new one";
    }
  } else if (isDemoMode()) {
    const list = await fetchEmblemList().catch(() => []);
    if (list.length) {
      await openEmblem(list[0].group, list[0].slot);
      return;
    }
  }
  renderAll();
}

init().catch((err) => {
  $("statusLine").textContent = "Failed to start editor";
  console.error(err);
});
