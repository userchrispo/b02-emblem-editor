// BO2 Emblem Toolkit - control panel frontend.
// Plain JS, no build step, no dependencies - keeps the tool easy to run
// for non-technical users.

const modeHints = {
  off: "The proxy is transparent. Nothing is captured or changed.",
  capture: "Open a player's profile or channel on your PS5 - their emblem is saved below automatically.",
  inject: "Open your own emblem editor on the PS5. The selected emblem below loads there, ready to save.",
};

let currentStatus = null;
let currentEmblems = [];
let libraryQuery = "";
let renderedKeys = "";
let selectInFlight = null;

const CAPTURE_SEQ_KEY = "emblemtool.captureSeq";
const LIVE_FRESH_S = 90;

function formatAgo(ts) {
  if (!ts) return "";
  const sec = Math.max(0, Math.round(Date.now() / 1000 - Number(ts)));
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  return `${Math.round(sec / 3600)}h ago`;
}

function showToast(text) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => { el.hidden = true; }, 6000);
}

function renderLive(status) {
  const el = document.getElementById("liveValue");
  const hit = status && status.last_hit;
  el.classList.remove("live-ok", "live-stale");
  if (!hit || !hit.ts) {
    el.textContent = "no emblem request yet";
    el.classList.add("live-stale");
    return;
  }
  const ago = formatAgo(hit.ts);
  const age = Date.now() / 1000 - Number(hit.ts);
  if (age <= LIVE_FRESH_S) {
    el.textContent = ago;
    el.classList.add("live-ok");
  } else {
    el.textContent = ago + " — open a profile if this is stale";
    el.classList.add("live-stale");
  }
}

function renderSessionBanner(status) {
  const el = document.getElementById("sessionBanner");
  el.hidden = !(status && status.needs_session_reset);
}

function maybeToastCapture(status) {
  const cap = status && status.last_capture;
  if (!cap || cap.seq == null) return;
  const seen = Number(sessionStorage.getItem(CAPTURE_SEQ_KEY) || "0");
  if (cap.seq <= seen) return;
  const recent = Date.now() / 1000 - Number(cap.ts || 0) <= 15;
  const isNewThisVisit = seen > 0 || recent;
  sessionStorage.setItem(CAPTURE_SEQ_KEY, String(cap.seq));
  if (isNewThisVisit) showToast("Captured an emblem — it is in Your emblems.");
}

document.getElementById("sessionClearBtn").addEventListener("click", async () => {
  await postJSON("/api/session-reset", {});
  if (currentStatus) currentStatus.needs_session_reset = false;
  renderSessionBanner(currentStatus);
});

async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

// ---------- network info ----------

async function loadNetworkInfo() {
  const info = await getJSON("/api/network-info");
  const el = document.getElementById("setupValue");
  el.textContent = info.lan_ip
    ? `${info.lan_ip} : ${info.proxy_port}`
    : "couldn't detect - see docs/INSTALL.md";
}

// ---------- mode ----------

function renderMode() {
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === currentStatus.mode);
  });
  document.getElementById("modeHint").textContent = modeHints[currentStatus.mode] || "";
}

async function setMode(mode) {
  await postJSON("/api/mode", { mode });
  await refreshStatus();
}

document.querySelectorAll(".mode-btn").forEach((btn) => {
  btn.addEventListener("click", () => setMode(btn.dataset.mode));
});

// ---------- emblem list (flat, single-select) ----------

function fmtDate(s) {
  if (!s) return "";
  return s.replace(" ", " · ").slice(0, 16);
}

function listKeys(list) {
  return list.map((e) => `${e.group}:${e.slot}`).join(",");
}

function visibleEmblems() {
  const q = libraryQuery.trim().toLowerCase();
  if (!q) return currentEmblems;
  return currentEmblems.filter((e) => (e.label || "").toLowerCase().includes(q));
}

function riskText(risks) {
  if (!risks || !risks.length) return "";
  const bits = risks.map((r) => (r === "ranks" ? "ranks" : "weapons"));
  return "Uses " + bits.join(" and ") + " — game may refuse if locked";
}

function thumbSrc(e) {
  const v = e.mtime ? `?v=${e.mtime}` : "";
  return `/api/render/${encodeURIComponent(e.group)}/${e.slot}.png${v}`;
}

function selectedRef() {
  if (selectInFlight) return selectInFlight;
  return currentStatus && currentStatus.selected;
}

function isSelected(e) {
  const sel = selectedRef();
  return sel && sel.group === e.group && Number(sel.slot) === Number(e.slot);
}

function paintSelection() {
  document.querySelectorAll("#emblemList .emblem-card").forEach((card) => {
    card.classList.toggle(
      "selected",
      isSelected({ group: card.dataset.group, slot: Number(card.dataset.slot) }),
    );
  });
}

function fillCard(card, e) {
  card.dataset.group = e.group;
  card.dataset.slot = String(e.slot);
  card.classList.toggle("selected", isSelected(e));
  const img = card.querySelector(".emblem-thumb");
  const src = thumbSrc(e);
  if (img.getAttribute("src") !== src) {
    img.loading = "lazy";
    img.fetchPriority = "low";
    img.src = src;
  }
  card.querySelector(".emblem-date").textContent =
    (e.source === "editor" ? "Editor · " : "") + fmtDate(e.captured_at);
  const risk = card.querySelector(".emblem-risk");
  const note = riskText(e.risks);
  risk.hidden = !note;
  risk.textContent = note;
  const labelInput = card.querySelector(".emblem-label-input");
  const nextLabel = e.label || `Emblem from ${fmtDate(e.captured_at)}`;
  if (document.activeElement !== labelInput && labelInput.value !== nextLabel) {
    labelInput.value = nextLabel;
  }
}

function bindCard(card) {
  const editBtn = card.querySelector(".emblem-edit");
  editBtn.addEventListener("click", (evt) => {
    evt.stopPropagation();
    location.href = `/editor.html?group=${encodeURIComponent(card.dataset.group)}&slot=${card.dataset.slot}`;
  });
  const inspectBtn = card.querySelector(".emblem-inspect");
  if (inspectBtn) {
    inspectBtn.addEventListener("click", (evt) => {
      evt.stopPropagation();
      openBinaryInspector(card.dataset.group, card.dataset.slot);
    });
  }
  const labelInput = card.querySelector(".emblem-label-input");
  labelInput.addEventListener("click", (evt) => evt.stopPropagation());
  labelInput.addEventListener("change", async () => {
    await postJSON(
      `/api/emblems/${encodeURIComponent(card.dataset.group)}/${card.dataset.slot}/label`,
      { label: labelInput.value },
    );
  });
  card.addEventListener("click", () => {
    selectEmblem({ group: card.dataset.group, slot: Number(card.dataset.slot) });
  });
  card.querySelector(".emblem-delete").addEventListener("click", async (evt) => {
    evt.stopPropagation();
    const name = card.querySelector(".emblem-label-input").value.trim() || "this emblem";
    if (!confirm(`Delete “${name}”? This cannot be undone.`)) return;
    await postJSON(
      `/api/emblems/${encodeURIComponent(card.dataset.group)}/${card.dataset.slot}/delete`,
      {},
    );
    await refreshAll();
  });
}

function selectEmblem(e) {
  selectInFlight = { group: e.group, slot: e.slot };
  if (!currentStatus) currentStatus = { mode: "off", selected: null };
  currentStatus.selected = selectInFlight;
  paintSelection();
  postJSON("/api/select", { group: e.group, slot: e.slot })
    .then(() => {
      if (
        selectInFlight &&
        selectInFlight.group === e.group &&
        Number(selectInFlight.slot) === Number(e.slot)
      ) {
        selectInFlight = null;
      }
    })
    .catch(() => {
      selectInFlight = null;
      refreshStatus();
    });
}

function renderEmblems() {
  const list = document.getElementById("emblemList");
  const empty = document.getElementById("emblemsEmpty");
  const shown = visibleEmblems();
  empty.style.display = currentEmblems.length ? "none" : "";
  const keys = listKeys(shown);
  if (keys === renderedKeys && list.children.length === shown.length) {
    shown.forEach((e, i) => fillCard(list.children[i], e));
    return;
  }
  renderedKeys = keys;
  list.innerHTML = "";
  const tpl = document.getElementById("emblemCardTpl");
  for (const e of shown) {
    const node = tpl.content.cloneNode(true);
    const card = node.querySelector(".emblem-card");
    fillCard(card, e);
    bindCard(card);
    list.appendChild(node);
  }
}

document.getElementById("refreshBtn").addEventListener("click", refreshAll);
document.getElementById("librarySearch").addEventListener("input", () => {
  libraryQuery = document.getElementById("librarySearch").value;
  renderedKeys = "";
  renderEmblems();
});
document.getElementById("importBtn").addEventListener("click", () => {
  document.getElementById("importFile").click();
});
document.getElementById("importFile").addEventListener("change", async (ev) => {
  const file = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!file) return;
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  const label = file.name.replace(/\.bin$/i, "").trim() || "Imported emblem";
  const res = await postJSON("/api/emblems/import", {
    data_base64: btoa(bin),
    label,
  });
  if (!res.ok) {
    showToast(res.error || "Could not import that file");
    return;
  }
  await refreshAll();
  showToast("Imported — select it, then Show.");
});

// ---------- traffic log & binary inspector ----------

function renderTraffic(status) {
  const events = (status && status.events) || [];
  const countEl = document.getElementById("trafficCount");
  const tbody = document.getElementById("trafficBody");
  if (!tbody) return;
  if (countEl) {
    countEl.textContent = `${events.length} request${events.length === 1 ? "" : "s"}`;
  }
  tbody.innerHTML = "";
  if (!events.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "muted";
    td.textContent = "No emblem requests observed yet.";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  for (const ev of events.slice(0, 25)) {
    const tr = document.createElement("tr");
    const hdrs = ev.cache_headers
      ? Object.entries(ev.cache_headers).map(([k, v]) => `${k}: ${v}`).join("; ")
      : "—";
    const cells = [
      (ev.at || "").slice(11) || "—",
      ev.kind || "—",
      ev.slot != null ? `slot_${ev.slot}` : "—",
      ev.size != null ? `${ev.size} B` : "—",
      hdrs,
    ];
    cells.forEach((val, idx) => {
      const td = document.createElement("td");
      if (idx === 1) {
        const badge = document.createElement("span");
        badge.className = `kind-pill kind-${ev.kind || "passthrough"}`;
        badge.textContent = val;
        td.appendChild(badge);
      } else {
        td.textContent = val;
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }
}

async function openBinaryInspector(group, slot) {
  const dlg = document.getElementById("inspectDialog");
  if (!dlg) return;
  const info = await getJSON(`/api/emblems/${encodeURIComponent(group)}/${slot}/inspect`);
  document.getElementById("inspectTitle").textContent =
    `Binary inspector — ${info.label || `${group}:${slot}`}`;
  document.getElementById("inspectSummary").textContent =
    `${info.used_layers} / ${info.max_layers} layers · ${info.body_bytes} B body (${info.total_bytes} B total, ${info.layer_size} B/layer)`;
  const hdrEl = document.getElementById("inspectHeaders");
  if (info.has_http_headers && info.http_headers) {
    hdrEl.textContent = info.http_headers;
    hdrEl.hidden = false;
  } else {
    hdrEl.hidden = true;
  }
  const tbody = document.getElementById("inspectBody");
  tbody.innerHTML = "";
  for (const rec of info.records || []) {
    const tr = document.createElement("tr");
    const flags = [rec.outlined ? "OUTLINE" : "", rec.flipped ? "FLIP" : ""]
      .filter(Boolean).join(", ") || "—";
    const cells = [
      String(rec.index),
      rec.offset_hex,
      `${rec.shape} (${rec.shape_label})`,
      rec.rgba.join(", "),
      rec.pos.join(", "),
      rec.scale.join(", "),
      `${rec.rot}°`,
      flags,
    ];
    for (const val of cells) {
      const td = document.createElement("td");
      td.textContent = val;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  dlg.showModal();
}

const closeInspectBtn = document.getElementById("closeInspectBtn");
if (closeInspectBtn) {
  closeInspectBtn.addEventListener("click", () => {
    document.getElementById("inspectDialog").close();
  });
}

// ---------- refresh loop + SSE ----------

function applyStatus(next) {
  currentStatus = {
    mode: next.mode,
    selected: selectInFlight || next.selected,
    last_hit: next.last_hit,
    last_capture: next.last_capture,
    needs_session_reset: next.needs_session_reset,
    events: next.events || [],
  };
  renderMode();
  paintSelection();
  renderLive(currentStatus);
  renderSessionBanner(currentStatus);
  renderTraffic(currentStatus);
  maybeToastCapture(currentStatus);
}

async function refreshStatus() {
  applyStatus(await getJSON("/api/status"));
}

async function refreshEmblemList() {
  currentEmblems = await getJSON("/api/emblems");
  renderEmblems();
}

async function refreshAll() {
  const [status, emblems] = await Promise.all([
    getJSON("/api/status"),
    getJSON("/api/emblems"),
  ]);
  currentEmblems = emblems;
  applyStatus(status);
  renderEmblems();
}

function connectSSE() {
  const badge = document.getElementById("sseBadge");
  if (typeof EventSource === "undefined") return;
  const es = new EventSource("/api/events");
  const onStatus = (ev) => {
    try {
      applyStatus(JSON.parse(ev.data));
    } catch (_) {}
  };
  es.addEventListener("open", () => {
    if (badge) { badge.textContent = "LIVE"; badge.classList.add("connected"); }
  });
  es.addEventListener("status", onStatus);
  es.addEventListener("mode", onStatus);
  es.addEventListener("select", onStatus);
  es.addEventListener("session_reset", onStatus);
  es.addEventListener("activity", (ev) => {
    onStatus(ev);
    refreshEmblemList();
  });
  es.addEventListener("library", () => refreshEmblemList());
  es.addEventListener("error", () => {
    if (badge) { badge.textContent = "POLL"; badge.classList.remove("connected"); }
  });
}

loadNetworkInfo();
refreshAll();
connectSSE();
setInterval(refreshStatus, 10000);
setInterval(refreshEmblemList, 15000);
