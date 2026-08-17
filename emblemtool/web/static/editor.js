// PC emblem editor. Plain JS, no build step — same contract as the control panel.
const MAX_LAYERS = 32;
const CAT_LABELS = {
  tools: "Shapes",
  type: "Letters",
  ranks: "Ranks",
  gear: "Weapons",
  emblems: "Emblems",
};

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
  dragging: null,
};

const shapeImgs = new Map();
const $ = (id) => document.getElementById(id);

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function postJSON(url, body) {
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

function rgbHex(L) {
  const h = (v) => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, "0");
  return `#${h(L.r)}${h(L.g)}${h(L.b)}`;
}
function hexRgb(hex) {
  const n = hex.replace("#", "");
  return {
    r: parseInt(n.slice(0, 2), 16) / 255,
    g: parseInt(n.slice(2, 4), 16) / 255,
    b: parseInt(n.slice(4, 6), 16) / 255,
  };
}
function scaleToPct(s) { return Math.pow(2, s) * 100; }
function pctToScale(p) { return Math.log2(Math.max(1, Number(p)) / 100); }

function shapeName(id) {
  const s = state.shapes.find((x) => x.id === id);
  return s ? s.name : `Shape ${id}`;
}

function markDirty() {
  state.dirty = true;
  renderAll();
}

function setStatus() {
  const bits = [];
  bits.push(state.group ? `Saved ${state.group}` : "Unsaved");
  if (state.dirty) bits.push("unsaved changes");
  bits.push(`${state.layers.length} / ${MAX_LAYERS} layers`);
  $("statusLine").textContent = bits.join(" · ");
}

function loadShapeImage(id) {
  if (shapeImgs.has(id)) return shapeImgs.get(id);
  const p = new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = `/api/shapes/${id}.png`;
  });
  shapeImgs.set(id, p);
  return p;
}

function tintGlyph(img, L) {
  const src = document.createElement("canvas");
  src.width = img.width;
  src.height = img.height;
  const sctx = src.getContext("2d");
  sctx.drawImage(img, 0, 0);
  sctx.globalCompositeOperation = "multiply";
  sctx.fillStyle = `rgb(${Math.round(L.r * 255)},${Math.round(L.g * 255)},${Math.round(L.b * 255)})`;
  sctx.fillRect(0, 0, src.width, src.height);
  sctx.globalCompositeOperation = "destination-in";
  sctx.drawImage(img, 0, 0);
  return src;
}

let drawGen = 0;

async function drawCanvas() {
  const canvas = $("emblemCanvas");
  const ctx = canvas.getContext("2d");
  const size = canvas.width;
  const gen = ++drawGen;
  ctx.clearRect(0, 0, size, size);

  const ordered = [...state.layers].sort((a, b) => a.index - b.index);
  for (const L of ordered) {
    const img = await loadShapeImage(L.shape);
    if (gen !== drawGen) return;
    const w = Math.max(1, Math.round(Math.pow(2, L.sx) * size));
    const h = Math.max(1, Math.round(Math.pow(2, L.sy) * size));
    ctx.save();
    ctx.translate(size / 2 + L.x * size, size / 2 + L.y * size);
    ctx.rotate((L.rot * Math.PI) / 180);
    if (L.flipped) ctx.scale(-1, 1);
    ctx.globalAlpha = Math.max(0, Math.min(1, L.a));
    if (img) {
      const glyph = tintGlyph(img, L);
      ctx.drawImage(glyph, -w / 2, -h / 2, w, h);
    } else {
      ctx.fillStyle = rgbHex(L);
      ctx.fillRect(-w / 2, -h / 2, w, h);
    }
    ctx.restore();
  }

  if (state.selected >= 0 && state.layers[state.selected]) {
    const L = state.layers[state.selected];
    const w = Math.pow(2, L.sx) * size;
    const h = Math.pow(2, L.sy) * size;
    ctx.save();
    ctx.translate(size / 2 + L.x * size, size / 2 + L.y * size);
    ctx.rotate((L.rot * Math.PI) / 180);
    ctx.strokeStyle = "#ff8000";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(-w / 2, -h / 2, w, h);
    ctx.restore();
  }
}

function reindex() {
  state.layers.forEach((L, i) => { L.index = i; });
}

function addLayer(shapeId) {
  if (state.layers.length >= MAX_LAYERS) {
    setStatus();
    $("statusLine").textContent = "32 layer limit — remove one first";
    return;
  }
  state.layers.push({
    index: state.layers.length,
    shape: shapeId,
    r: 1, g: 1, b: 1, a: 1,
    x: 0, y: 0,
    sx: -1, sy: -1,
    rot: 0,
    outlined: false,
    flipped: false,
  });
  state.selected = state.layers.length - 1;
  markDirty();
}

function removeSelected() {
  if (state.selected < 0) return;
  state.layers.splice(state.selected, 1);
  reindex();
  state.selected = Math.min(state.selected, state.layers.length - 1);
  markDirty();
}

function payload() {
  return {
    layers: state.layers,
    label: $("emblemName").value.trim() || "Untitled emblem",
    group: state.group,
    slot: state.slot,
  };
}

async function saveEmblem(extra) {
  const body = { ...payload(), ...extra };
  const res = await postJSON("/api/editor/save", body);
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
    const img = document.createElement("img");
    img.alt = "";
    img.src = `/api/shapes/${s.id}.png`;
    btn.appendChild(img);
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
    li.className = "layer-item" + (i === state.selected ? " selected" : "");
    li.draggable = true;
    li.dataset.index = String(i);
    li.innerHTML = `
      <span class="layer-handle" aria-hidden="true">⋮⋮</span>
      <span class="layer-swatch"></span>
      <span class="layer-name"></span>`;
    li.querySelector(".layer-swatch").style.background = rgbHex(L);
    li.querySelector(".layer-name").textContent = shapeName(L.shape) + (L.outlined ? " · outline" : "");
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
      const [moved] = state.layers.splice(from, 1);
      state.layers.splice(to, 0, moved);
      reindex();
      state.selected = to;
      markDirty();
    });
    list.appendChild(li);
  }
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
  drawCanvas();
}

function bindInspector() {
  $("propColor").addEventListener("input", () => {
    const L = state.layers[state.selected];
    if (!L) return;
    Object.assign(L, hexRgb($("propColor").value));
    markDirty();
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
    L.outlined = $("propOutline").checked;
    markDirty();
  });
  $("propFlip").addEventListener("change", () => {
    const L = state.layers[state.selected];
    if (!L) return;
    L.flipped = $("propFlip").checked;
    markDirty();
  });
  $("deleteLayerBtn").addEventListener("click", removeSelected);
}

function canvasPos(ev) {
  const canvas = $("emblemCanvas");
  const rect = canvas.getBoundingClientRect();
  return {
    x: (ev.clientX - rect.left) / rect.width - 0.5,
    y: (ev.clientY - rect.top) / rect.height - 0.5,
  };
}

function hitTest(x, y) {
  for (let i = state.layers.length - 1; i >= 0; i--) {
    const L = state.layers[i];
    const w = Math.pow(2, L.sx);
    const h = Math.pow(2, L.sy);
    let dx = x - L.x;
    let dy = y - L.y;
    const rad = (-L.rot * Math.PI) / 180;
    const cos = Math.cos(rad);
    const sin = Math.sin(rad);
    let rx = dx * cos - dy * sin;
    let ry = dx * sin + dy * cos;
    if (L.flipped) rx = -rx;
    if (Math.abs(rx) <= w / 2 && Math.abs(ry) <= h / 2) return i;
  }
  return -1;
}

function bindCanvas() {
  const canvas = $("emblemCanvas");
  canvas.addEventListener("pointerdown", (ev) => {
    canvas.setPointerCapture(ev.pointerId);
    const p = canvasPos(ev);
    const hit = hitTest(p.x, p.y);
    state.selected = hit;
    if (hit >= 0) {
      const L = state.layers[hit];
      state.dragging = { dx: p.x - L.x, dy: p.y - L.y };
    } else {
      state.dragging = null;
    }
    renderAll();
  });
  canvas.addEventListener("pointermove", (ev) => {
    if (!state.dragging || state.selected < 0) return;
    const p = canvasPos(ev);
    const L = state.layers[state.selected];
    L.x = p.x - state.dragging.dx;
    L.y = p.y - state.dragging.dy;
    state.dirty = true;
    drawCanvas();
    setStatus();
  });
  canvas.addEventListener("pointerup", () => {
    if (state.dragging) renderAll();
    state.dragging = null;
  });
}

function bindKeys() {
  document.addEventListener("keydown", (ev) => {
    const tag = (ev.target && ev.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    const L = state.layers[state.selected];
    if (ev.key === "Delete" || ev.key === "Backspace") {
      ev.preventDefault();
      removeSelected();
      return;
    }
    if (!L) return;
    const step = ev.shiftKey ? 0.05 : 0.01;
    if (ev.key === "ArrowLeft") { L.x -= step; markDirty(); ev.preventDefault(); }
    if (ev.key === "ArrowRight") { L.x += step; markDirty(); ev.preventDefault(); }
    if (ev.key === "ArrowUp") { L.y -= step; markDirty(); ev.preventDefault(); }
    if (ev.key === "ArrowDown") { L.y += step; markDirty(); ev.preventDefault(); }
    if (ev.key === "[") { L.rot = (L.rot - (ev.shiftKey ? 15 : 5) + 360) % 360; markDirty(); }
    if (ev.key === "]") { L.rot = (L.rot + (ev.shiftKey ? 15 : 5)) % 360; markDirty(); }
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
  renderAll();
}

async function openEmblem(group, slot) {
  const data = await getJSON(`/api/emblems/${encodeURIComponent(group)}/${slot}/layers`);
  state.layers = data.layers || [];
  reindex();
  state.selected = state.layers.length ? state.layers.length - 1 : -1;
  state.label = data.label || "";
  state.group = data.source === "editor" ? data.group : null;
  state.slot = data.source === "editor" ? data.slot : 0;
  state.dirty = false;
  const dlg = $("openDialog");
  if (dlg.open) dlg.close();
  renderAll();
}

async function showOpenDialog() {
  const emblems = await getJSON("/api/emblems");
  $("openEmpty").style.display = emblems.length ? "none" : "";
  const grid = $("openGrid");
  grid.innerHTML = "";
  for (const e of emblems) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "open-card";
    const img = document.createElement("img");
    img.alt = "";
    img.src = `/api/render/${encodeURIComponent(e.group)}/${e.slot}.png`;
    const span = document.createElement("span");
    span.textContent = e.label || (e.source === "editor" ? "Untitled" : "Capture");
    btn.append(img, span);
    btn.addEventListener("click", () => openEmblem(e.group, e.slot));
    grid.appendChild(btn);
  }
  $("openDialog").showModal();
}

async function downloadBin() {
  const r = await fetch("/api/editor/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload()),
  });
  if (!r.ok) throw new Error("export failed");
  const blob = await r.blob();
  const a = document.createElement("a");
  const name = (payload().label || "emblem").replace(/[^\w\- ]+/g, "").trim() || "emblem";
  a.href = URL.createObjectURL(blob);
  a.download = `${name}.bin`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function bindToolbar() {
  $("newBtn").addEventListener("click", newEmblem);
  $("openBtn").addEventListener("click", showOpenDialog);
  $("closeOpenBtn").addEventListener("click", () => $("openDialog").close());
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
  state.shapes = await getJSON("/api/shapes");
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
    } catch (err) {
      console.error(err);
      $("statusLine").textContent = "Could not open that emblem — starting a new one";
      renderAll();
    }
  } else {
    renderAll();
  }
}

init().catch((err) => {
  $("statusLine").textContent = "Failed to start editor";
  console.error(err);
});
