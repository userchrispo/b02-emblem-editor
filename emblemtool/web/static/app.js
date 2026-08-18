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
let renderedKeys = "";
let selectInFlight = null;

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
  empty.style.display = currentEmblems.length ? "none" : "";
  const keys = listKeys(currentEmblems);
  if (keys === renderedKeys && list.children.length === currentEmblems.length) {
    currentEmblems.forEach((e, i) => fillCard(list.children[i], e));
    return;
  }
  renderedKeys = keys;
  list.innerHTML = "";
  const tpl = document.getElementById("emblemCardTpl");
  for (const e of currentEmblems) {
    const node = tpl.content.cloneNode(true);
    const card = node.querySelector(".emblem-card");
    fillCard(card, e);
    bindCard(card);
    list.appendChild(node);
  }
}

document.getElementById("refreshBtn").addEventListener("click", refreshAll);

// ---------- refresh loop ----------

async function refreshStatus() {
  const next = await getJSON("/api/status");
  currentStatus = {
    mode: next.mode,
    selected: selectInFlight || next.selected,
  };
  renderMode();
  paintSelection();
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
  currentStatus = {
    mode: status.mode,
    selected: selectInFlight || status.selected,
  };
  currentEmblems = emblems;
  renderMode();
  renderEmblems();
}

loadNetworkInfo();
refreshAll();
setInterval(refreshStatus, 2500);
setInterval(refreshEmblemList, 5000);
