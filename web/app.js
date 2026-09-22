// A-Train browser demo client (Phase 2.5).
//
// A thin test client of the simulator (architectural.md §5.1): it applies no
// restriction the API itself does not impose, and every API operation is
// reachable, addressed the same way the API addresses it — equipment by
// instance key (one panel per driving system, door, BTM, STCS ATP), train
// commands by cab. It subscribes to the `/ws` snapshot stream so state
// updates continuously without polling, and submits commands through the REST
// API. Rejected commands surface their error without changing the displayed
// state. The UI contains no simulation rules.

const API = "/api";
const state = {
  status: null,
  trains: [],
  selectedTrainId: null,
  selectedCab: null,
  error: null,
  notice: null,
  ws: "connecting",
  // Per-control unsaved-intent flags keyed by control id ("drive") or
  // equipment instance key ("driving_1"). A snapshot never overwrites a
  // control the operator has edited but not yet applied.
  dirty: {},
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
  if (sel && !sel.cabs.some((c) => c.cab_id === state.selectedCab)) {
    state.selectedCab = sel.cabs[0].cab_id;
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
  return res.ok;
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
  const cabSel = $("cab-select");
  const sel = selectedTrain();
  if (sel) {
    if (
      cabSel.children.length !== sel.cabs.length ||
      ![...cabSel.options].some((o) => Number(o.value) === state.selectedCab)
    ) {
      cabSel.innerHTML = "";
      for (const c of sel.cabs) {
        const o = document.createElement("option");
        o.value = String(c.cab_id);
        o.textContent = String(c.cab_id);
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
  const stcsEl = $("train-stcs");
  if (!sel) {
    pre.textContent = "no trains";
    equipmentEl.replaceChildren();
    stcsEl.replaceChildren();
    $("cab-state").textContent = "—";
    $("key-state").textContent = "—";
    renderEquipmentControls([]);
    return;
  }
  const equipment = sel.equipment || [];
  const cabs = sel.cabs || [];
  const cabText = cabs
    .map(
      (c) =>
        `${c.cab_id}=${c.active ? "active" : "inactive"}, key=${c.key ? "inserted" : "removed"} (${c.facing})`
    )
    .join(", ");
  const selected = cabs.find((c) => c.cab_id === state.selectedCab);
  $("cab-state").textContent = selected
    ? `cab ${selected.cab_id}: ${selected.active ? "active" : "inactive"}`
    : "—";
  $("key-state").textContent = selected
    ? `cab ${selected.cab_id}: ${selected.key ? "inserted" : "removed"}`
    : "—";
  const lines = [
    `train_id        ${sel.train_id}`,
    `cabs            ${cabText}`,
    `position        ${fmt(sel.position)} m`,
    `speed           ${fmt(sel.speed)} m/s`,
    `acceleration    ${fmt(sel.acceleration)} m/s^2`,
    `direction       ${sel.direction}`,
    `drive_demand    ${fmt(sel.drive_demand)}`,
  ];
  pre.textContent = lines.join("\n");

  const isStcsAtp = (entry) =>
    entry.type === "stcs_atp_duo" || entry.type === "stcs_atp_solo";
  renderStcs(stcsEl, equipment.filter(isStcsAtp));
  renderEquipment(equipmentEl, equipment.filter((entry) => !isStcsAtp(entry)));
  syncSlider("drive", sel.drive_demand);
  renderEquipmentControls(equipment);
}

// -- Instance-addressed equipment controls ------------------------------------
//
// One interactive card per equipment instance of the selected train. Cards
// are rebuilt only when the instance set changes, so live snapshots cannot
// steal focus; values are synced from each snapshot unless dirty.

const controlSignature = {};

function ensurePanels(container, signature, rebuild) {
  if (controlSignature[container.id] === signature) return;
  controlSignature[container.id] = signature;
  for (const key of Object.keys(state.dirty)) {
    if (key !== "drive") delete state.dirty[key];
  }
  container.replaceChildren(...rebuild());
}

function byType(equipment, type) {
  return equipment.filter((entry) => entry.type === type);
}

function renderEquipmentControls(equipment) {
  const trainId = state.selectedTrainId;
  const prefix = (list) => list.map((e) => `${trainId}:${e.key}`).join(",");

  ensurePanels(
    $("driving-panels"),
    prefix(byType(equipment, "driving_system")),
    () => byType(equipment, "driving_system").map(buildDrivingCard)
  );
  ensurePanels(
    $("door-controls"),
    prefix(byType(equipment, "door")),
    () => byType(equipment, "door").map(buildDoorCard)
  );
  ensurePanels(
    $("btm-send-buttons"),
    prefix(byType(equipment, "btm")),
    () => byType(equipment, "btm").map(buildBtmSendButton)
  );
  ensurePanels(
    $("stcs-send-buttons"),
    prefix(
      equipment.filter(
        (entry) => entry.type === "stcs_atp_duo" || entry.type === "stcs_atp_solo"
      )
    ),
    () =>
      equipment
        .filter(
          (entry) => entry.type === "stcs_atp_duo" || entry.type === "stcs_atp_solo"
        )
        .map(buildStcsSendButton)
  );

  syncDrivingPanels(equipment);
  syncDoorCards(equipment);
}

function dirtyKey(key) {
  return `${state.selectedTrainId}:${key}`;
}

function selectEl(values, labels) {
  const sel = document.createElement("select");
  values.forEach((value, index) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = labels ? labels[index] : value;
    sel.appendChild(option);
  });
  return sel;
}

function buildDrivingCard(entry) {
  const key = entry.key;
  const card = document.createElement("article");
  card.className = "equipment-card driving-card";
  card.dataset.key = key;

  const heading = document.createElement("h3");
  heading.textContent = key;
  card.appendChild(heading);

  const readout = document.createElement("p");
  readout.className = "stcs-raw";
  card.appendChild(readout);

  const row = document.createElement("div");
  row.className = "controls";
  const mode = selectEl(["off", "traction", "brake"], ["Off", "Traction", "Brake"]);
  const direction = selectEl(["off", "forward", "backward"], ["Off", "Forward", "Backward"]);
  const accel = document.createElement("input");
  accel.type = "range";
  accel.min = "0";
  accel.max = "1";
  accel.step = "0.01";
  accel.value = "0";
  const accelVal = document.createElement("output");
  accelVal.textContent = "0.00";

  const markDirty = () => {
    state.dirty[dirtyKey(key)] = true;
    accelVal.textContent = fmt(parseFloat(accel.value), 2);
  };
  mode.onchange = markDirty;
  direction.onchange = markDirty;
  accel.oninput = markDirty;

  const apply = document.createElement("button");
  apply.textContent = "Apply Handles";
  apply.onclick = async () => {
    const ok = await postCommand(`/trains/${state.selectedTrainId}/equipment/${key}`, {
      mode: mode.value,
      direction: direction.value,
      acceleration: parseFloat(accel.value),
    });
    if (ok) delete state.dirty[dirtyKey(key)];
  };

  row.append(
    labeled("Mode", mode),
    labeled("Direction", direction),
    labeled("Acceleration", accel, accelVal),
    apply
  );
  card.appendChild(row);
  card.widgets = { readout, mode, direction, accel, accelVal };
  return card;
}

function labeled(text, ...controls) {
  const label = document.createElement("label");
  label.append(text);
  for (const control of controls) label.append(control);
  return label;
}

function syncDrivingPanels(equipment) {
  for (const card of $("driving-panels").children) {
    const entry = equipment.find((e) => e.key === card.dataset.key);
    if (!entry || !card.widgets) continue;
    const s = entry.state;
    const w = card.widgets;
    w.readout.textContent =
      `cab ${s.cab_id} · facing ${s.facing} · ${s.mode}/${s.direction}/${fmt(s.acceleration, 2)}`;
    if (state.dirty[dirtyKey(card.dataset.key)]) continue;
    w.mode.value = s.mode;
    w.direction.value = s.direction;
    w.accel.value = String(s.acceleration);
    w.accelVal.textContent = fmt(s.acceleration, 2);
  }
}

function buildDoorCard(entry) {
  const key = entry.key;
  const card = document.createElement("article");
  card.className = "equipment-card";
  card.dataset.key = key;

  const heading = document.createElement("h3");
  heading.textContent = key;
  card.appendChild(heading);

  const readout = document.createElement("p");
  readout.className = "stcs-raw";
  card.appendChild(readout);

  const row = document.createElement("div");
  row.className = "controls";
  for (const command of ["open", "close"]) {
    const button = document.createElement("button");
    button.textContent = command === "open" ? "Open" : "Close";
    button.onclick = () =>
      postCommand(`/trains/${state.selectedTrainId}/equipment/${key}`, { command });
    row.appendChild(button);
  }
  card.appendChild(row);
  card.widgets = { readout };
  return card;
}

function syncDoorCards(equipment) {
  for (const card of $("door-controls").children) {
    const entry = equipment.find((e) => e.key === card.dataset.key);
    if (!entry || !card.widgets) continue;
    card.widgets.readout.textContent = `state: ${entry.state.state}`;
  }
}

function buildBtmSendButton(entry) {
  const key = entry.key;
  const button = document.createElement("button");
  button.textContent = `Send → ${key}`;
  button.onclick = () => sendBtmPayload(key);
  return button;
}

function buildStcsSendButton(entry) {
  const key = entry.key;
  const button = document.createElement("button");
  button.textContent = `Send → ${key}`;
  button.onclick = async () => {
    await postCommand(`/trains/${state.selectedTrainId}/equipment/${key}`, {
      command: $("stcs-command").value,
    });
  };
  return button;
}

function renderEquipment(container, equipment) {
  container.replaceChildren();
  if (!equipment.length) {
    const empty = document.createElement("p");
    empty.className = "equipment-empty";
    empty.textContent = "No equipment reported";
    container.appendChild(empty);
    return;
  }

  for (const entry of equipment) {
    const card = document.createElement("article");
    card.className = "equipment-card";

    const heading = document.createElement("h3");
    heading.textContent = entry.key;
    card.appendChild(heading);

    const details = document.createElement("pre");
    details.textContent = JSON.stringify(entry.state, null, 2);
    card.appendChild(details);
    container.appendChild(card);
  }
}

function renderStcs(container, entries) {
  container.replaceChildren();
  for (const entry of entries) {
    container.appendChild(buildStcsCard(entry));
  }
}

function buildSignalTable(states) {
  const table = document.createElement("table");
  for (const [bit, signal] of (states || []).entries()) {
    const row = document.createElement("tr");
    if (signal.value) row.className = "on";
    const bitCell = document.createElement("td");
    bitCell.className = "bit";
    bitCell.textContent = String(bit);
    const nameCell = document.createElement("td");
    nameCell.textContent = signal.name;
    const valueCell = document.createElement("td");
    valueCell.className = "value";
    valueCell.textContent = signal.value ? "1" : "0";
    row.append(bitCell, nameCell, valueCell);
    table.appendChild(row);
  }
  return table;
}

function buildStcsCard(entry) {
  const state = entry.state || {};
  const card = document.createElement("article");
  card.className = "equipment-card stcs-card";

  const heading = document.createElement("h3");
  const cabMatch = /^stcs_atp_(?:duo|solo)_(\d+)$/.exec(entry.key);
  const cabLabel = cabMatch ? `Cab ${cabMatch[1]}` : "Cab —";
  heading.textContent = entry.key;
  card.appendChild(heading);

  const raw = document.createElement("p");
  raw.className = "stcs-raw";
  const cab = document.createElement("span");
  cab.textContent = `cab: ${cabLabel}`;
  const command = document.createElement("span");
  command.textContent = `last ATP command: ${state.last_command ?? "—"}`;
  const signal = document.createElement("span");
  signal.textContent = `train-out signal: ${state.train_out_signal || "—"}`;
  raw.append(
    cab,
    document.createElement("br"),
    command,
    document.createElement("br"),
    signal
  );
  card.appendChild(raw);

  const columns = document.createElement("div");
  columns.className = "stcs-columns";
  for (const [title, states] of [
    ["ATP → train (in)", state.train_in_states],
    ["train → ATP (out)", state.train_out_states],
  ]) {
    const column = document.createElement("div");
    const label = document.createElement("h4");
    label.textContent = title;
    column.appendChild(label);
    column.appendChild(buildSignalTable(states));
    columns.appendChild(column);
  }
  card.appendChild(columns);
  return card;
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

function timeModeBody() {
  const mode = $("mode").value;
  const mult = mode === "SCALED" ? parseFloat($("multiplier").value) : null;
  return { mode, time_multiplier: mult };
}

function bind() {
  $("btn-run").onclick = async () => {
    const ok = await postCommand("/simulation/time-mode", timeModeBody());
    if (ok) await postCommand("/simulation/start", {});
  };
  $("btn-pause").onclick = () => postCommand("/simulation/pause", {});
  $("btn-reset").onclick = async () => {
    const ok = await postCommand("/simulation/reset", {});
    if (ok) state.dirty = {};
  };
  $("btn-set-mode").onclick = () => postCommand("/simulation/time-mode", timeModeBody());
  $("btn-step").onclick = () =>
    postCommand("/simulation/step", { delta: parseFloat($("step-delta").value) });

  $("cab-select").onchange = (e) => {
    state.selectedCab = Number(e.target.value);
    render();
  };

  $("drive").oninput = (e) => {
    state.dirty.drive = true;
    $("drive-val").textContent = fmt(parseFloat(e.target.value), 2);
  };
  $("btn-apply-demand").onclick = async () => {
    const ok = await postCommand(`/trains/${state.selectedTrainId}/commands`, {
      cab_id: state.selectedCab,
      drive_demand: parseFloat($("drive").value),
    });
    if (ok) delete state.dirty.drive;
  };
  $("btn-cab-activate").onclick = () =>
    postCommand(`/trains/${state.selectedTrainId}/commands`, {
      cab_id: state.selectedCab,
      active: true,
    });
  $("btn-cab-deactivate").onclick = () =>
    postCommand(`/trains/${state.selectedTrainId}/commands`, {
      cab_id: state.selectedCab,
      active: false,
    });
  $("btn-key-insert").onclick = () =>
    postCommand(`/trains/${state.selectedTrainId}/commands`, {
      cab_id: state.selectedCab,
      key: true,
    });
  $("btn-key-remove").onclick = () =>
    postCommand(`/trains/${state.selectedTrainId}/commands`, {
      cab_id: state.selectedCab,
      key: false,
    });

  $("btm-payload").oninput = renderBtmEncoding;
}

async function sendBtmPayload(key) {
  let data;
  try {
    data = hexToBase64($("btm-payload").value);
  } catch (error) {
    state.error = error.message;
    state.notice = null;
    renderMessage();
    return;
  }
  await postCommand(`/trains/${state.selectedTrainId}/equipment/${key}`, { data });
}

bind();
refresh();
connectWs();
