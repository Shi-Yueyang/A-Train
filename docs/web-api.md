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

Read-only view of each configured cab's ATP channel (Phase 3.1, atp-api.md §6.2).
`connections` is empty when no ATP endpoints are configured.

**Response 200**:

```json
{
  "connections": [
    {
      "train_id": "TRAIN001",
      "cab_id": 1,
      "host": "127.0.0.1",
      "port": 9101,
      "state": "READY",
      "ready": true
    }
  ]
}
```

| Field   | Type   | Notes                                                                     |
| ------- | ------ | ------------------------------------------------------------------------- |
| `state` | string | `IDLE` / `CONNECTING` / `READY` / `DISCONNECTED` / `STOPPED`. |
| `ready` | bool   | True while the TCP channel is open (`READY`). |

### POST /api/simulation/pause

Pauses the core. Idempotent while paused or stopped. Body: none.

**Response 200**: `StatusResponse`.

### POST /api/simulation/reset

Restores the configured initial world state, sets `simulation_time` to `0.0`,
clears all control and equipment runtime state, and leaves the core `STOPPED`.
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

Lists all trains in stable train-ID order.

**Response 200**:

```json
{ "trains": [ { "train_id": "TRAIN001", "...": "..." } ] }
```

Items are `TrainResponse` objects (see [Response models](#response-models)).

### GET /api/trains/

Reads one train.

**Response 200**: `TrainResponse`.

**Errors**: 404 unknown train.

### POST /api/trains//commands

Applies a normalized control through the train aggregate (§3.3). Control state
changes are accepted immediately; physical effects appear at the next fixed
step boundary.

**Request body** (`TrainControlRequest`):

```json
{ "cab_id": 1, "drive_demand": 0.75 }
```

| Field            | Type           | Notes                                                                                                                                                             |
| ---------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cab_id`       | integer        | Must be one of the train's configured cabs. Cabs carry no authority (§3.3); any configured cab is accepted.                                                                                                                   |
| `drive_demand` | number or null | Signed lever in`[-1.0, 1.0]`: positive drives (traction limit), negative decelerates (decel limit). Omitted or null leaves it unchanged. |

**Response 200**: `TrainResponse`.

**Errors**: 400 unknown train; `cab_id` is not a configured cab; `drive_demand`
outside `[-1.0, 1.0]` or not finite.

### POST /api/trains//equipment/

Sets train-facing equipment state through the generic equipment endpoint
(§3.5). `{key}` is the registered equipment type. Equipment changes are applied
immediately by the core (like train controls) and do not advance simulation
time; equipment does not affect train dynamics in this version (§3.1). The
dispatcher validates the body against the target equipment and returns 400
with the component's error message on invalid input.

**Request body** (`EquipmentSetRequest` — the fields used depend on `{key}`):

| Field         | Type               | Used by           | Notes                                                        |
| ------------- | ------------------ | ----------------- | ------------------------------------------------------------ |
| `command`   | string             | `door`, `cab` | `"open"` / `"close"`; `"activate"` / `"deactivate"`. |
| `cab_id`    | integer            | `cab`, `btm`  | Target cab.                                                  |
| `data`      | string             | `btm`           | Base64 opaque payload (atp-api.md §3.2); invalid base64 → 400.        |

**Semantics per equipment key**:

| Key      | Behavior                                                                                                                                                                                               |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `door` | `command` `"open"` / `"close"` sets the train door state.                                                                                                                                        |
| `cab`  | `"activate"` / `"deactivate"` sets `cab_id`'s local activation flag only; cabs carry no authority, flags are independent, and control acceptance is unaffected. `reset` restores the configured flags. |
| `btm`  | Delivers opaque`data` to `cab_id`'s BTM equipment; delivery count and payload appear in `equipment.btm`.                                                                                         |

**Example**:

```http
POST /api/trains/TRAIN001/equipment/door
Content-Type: application/json

{ "command": "open" }
```

**Response 200**: `TrainResponse`.

**Errors**: 400 unknown train; unknown equipment key (no such equipment on the
train); missing or invalid fields for the target equipment.

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
  "cab_ids": [1, 2],
  "speed": 0.5,
  "acceleration": 1.5,
  "position": 0.0125,
  "direction": "forward",
  "drive_demand": 1.0,
  "equipment": {
    "cab": [
      { "cab_id": 1, "active": true },
      { "cab_id": 2, "active": false }
    ],
    "door": { "state": "closed" },
    "btm": [
      {
        "cab_id": 1,
        "pending": false,
        "payload_b64": null,
        "received_count": 0
      }
    ]
  }
}
```

| Field            | Type   | Notes                                                           |
| ---------------- | ------ | --------------------------------------------------------------- |
| `train_id`     | string | Stable identifier.                                              |
| `cab_ids`      | array  | Configured cabs.                                                |
| `speed`        | number | m/s, never negative.                                            |
| `acceleration` | number | m/s², the value actually applied during the last step (§3.4). |
| `position`     | number | m along the linear track, never decreases.                      |
| `direction`    | string | Always`"forward"` in this version (forward-only model).       |
| `drive_demand` | number | The held signed lever in`[-1.0, 1.0]`.                        |
| `equipment`    | object | Nested equipment snapshots, keyed by type; see below.           |

**`equipment` members**:

| Key      | Shape                                                        | Notes                                                                                       |
| -------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| `cab`  | array of`{ cab_id: int, active: bool }`                    | One entry per configured cab; `active` is a local flag with no control-side effect (§3.3).                 |
| `door` | `{ state: "open" \| "closed" }`                             | Train-level door state.                                                                     |
| `btm`  | array of`{ cab_id, pending, payload_b64, received_count }` | Payload bytes are opaque to the simulator and base64-encoded (atp-api.md §3.2).                       |

Future addons add optional keys without changing existing fields; a train
without an equipment type simply omits its key.

**Immutability**: snapshots are read-only views; writing fields back through a
command body has no effect (extra fields are ignored).

## WebSocket /ws

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
`/api/*` and `/ws` are registered before the static mount and are never
shadowed.

## Not implemented yet

Planned in `docs/architectural.md` §5.2 but absent from the current routes:

- `POST /api/signals/{id}` (linear-track signals, Phase 3+).
- The earlier `POST /api/btm/inject` proposal is superseded by the generic
  `POST /api/trains/{train_id}/equipment/btm`.
- The ATP-side TCP/NDJSON protocol (`atp-api.md`) is separate from this HTTP API.
