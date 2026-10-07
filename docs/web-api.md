# A-Train HTTP / WebSocket API Specification

This document specifies the browser-facing API as **implemented** by
`src/a_train/adapters/api/`. It is the contract for the web client and for
automation (§6.1): every GUI operation has an equivalent REST call, and the
browser never touches simulator internals.

## Conventions

- Base URL: the host the app is run on, e.g. `http://127.0.0.1:8000`.
- All request and response bodies are JSON (`Content-Type: application/json`).
- All mutating endpoints submit commands to the `SimulationCore`; they never
  modify world objects directly (§2.1).
- An invalid command returns **HTTP 400** with `{"detail": "<error message>"}`
  and leaves the simulation state unchanged. Unknown train IDs return 400 for
  commands (the core reports them) and 404 for reads.
- Unknown extra fields in a request body are ignored.
- Units: position in metres, speed in m/s, acceleration in m/s², all times in
  seconds. The nominal fixed step is `0.05 s` (§2.3).

### Enums

`simulation_state` (§2.2):

| Value       | Meaning                           |
| ----------- | --------------------------------- |
| `STOPPED` | No simulation time advances.      |
| `RUNNING` | The core advances time and world. |
| `PAUSED`  | World state and time are frozen.  |

`time_mode` (§2.3):

| Value        | Time advancement                                |
| ------------ | ----------------------------------------------- |
| `REALTIME` | `simulation_delta = wall_delta`               |
| `SCALED`   | `simulation_delta = wall_delta * multiplier`  |
| `MANUAL`   | Advances only via`POST /api/simulation/step`. |

## Cab representation

Cab activation is native train state, not equipment. The train exposes
`cabs`: one `{ "cab_id": int, "active": bool, "key": bool, "facing": string }` entry per
configured cab, in configured order. `facing` is the cab's immutable track
facing (`"forward"` drives toward increasing position, `"backward"` toward
decreasing); the driving system maps its cab-relative direction handle through
it. It changes through `POST /api/trains/{train_id}/commands` with `cab_id`
plus `active`.

## Equipment representation

The train exposes equipment as a flat array of independently addressed
instances. Each entry has a behavior `type`, a unique instance `key`, a
type-specific `state` object, and `cab_id` -- the owning cab for cab-scoped
equipment (`null` for train-level instances such as doors). `cab_id` is
informational in the Web API; `key` remains the address for equipment
commands:

```json
{
  "equipment": [
    { "type": "door", "key": "left_door", "cab_id": null, "state": { "state": "closed" } },
    { "type": "door", "key": "right_door", "cab_id": null, "state": { "state": "closed" } },
    { "type": "btm", "key": "btm_1", "cab_id": 1, "state": { "cab_id": 1, "payload_b64": null, "received_count": 0 } },
    { "type": "cbtc", "key": "cbtc_1", "cab_id": 1, "state": { "cab_id": 1, "is_cbtc_authorized": false } },
    { "type": "driving_system", "key": "driving_1", "cab_id": 1, "state": { "cab_id": 1, "facing": "forward", "control_mode": "manual", "mode": "off", "direction": "off", "acceleration": 0.0, "target_speed": null, "target_position": null, "max_speed": null, "manual_mode": "off", "manual_direction": "off", "manual_acceleration": 0.0 } },
    { "type": "driving_system", "key": "driving_2", "cab_id": 2, "state": { "cab_id": 2, "facing": "backward", "control_mode": "manual", "mode": "off", "direction": "off", "acceleration": 0.0, "target_speed": null, "target_position": null, "max_speed": null, "manual_mode": "off", "manual_direction": "off", "manual_acceleration": 0.0 } },
    { "type": "switch_box", "key": "switch_box_1", "cab_id": 1, "state": { "cab_id": 1, "position": "c2", "c2_authorized": false, "cbtc_authorized": false } },
    { "type": "stcs_atp_duo", "key": "stcs_atp_duo_1", "cab_id": 1, "state": {
        "last_command": null,
        "last_command_time": null,
        "train_out_signal": "110110000000000000000000000000",
        "train_in_states": [ { "name": "emergency_brake_1", "value": false, "blockable": false, "blocked": false } ],
        "train_out_states": [ { "name": "emergency_brake_1_inner_feedback", "value": true, "blockable": true, "blocked": false } ]
    } }
  ]
}
```

Equipment commands address the instance directly through
`POST /api/trains/{train_id}/equipment/{equipment_key}`. There is no implicit
slot or type grouping.

## Endpoints

### GET /api/status

Returns the current control state.

**Response 200** (`StatusResponse`):

```json
{
  "simulation_state": "STOPPED",
  "simulation_time": 0.0,
  "time_mode": "MANUAL",
  "time_multiplier": 1.0
}
```

| Field                | Type           | Notes                                    |
| -------------------- | -------------- | ---------------------------------------- |
| `simulation_state` | string enum    | `STOPPED` / `RUNNING` / `PAUSED`   |
| `simulation_time`  | number         | Seconds since reset.                     |
| `time_mode`        | string enum    | `REALTIME` / `SCALED` / `MANUAL`   |
| `time_multiplier`  | number or null | Only meaningful while in`SCALED` mode. |

### POST /api/simulation/start

Runs the core. Idempotent while already running. Body: none.

**Response 200**: `StatusResponse` after the transition.

### GET /api/atp/status

Read-only view of each configured A-Train ATP listener (atp-api.md §6.2).
`connections` is empty when no ATP listeners are configured. Channels carry
no cab binding, so entries name the local listener address and current number
of connected ATP clients.

**Response 200**:

```json
{
  "connections": [
    {
      "host": "127.0.0.1",
      "port": 9101,
      "state": "READY",
      "ready": true,
      "active_peers": 1
    }
  ]
}
```

| Field   | Type   | Notes                                                                     |
| ------- | ------ | ------------------------------------------------------------------------- |
| `host`         | string | Local address A-Train binds. |
| `port`         | int    | Local TCP port A-Train binds. |
| `state`        | string | `READY` while the listener is active; otherwise `STOPPED`. |
| `ready`        | bool   | True while A-Train is accepting TCP connections. |
| `active_peers` | int    | Number of currently connected ATP clients. |

### POST /api/simulation/pause

Pauses the core. Idempotent while paused or stopped. Body: none.

**Response 200**: `StatusResponse`.

### POST /api/simulation/reset

Restores the configured initial world state, sets `simulation_time` to `0.0`,
clears all control, equipment, and link-cut state, and leaves the core
`STOPPED`.
Body: none.

**Response 200**: `StatusResponse`.

### POST /api/simulation/time-mode

Selects the time mode. Resets the wall-clock reference so paused time is never
accumulated (§2.5).

**Request body** (`TimeModeRequest`):

```json
{ "mode": "SCALED", "time_multiplier": 2.0 }
```

| Field               | Type           | Notes                                                |
| ------------------- | -------------- | ---------------------------------------------------- |
| `mode`            | string enum    | `REALTIME` / `SCALED` / `MANUAL`.              |
| `time_multiplier` | number or null | Required for`SCALED`; must be positive and finite. |

**Response 200**: `StatusResponse`.

**Errors**: 400 unknown mode; missing or invalid multiplier for `SCALED`.

### POST /api/simulation/step

Advances simulation time in `MANUAL` mode only. The request returns after all
whole fixed steps for `delta` have been processed (fractional remainder is
retained in the accumulator, §2.3).

**Request body** (`StepRequest`):

```json
{ "delta": 0.5 }
```

| Field     | Type   | Notes                         |
| --------- | ------ | ----------------------------- |
| `delta` | number | Non-negative, finite seconds. |

**Response 200**: `StatusResponse`.

**Errors**: 400 not in `MANUAL` mode; negative or non-finite `delta`.

### GET /api/trains

Returns the single configured train in the response envelope retained for the
WebSocket contract.

**Response 200**:

```json
{ "trains": [ { "train_id": "TRAIN001", "...": "..." } ] }
```

Items are `TrainResponse` objects (see [Response models](#response-models)).

### GET /api/trains/{train_id}

Reads one train.

**Response 200**: `TrainResponse`.

**Errors**: 404 unknown train.

### POST /api/trains/{train_id}/commands

Applies a normalized control through the train aggregate (§3.3). Control state
changes are accepted immediately; physical effects appear at the next fixed
step boundary.

**Request body** (`TrainControlRequest`):

```json
{ "cab_id": 1, "active": true, "key": true }
```

Train-wide drive demand can be submitted without selecting a cab:

```json
{ "drive_demand": 0.75 }
```

| Field            | Type           | Notes                                                                                                                                                             |
| ---------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cab_id`       | integer or null | Required for `active` and `key`; must be a configured cab. Omit for train-wide `drive_demand`. |
| `drive_demand` | number or null | Signed lever in `[-1.0, 1.0]`: positive drives (traction limit), negative decelerates toward zero (decel limit) and never moves a standing train rearward. Overwritten while any driving system is engaged. Omitted or null leaves it unchanged. |
| `active`       | bool or null   | Sets this cab's native activation flag; requires `cab_id`. Omitted or null leaves it unchanged; it never affects control acceptance or dynamics. |
| `key`          | bool or null   | Sets whether the key is inserted in this cab; requires `cab_id`. Omitted or null leaves it unchanged. |

**Response 200**: `TrainResponse`.

**Errors**: 400 unknown train; `cab_id` is not a configured cab; `drive_demand`
outside `[-1.0, 1.0]` or not finite.

### POST /api/trains/{train_id}/equipment/{equipment_key}

Sets train-facing equipment state through the generic equipment endpoint
(§3.5). `{key}` is the registered equipment type. Equipment changes are applied
immediately by the core (like train controls) and do not advance simulation
time; driving-system automatic targets affect dynamics on the next fixed step.
Other equipment does not directly affect train motion. The
dispatcher validates the body against the target equipment and returns 400
with the component's error message on invalid input.

**Request body** (`EquipmentSetRequest` — the fields used depend on `{key}`):

| Field         | Type               | Used by                    | Notes                                                        |
| ------------- | ------------------ | -------------------------- | ------------------------------------------------------------ |
| `command`   | string             | `door`, `stcs_atp_duo_<cab_id>` | `"open"` / `"close"`; STCS ATP command. |
| `train_out_signal` | string      | `stcs_atp_duo_<cab_id>` | train→ATP bit assertion (`"0"`/`"1"` string, bit meanings in atp-api.md §4.2); positions beyond the string keep their value. |
| `train_in_signals` | object      | `stcs_atp_duo_<cab_id>` | Sparse ATP→train updates, e.g. `{"turnback_activation": true}`; omitted names keep their value. Does not change `last_command`. |
| `train_out_signals` | object     | `stcs_atp_duo_<cab_id>` | Sparse train→ATP updates by signal name; omitted names keep their value. Derived outputs must be blocked in this request or already blocked. |
| `block`           | array of string | `stcs_atp_duo_<cab_id>` | Freeze this instance's internal derivation of the named derived train-out signals; while blocked a bit keeps its last value and a `train_out_signal` assertion on it sticks. Unknown or non-derived names → 400, all-or-nothing. |
| `unblock`         | array of string | `stcs_atp_duo_<cab_id>` | Resume derivation of the named signals; the bit self-heals to derived state on the same application. |
| `system_switch`   | string          | `switch_box_<cab_id>` | Box position `"c2"` / `"auto"` / `"cbtc"`; mirrored one-hot into that cab's STCS ATP `system_switch_*` bits. |
| `c2_authorized`   | boolean         | `switch_box_<cab_id>` | Sets the box's C2 authorization state. It does not change the physical selector position. |
| `cbtc_authorized` | boolean         | `switch_box_<cab_id>` | Sets the box's CBTC authorization state. It does not change the physical selector position. |
| `is_cbtc_authorized` | boolean      | `cbtc_<cab_id>` | Sets the standalone CBTC equipment authorization state. It does not update a switch box. |
| `cab_id`    | integer            | optional consistency check | Target cab, when applicable. |
| `data`      | string             | `btm_1`, `btm_2` | Base64 opaque payload (atp-api.md §3.2); invalid base64 → 400. |
| `telegram`  | object             | `btm_1`, `btm_2` | Complete JSON CTCS balise telegram encoded exactly as supplied (btm-telegram.md); mutually exclusive with `data`. |
| `mode`      | string             | `driving_1`, `driving_2`  | Driving-system mode handle: `"traction"` / `"off"` / `"brake"`. |
| `direction` | string             | `driving_1`, `driving_2`  | Driving-system direction handle: `"forward"` / `"off"` / `"backward"` (cab-relative). |
| `acceleration` | number          | `driving_1`, `driving_2`  | Driving-system acceleration handle: continuous effort in `[0.0, 1.0]`. |
| `control_mode` | string          | `driving_1`, `driving_2`  | `"manual"`, `"speed"`, or `"position"`. |
| `target_speed` | number          | speed control             | Signed target speed in m/s; must be finite. |
| `target_position` | number       | position control          | Absolute track position in metres; must be finite. |
| `max_speed` | number              | position control          | Positive speed limit in m/s. |

For a structured BTM `telegram` request, the successful `200` response is the
updated `TrainResponse` plus an `encoded` object containing the exact payload
sent to the BTM:

```json
{
  "encoded": {
    "hex": "83 00 7f 80 ...",
    "b64": "gwB/gAAA..."
  }
}
```

The `hex` value is space-separated lowercase bytes. The `b64` value is the
same payload encoded as base64. Other equipment requests return the normal
`TrainResponse` without `encoded`.

**Semantics per equipment key**:

| Key      | Behavior                                                                                                                                                                                               |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `left_door`, `right_door` | `command` `"open"` / `"close"` sets the selected door state. |
| `btm_1`, `btm_2` | Delivers opaque `data` to that BTM instance. |
| `cbtc_<cab_id>` | Stores standalone `is_cbtc_authorized` for the configured cab; reset restores its configured initial value. It does not assert switch-box state. |
| `stcs_atp_duo_1`, `stcs_atp_duo_2` | `command` is recorded as `last_command` on the addressed cab's STCS Duo instance, together with the wall-clock time of arrival as `last_command_time`. `train_in_signals` and `train_out_signals` apply sparse, named updates without changing omitted bits; train-in updates do not update `last_command`. An update to a derived train-out signal is accepted only while blocked. Bulk `train_out_signal` asserts train→ATP bits; derived feedbacks (brake feedbacks and sleep) and real-state mirrors (cab/key activation, door, driving-handle, switch-box position, and the four `c2_control_state_*` bits on a cab with a fitted switch box) are re-established and reject the override, while operator-owned signals (buttons, panel states, and those four bits on a cab without a switch box) take manual assertions as given. `block` freezes the simulator's derivation of named *derived* signals (snapshot rows with `blockable: true`): while blocked such a bit keeps its last value, manual updates stick, and ATP-side reactions to input bits are unaffected. `unblock` resumes derivation; the bit self-heals on the same application. Unknown signal names and non-blockable names in `block`/`unblock` are rejected 400, all-or-nothing. |
| `driving_1`, `driving_2` | In `manual` control mode, sets any subset of the three driver-room handles (no interlocks; each position is independently settable). A legacy request containing `mode`, `direction`, or `acceleration` without `control_mode` selects manual mode. While a cab's handle `mode` is `"traction"` or `"brake"`, that driving system **overwrites** the legacy `drive_demand` lever for every step. In `speed` mode, `target_speed` is a signed speed setpoint. In `position` mode, the system drives to the absolute `target_position`, slowing under a stopping-speed envelope, with `max_speed` as a cap and a 0.05 m arrival tolerance. Automatic control updates effective handles before each physics step; `manual_*` snapshot fields retain the operator's stored handles. |
| `switch_box_<cab>` | `system_switch` sets the cab's system-selection box to `"c2"`, `"auto"`, or `"cbtc"` (reset returns it to `"c2"`); the selected physical position is mirrored one-hot to STCS ATP, including `system_switch_auto` while set to `"auto"`. The four `c2_control_state_*` bits assert the C2 pair in C2 position, the CBTC pair in CBTC position, and in AUTO assert a pair only when its authorization is exclusively true; equal authorization values in AUTO set all four low. `c2_authorized` and `cbtc_authorized` are independent authorization states and do not alter the selector position. `cbtc_authorized` can be set through this equipment endpoint or the CBTC switch-logic WebSocket (`/ws/cbtc`). The matching STCS ATP instance feeds `c2_authorized` back to the box when its ATP input signal is true. On a cab with no fitted box the switch bits remain plain operator-asserted panel bits. |

While `cut_off_traction` is high on an STCS ATP instance, its same-cab driving system is continuously reset to `off` / `off` / `0.0`, and the train-wide legacy drive-demand lever is asserted to zero on equipment-intent resolution, including before each physics step. When the signal goes low, that driving system can be commanded again; drive demand remains zero until explicitly changed.

**Example**:

```http
POST /api/trains/TRAIN001/equipment/left_door
Content-Type: application/json

{ "command": "open" }
```

**Response 200**: `TrainResponse`.

**Errors**: 400 unknown train; unknown equipment key (no such equipment on the
train); missing or invalid fields for the target equipment.

### Link cuts (physical equipment wires)

A link cut drops every intent delivery over one concrete physical wire: an
intent `source` (equipment key or `cab_<id>` broadcast source) reaching a
`target` (equipment key or `"train"`). A cut wire is **stale, not zeroed**:
the recipient keeps its last delivered value until the wire is restored,
and restores self-heal on the next delivery (§3.7). Grouped feedback fans
out per physical wire: cutting `left_door -> stcs_atp_duo_1` leaves
`left_door -> stcs_atp_duo_2` live. `reset()` clears all cuts. The current
cut set is reported in every train snapshot as `link_cuts`.

| Method & Path                                          | Body / params                                   | Meaning                                |
| ------------------------------------------------------ | ----------------------------------------------- | -------------------------------------- |
| `PUT /api/trains/{train_id}/links`                     | `{ "cuts": [ { "source", "target" }, ... ] }`   | Replace the whole cut set; `[]` restores all. |
| `POST /api/trains/{train_id}/links/cut`                | `{ "source": "...", "target": "..." }`          | Cut one wire; already-cut is a no-op.  |
| `DELETE /api/trains/{train_id}/links/cut?source=&target=` | query params                                  | Restore one wire; restoring a live wire is a no-op. |

**Example**:

```http
POST /api/trains/TRAIN001/links/cut
Content-Type: application/json

{ "source": "left_door", "target": "stcs_atp_duo_1" }
```

```http
DELETE /api/trains/TRAIN001/links/cut?source=left_door&target=stcs_atp_duo_1
```

**Response 200**: `TrainResponse` reflecting the updated set.

**Errors**: 400 unknown source/target (not an installed equipment key or a
configured `cab_<id>`; targets additionally may be `"train"`), self-cut
(`source == target`). A `PUT` replaces all-or-nothing: an invalid set leaves
the previous cuts untouched.

## Response models

### StatusResponse

See `GET /api/status`.

### TrainResponse

A read-only snapshot of one train. Physical fields keep their names and
meanings across versions; equipment state is nested under `equipment`, keyed
by equipment type (§3.5).

```json
{
  "train_id": "TRAIN001",
  "cabs": [
    { "cab_id": 1, "active": true, "key": false, "facing": "forward" },
    { "cab_id": 2, "active": false, "key": true, "facing": "backward" }
  ],
  "speed": 0.5,
  "acceleration": 1.5,
  "position": 0.0125,
  "direction": "forward",
  "drive_demand": 1.0,
  "equipment": [
    { "type": "door", "key": "left_door", "state": { "state": "closed" } },
    { "type": "door", "key": "right_door", "state": { "state": "closed" } },
    { "type": "btm", "key": "btm_1", "state": { "cab_id": 1, "payload_b64": null, "received_count": 0 } },
    { "type": "driving_system", "key": "driving_1", "state": { "cab_id": 1, "facing": "forward", "mode": "off", "direction": "off", "acceleration": 0.0 } },
    { "type": "stcs_atp_duo", "key": "stcs_atp_duo_1", "state": {
        "last_command": null,
        "last_command_time": null,
        "train_out_signal": "110110000000000000000000000000",
        "train_in_states": [ { "name": "emergency_brake_1", "value": false, "blockable": false, "blocked": false } ],
        "train_out_states": [ { "name": "emergency_brake_1_inner_feedback", "value": true, "blockable": true, "blocked": false } ]
    } }
  ],
  "link_cuts": []
}
```

| Field            | Type   | Notes                                                           |
| ---------------- | ------ | --------------------------------------------------------------- |
| `train_id`     | string | Stable identifier.                                              |
| `cabs`         | array  | Native cab state: one `{cab_id, active, key, facing}` per configured cab, in configured order. `key` reports whether that cab's key is inserted. |
| `speed`        | number | m/s, signed: negative means rearward travel.                    |
| `acceleration` | number | m/s², the value actually applied during the last step (§3.4). |
| `position`     | number | m along the linear track; may decrease with rearward motion.   |
| `direction`    | string | Derived from the speed sign: `"forward"` (v > 0), `"backward"` (v < 0), `"stopped"` (v = 0). |
| `drive_demand` | number | The held legacy signed lever in`[-1.0, 1.0]`; without an engaged driving system, negative values decelerate toward (and clamp at) zero and never move a standing train rearward. Ignored for any step in which a driving system or the ATP protection brake is engaged, but its value persists. |
| `equipment`    | array | One `{type, key, state}` entry per equipment instance. |

| `link_cuts`    | array | Active physical wire cuts `{source, target}` (equipment-wire faults, "Link cuts" above and §3.7). |

**`equipment` entries**:

| Field | Shape | Notes |
| ----- | ----- | ----- |
| `type` | string | Equipment behavior type. |
| `key` | string | Unique equipment instance key. |
| `state` | object | Type-specific state. |

**`stcs_atp_duo_<cab_id>` state**: each configured cab has its own STCS Duo instance.
`last_command` is the raw ATP bit string (null until the first `atp_signal` on
that cab); `last_command_time` is the wall-clock time in POSIX seconds (UTC)
at which the core applied that command (null until the first command, cleared
by reset); `train_in_states` and `train_out_states` are every
decoded signal as `{name, value, blockable, blocked}` in bit order (17 train-in, 30 train-out;
names and meanings in atp-api.md §4.2); `train_out_signal` is the train-out
state as one bit string. Each signal row's `blockable` is true exactly for the six train-out
signals whose value STCS derives from other signals it holds — the four brake
feedback rows, `turnback_activation_feedback`, and `sleep_signal` — and `blocked` reports the active freeze set through `block`/`unblock`
(cleared by `simulation/reset`). Every other row — train-in bits, asserted
real-state mirrors (door, cab/key, driving-handle, switch-box, and the four
`c2_control_state_*` rows when a switch box is fitted), and operator-owned
panel signals including those four rows on cabs without a switch box — is
`blockable: false`; freeze an asserted
mirror with its `source -> stcs_atp_duo_<cab>` link cut (stale, not zeroed)
rather than with `block`. `door_state_1` / `door_state_2` mirror the
`left_door` / `right_door` open state. `direction_handle_forward_1` and
`direction_handle_forward_2` are duplicate bit wires of one
'direction handle forward' signal and always read identically (the
`_1`/`_2` suffix is not a cab index); `direction_handle_backward`,
`traction_handle_traction`, and `traction_handle_brake` mirror the cab's
remaining handle positions. All five are projected onto these rows at
ingest by that cab's own `driving_system_<cab>` feedback intent; a cut on
that wire leaves them stale and manually assertable until a delivery
after restore self-heals them. A fitted switch box asserts the
`c2_control_state_*` rows from its position and authorization: C2 asserts the
two `*_1_*` rows, CBTC asserts the two `*_2_*` rows, and AUTO asserts one pair
only when that system is exclusively authorized; equal authorization values
in AUTO leave all four rows low. `system_switch_c2` / `system_switch_auto` /
`system_switch_cbtc` mirror the fitted `switch_box_<cab>` one-hot; unfitted
cabs keep those rows and the `c2_control_state_*` rows operator-owned.

Future addons add entries without changing existing physical fields; a train
without an equipment instance simply omits that entry.

**Immutability**: snapshots are read-only views; writing fields back through a
command body has no effect (extra fields are ignored).

## WebSocket /ws/cbtc

This endpoint serves the external CBTC switch-logic protocol. It is distinct
from the dashboard snapshot stream at `/ws` and uses the same HTTP server.
The client sends text JSON messages. A cab must have a configured switch box,
and only one client may be connected for a given cab; different cabs may
connect concurrently.

The client begins with:

```json
{ "type": "hello", "cab_id": 1 }
```

For a cab with a switch box, the server sends `hello_ack`, then the current
input state:

```json
{ "type": "hello_ack", "cab_id": 1 }
{ "type": "cbtc-in", "cbtc_active": false, "c2_authorized": false }
```

`cbtc_active` is true when the selector position is `cbtc`, or when the
position is `auto` and CBTC is authorized while C2 is not. `c2_authorized`
is read from the same cab's switch box. The server sends another complete
`cbtc-in` whenever either input changes.

The client reports its effective authorization with:

```json
{ "type": "cbtc-out", "cbtc_authorized": true }
```

The server applies `cbtc_authorized` directly to the same cab's switch box
through the simulation core. No response is sent for `cbtc-out`; any resulting
input-state change is published as `cbtc-in`.

If the cab has no switch box, or already has a connected switch-logic client,
the server closes with WebSocket code `1008` and does not send `hello_ack`.
Malformed JSON, unexpected message types, or invalid fields close with code
`1002`. Only `cbtc-out` is accepted after the handshake.

## WebSocket /ws

### Equipment contract

The equipment model is flat in every REST and WebSocket response. `equipment`
is an array of entries shaped as `{ "type": string, "key": string, "state":
object }`. The key uniquely identifies one instance, for example
`left_door`, `right_door`, `btm_1`, `driving_1`, or `stcs_atp_duo_1`. Equipment
commands address that instance directly through
`/api/trains/{train_id}/equipment/{key}`; there is no implicit
type grouping or slot field.

One-way live state stream (simulator → browser, §5.3). The client receives:

1. the latest snapshot immediately after connecting;
2. one snapshot after every nominal fixed step while running or stepping;
3. one snapshot after every control-state transition (run / pause / reset /
   time mode).

**Message** (JSON object):

```json
{
  "simulation_state": "RUNNING",
  "simulation_time": 123.45,
  "time_mode": "SCALED",
  "time_multiplier": 2.0,
  "trains": [ { "train_id": "TRAIN001", "...": "..." } ]
}
```

`trains` items are the same `TrainResponse` shape as REST.

**Delivery semantics**: each client has its own bounded queue; when a client
falls behind, its **oldest undelivered snapshot is dropped**, so a slow
consumer never blocks physics or other clients. Treat the stream as
latest-state, not an event log. There is no application-level ping/pong; the
static demo client reconnects automatically on socket close.

## Static client

The static browser demo is served from `web/` at `/` (HTML at `/index.html`).
`/api/*`, `/ws`, and `/ws/cbtc` are registered before the static mount and are
never shadowed.

## Not implemented yet

Planned in `docs/architectural.md` §5.2 but absent from the current routes:

- `POST /api/signals/{id}` (linear-track signals, Phase 3+).
- The earlier `POST /api/btm/inject` proposal is superseded by the generic
  `POST /api/trains/{train_id}/equipment/btm`.
- The ATP-side TCP/NDJSON protocol (`atp-api.md`) is separate from this HTTP API.
