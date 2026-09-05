// A-Train browser demo client (Phase 2.5).
//
// A thin client of the simulator. It subscribes to the `/ws` snapshot stream
// so state updates continuously without polling, and submits run/pause/reset/
// time-mode/step and train-control commands through the REST API. The page
// renders every received snapshot; rejected commands surface their error
// without changing the displayed state. The UI contains no simulation rules.

const API = "/api";
const state = {
  status: null,
  trains: [],
  selectedTrainId: null,
  selectedCab: null,
  error: null,
  notice: null,
  ws: "connecting",
  dirty: { drive: false },
};

const $ = (id) => document.getElementById(id);

async function fetchJson(path, options) {
  const res = await fetch(API + path, options);
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  return { ok: res.ok, status: res.status, data };
}

function selectedTrain() {
  return state.trains.find((t) => t.train_id === state.selectedTrainId) || null;
}

function setState(status, trains) {
  state.status = status;
  state.trains = trains;
  state.error = null;
  state.notice = null;
  if (!trains.some((t) => t.train_id === state.selectedTrainId)) {
    state.selectedTrainId = trains[0] ? trains[0].train_id : null;
  }
  const sel = selectedTrain();
  if (sel && !sel.cab_ids.includes(state.selectedCab)) {
    state.selectedCab = sel.cab_ids[0];
  }
  render();
}

function applySnapshot(snap) {
  setState(
    {
      simulation_state: snap.simulation_state,
      simulation_time: snap.simulation_time,
      time_mode: snap.time_mode,
      time_multiplier: snap.time_multiplier,
    },
    snap.trains || []
  );
}

async function refresh() {
  // REST fallback: initial load, and when the socket is not open.
  const [statusRes, trainsRes] = await Promise.all([
    fetchJson("/status"),
    fetchJson("/trains"),
  ]);
  if (statusRes.ok && trainsRes.ok) {
    setState(statusRes.data, (trainsRes.data && trainsRes.data.trains) || []);
  } else {
    render();
  }
}

async function postCommand(path, body) {
  const res = await fetchJson(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (res.ok) {
    state.error = null;
    state.notice = "ok";
    // The resulting snapshot is delivered over /ws. Fall back to REST only if
    // the socket is not open.
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      await refresh();
    } else {
      renderMessage();
    }
  } else {
    state.error =
      (res.data && res.data.detail) || `Request failed (HTTP ${res.status})`;
    state.notice = null;
    renderMessage();
  }
}

function fmt(n, d = 4) {
  return Number.isFinite(n) ? n.toFixed(d) : String(n);
}

function hexToBase64(value) {
  const hex = value.replace(/\s+/g, "");
  if (!hex || hex.length % 2 !== 0 || !/^[0-9a-f]+$/i.test(hex)) {
    throw new Error("BTM payload must contain an even number of hexadecimal digits");
  }
  const bytes = new Uint8Array(hex.length / 2);
  for (let index = 0; index < bytes.length; index += 1) {
    bytes[index] = parseInt(hex.slice(index * 2, index * 2 + 2), 16);
  }
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function renderBtmEncoding() {
  const preview = $("btm-base64");
  const value = $("btm-payload").value;
  if (!value.trim()) {
    preview.textContent = "";
    return;
  }
  try {
    preview.textContent = `base64: ${hexToBase64(value)}`;
  } catch (error) {
    preview.textContent = error.message;
  }
}

function renderStatus() {
  const s = state.status;
  $("sim-state").textContent = s ? s.simulation_state : "—";
  $("sim-time").textContent = s ? fmt(s.simulation_time, 3) : "0.000";
  $("sim-mode").textContent = s ? s.time_mode : "—";
  $("sim-mult").textContent = s ? fmt(s.time_multiplier, 2) : "—";
}

function renderTrainSelectors() {
  const trainSel = $("train-select");
  const cabSel = $("cab-select");
  if (
    trainSel.children.length !== state.trains.length ||
    ![...trainSel.options].some((o) => o.value === state.selectedTrainId)
  ) {
    trainSel.innerHTML = "";
    for (const t of state.trains) {
      const o = document.createElement("option");
      o.value = t.train_id;
      o.textContent = t.train_id;
      trainSel.appendChild(o);
    }
  }
  trainSel.value = state.selectedTrainId || "";
  const sel = selectedTrain();
  if (sel) {
    if (
      cabSel.children.length !== sel.cab_ids.length ||
      ![...cabSel.options].some((o) => Number(o.value) === state.selectedCab)
    ) {
      cabSel.innerHTML = "";
      for (const c of sel.cab_ids) {
        const o = document.createElement("option");
        o.value = String(c);
        o.textContent = String(c);
        cabSel.appendChild(o);
      }
    }
    cabSel.value = String(state.selectedCab);
  } else {
    cabSel.innerHTML = "";
  }
}

function renderTrainState() {
  const sel = selectedTrain();
  const pre = $("train-state-pre");
  const equipmentEl = $("train-equipment");
  if (!sel) {
    pre.textContent = "no trains";
    equipmentEl.replaceChildren();
    return;
  }
  const equipment = sel.equipment || {};
  const cabs = (sel.equipment && sel.equipment.cab) || [];
  const cabText = cabs.length
    ? cabs.map((e) => `${e.cab_id}${e.active ? " (on)" : ""}`).join(", ")
    : sel.cab_ids.join(", ");
  const lines = [
    `train_id        ${sel.train_id}`,
    `cabs            ${cabText}`,
    `position        ${fmt(sel.position)} m`,
    `speed           ${fmt(sel.speed)} m/s`,
    `acceleration    ${fmt(sel.acceleration)} m/s^2`,
    `drive_demand    ${fmt(sel.drive_demand)}`,
  ];
  pre.textContent = lines.join("\n");

  renderEquipment(equipmentEl, equipment);
  const doorState = (equipment.door && equipment.door.state) || "—";
  $("door-state").textContent = doorState;
  syncSlider("drive", sel.drive_demand);
}

function renderEquipment(container, equipment) {
  container.replaceChildren();
  const entries = Object.entries(equipment);
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "equipment-empty";
    empty.textContent = "No equipment reported";
    container.appendChild(empty);
    return;
  }

  for (const [key, value] of entries) {
    const card = document.createElement("article");
    card.className = "equipment-card";

    const heading = document.createElement("h3");
    heading.textContent = key.replaceAll("_", " ");
    card.appendChild(heading);

    const details = document.createElement("pre");
    details.textContent = JSON.stringify(value, null, 2);
    card.appendChild(details);
    container.appendChild(card);
  }
}

// Sync a demand slider from the live snapshot, but never while it holds the
// user's unsaved intent (dirty). Otherwise each frame would snap it back to the
// train's last-applied demand between editing and clicking Apply, so Apply
// would read the reset value and send nothing.
function syncSlider(id, value) {
  if (state.dirty[id]) return;
  const el = $(id);
  el.value = String(value);
  $(`${id}-val`).textContent = fmt(value, 2);
}

function renderMessage() {
  const el = $("message");
  el.className = "message";
  if (state.error) {
    el.classList.add("error");
    el.textContent = `Rejected: ${state.error}`;
  } else if (state.notice) {
    el.classList.add("ok");
    el.textContent = "ok";
  } else {
    el.textContent = "";
  }
}

function renderWsStatus() {
  const el = $("ws-status");
  el.className = `ws-status ${state.ws}`;
  el.textContent =
    state.ws === "live" ? "live" : state.ws === "connecting" ? "connecting…" : "disconnected";
}

function render() {
  renderStatus();
  renderTrainSelectors();
  renderTrainState();
  renderMessage();
  renderWsStatus();
}

// -- WebSocket live stream ---------------------------------------------------

let ws = null;
let reconnectTimer = null;

function connectWs() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  try {
    ws = new WebSocket(`${proto}//${location.host}/ws`);
  } catch {
    scheduleReconnect();
    return;
  }
  state.ws = "connecting";
  renderWsStatus();

  ws.onopen = () => {
    state.ws = "live";
    renderWsStatus();
  };
  ws.onmessage = (ev) => {
    try {
      applySnapshot(JSON.parse(ev.data));
    } catch {
      /* ignore malformed frames */
    }
  };
  ws.onerror = () => {
    try {
      ws.close();
    } catch {
      /* ignore */
    }
  };
  ws.onclose = () => {
    state.ws = "disconnected";
    renderWsStatus();
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connectWs, 1000);
}

// -- Controls ----------------------------------------------------------------

function bind() {
  $("btn-run").onclick = () => postCommand("/simulation/start", {});
  $("btn-pause").onclick = () => postCommand("/simulation/pause", {});
  $("btn-reset").onclick = async () => {
    await postCommand("/simulation/reset", {});
    if (!state.error) {
      state.dirty.drive = false;
    }
  };
  $("btn-set-mode").onclick = () => {
    const mode = $("mode").value;
    const mult = mode === "SCALED" ? parseFloat($("multiplier").value) : null;
    postCommand("/simulation/time-mode", { mode, time_multiplier: mult });
  };
  $("btn-step").onclick = () =>
    postCommand("/simulation/step", { delta: parseFloat($("step-delta").value) });

  $("train-select").onchange = (e) => {
    state.selectedTrainId = e.target.value;
    const sel = selectedTrain();
    state.selectedCab = sel ? sel.cab_ids[0] : null;
    state.dirty.drive = false;
    render();
  };
  $("cab-select").onchange = (e) => {
    state.selectedCab = Number(e.target.value);
    render();
  };

  $("drive").oninput = (e) => {
    state.dirty.drive = true;
    $("drive-val").textContent = fmt(parseFloat(e.target.value), 2);
  };
  $("btn-apply-demand").onclick = async () => {
    await postCommand(`/trains/${state.selectedTrainId}/commands`, {
      cab_id: state.selectedCab,
      drive_demand: parseFloat($("drive").value),
    });
    if (!state.error) {
      state.dirty.drive = false;
    }
  };
  $("btn-door-open").onclick = () =>
    postCommand(`/trains/${state.selectedTrainId}/equipment/door`, {
      command: "open",
    });
  $("btn-door-close").onclick = () =>
    postCommand(`/trains/${state.selectedTrainId}/equipment/door`, {
      command: "close",
    });
  $("btn-send-btm").onclick = () =>
    sendBtmPayload();
  $("btm-payload").oninput = renderBtmEncoding;
}

async function sendBtmPayload() {
  let data;
  try {
    data = hexToBase64($("btm-payload").value);
  } catch (error) {
    state.error = error.message;
    state.notice = null;
    renderMessage();
    return;
  }
  await postCommand(`/trains/${state.selectedTrainId}/equipment/btm`, {
    cab_id: state.selectedCab,
    data,
  });
}

bind();
refresh();
connectWs();
