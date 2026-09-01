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
    state.selectedCab = sel.active_cab;
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
  if (!sel) {
    pre.textContent = "no trains";
    return;
  }
  const doorState = (sel.equipment && sel.equipment.door && sel.equipment.door.state) || "—";
  const lines = [
    `train_id        ${sel.train_id}`,
    `active_cab      ${sel.active_cab}  (cabs: ${sel.cab_ids.join(", ")})`,
    `position        ${fmt(sel.position)} m`,
    `speed           ${fmt(sel.speed)} m/s`,
    `acceleration    ${fmt(sel.acceleration)} m/s^2`,
    `drive_demand    ${fmt(sel.drive_demand)}`,
    `door_state      ${doorState}`,
  ];
  const io = sel.equipment && sel.equipment.io;
  if (io) {
    lines.push(`train_to_atp    ${io.train_to_atp}`);
    lines.push(`atp_to_train    ${io.atp_to_train}`);
  }
  const btm = sel.equipment && sel.equipment.btm;
  if (btm && btm.length) {
    lines.push(
      `btm             ${btm
        .map((b) => `cab${b.cab_id}:${b.received_count}`)
        .join(" ")}`
    );
  }
  pre.textContent = lines.join("\n");

  $("door-state").textContent = doorState;
  syncSlider("drive", sel.drive_demand);
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
    state.selectedCab = sel ? sel.active_cab : null;
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
}

bind();
refresh();
connectWs();
