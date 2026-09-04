# A-Train ATP Protocol Specification

This document specifies the wire protocol between the simulator and external
ATP (Automatic Train Protection) processes, as **implemented** by
`src/a_train/adapters/atp/` (`protocol.py` framing and builders, `client.py`
transport, `manager.py` dispatch). The architectural boundary it enforces --
"ATP requests, physics decides" -- is in `architectural.md` §4.1. The
browser-facing HTTP/WebSocket API is a separate contract (`web-api.md`).

---

## 1. Transport and Connection

### 1.1 Roles and topology

| Aspect         | Rule                                                                                                            |
| -------------- | --------------------------------------------------------------------------------------------------------------- |
| Transport      | TCP, one persistent connection per cab.                                                                         |
| TCP server     | The**ATP process**. It accepts exactly one connection at a time.                                          |
| TCP client     | The**simulator**. It dials each configured endpoint and keeps the connection open.                        |
| Stream content | NDJSON: one JSON object per line,`\n`-terminated, UTF-8.                                                      |
| Cab identity   | Lives in the endpoint configuration (which host:port is dialed). One endpoint = one`(train_id, cab_id)` pair. |

The channel is live the moment TCP opens, and the simulator starts publishing
immediately.

### 1.2 Framing rules

- Every message is a single JSON **object** on one line; the newline is the
  message delimiter.
- The object must contain a string `"type"` field; anything else (invalid
  JSON, non-object, missing `type`) is answered with an `ERROR`
  (`malformed_message`, §5.1) and the connection stays up.
- A line longer than 64 KiB is a stream error: the session is dropped and the
  client reconnects (§1.4).

### 1.3 Channel lifecycle

```text
IDLE ──start()──> CONNECTING ──TCP open──> READY ──peer close / error──> DISCONNECTED
                    ^                                                       │
                    └──────────────── backoff wait ◄────────────────────────┘
STOPPED (only via shutdown)
```

These are the states reported by `GET /api/atp/status` (§6.2). `READY` means
"the TCP socket is open".

### 1.4 Reconnection and backoff

While not READY, the client retries forever:

- Failed attempts double the delay, capped at `30 s`.
- A session that reached `READY` even once resets the delay to the base value
  (`1 s`), so recovery from a dropped link is fast.
- On reconnect the simulator **re-publishes the most recent snapshot** it
  remembers, so an ATP peer that was offline is immediately brought up to
  date.

### 1.5 Dead-link detection

Link failure detection is TCP-based: a closed or reset socket ends the
session, drops the channel to `DISCONNECTED`, and the reconnect loop (§1.4)
takes over.

---

## 2. Message Conventions

### 2.1 Envelope

Every message is a JSON object whose `type` selects its meaning:

```json
{ "type": "<lowercase message name>", ...fields }
```

Unrecognized `type` values on either side are answered with `ERROR`
(`unknown_message_type`) and never applied (§5.1).

### 2.2 Identity fields

Outbound simulator messages always carry the channel's own `train_id` /
`cab_id` where the message type defines them. Inbound ATP messages may carry
`train_id` / `cab_id`; if present they **must match the channel**, otherwise
the whole message is rejected (`invalid_atp_command`, §5.1).

### 2.3 Units

```text
position        m   (distance from a fixed origin on a single linear track)
speed           m/s
acceleration    m/s²
drive_demand    dimensionless, [-1.0, 1.0]
```

### 2.4 Message catalog

| Type        | Wire value        | Direction        | Trigger                                                                             | Spec  |
| ----------- | ----------------- | ---------------- | ----------------------------------------------------------------------------------- | ----- |
| TRAIN_STATE | `"train_state"` | simulator → ATP | Cyclic: every published core snapshot.                                              | §3.1 |
| ATP_COMMAND | `"atp_command"` | ATP → simulator | Whenever ATP wants to move the train, work the doors, or assert protection signals. | §4.1 |
| ERROR (in)  | `"error"`       | ATP → simulator | ATP reports its own problem; logged only.                                           | §4.3 |
| ERROR (out) | `"error"`       | simulator → ATP | Reply to any rejected or malformed input.                                           | §5   |

These three inbound/outbound message families are the complete protocol; unknown inbound `type` values are answered with `ERROR` (§5.1).

---

## 3. Simulator → ATP Messages

### 3.1 TRAIN_STATE — cyclic world observation

Sent once per channel for every snapshot the core publishes (after each fixed
simulation step while running, and after each immediately-applied control),
while the channel is READY.

```json
{
  "type": "train_state",
  "train_id": "TRAIN001",
  "cab_id": 1,
  "speed": 22.31,
  "acceleration": -0.15,
  "position": 15320.4,
  "direction": "forward",
  "equipment": {
    "cab": [
      { "cab_id": 1, "active": true },
      { "cab_id": 2, "active": false }
    ],
    "door": { "state": "closed" },
    "btm": [
      { "cab_id": 1, "pending": false, "payload_b64": null, "received_count": 0 }
    ],
    "stcs_atp": { "traction_cutoff": false, "service": false, "emergency": true }
  }
}
```

| Field            | Type   | Notes                                                                                                                                                  |
| ---------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `train_id`     | string | The channel's train.                                                                                                                                   |
| `cab_id`       | int    | The channel's cab; both cabs of one train get identical physical values, differing only in`cab_id`.                                                  |
| `speed`        | number | m/s, never negative (forward-only model).                                                                                                              |
| `acceleration` | number | m/s² of the last integrated step.                                                                                                                     |
| `position`     | number | m along the linear track from the fixed origin.                                                                                                        |
| `direction`    | string | `"forward"` in the current model; reserved for future multi-direction movement.                                                                      |
| `equipment`    | object | Full equipment snapshot for the train, keyed by equipment type. This is the canonical ATP payload; each nested entry mirrors the train snapshot state. |

`TRAIN_STATE` remains a read-only observation: ATP derives its protection decisions from it and acts back on the train only through `ATP_COMMAND` (§4.1; architectural boundary §4.1 of architectural.md). The simulator publishes the full equipment state because ATP peers may need more than the ATP protection flags alone.

BTM payloads are carried as part of the train snapshot itself under the
`equipment.btm` entry of `TRAIN_STATE`. The simulator models the BTM antenna,
delivers the bytes, and treats the payload as opaque (design principle "BTM is
opaque", architectural.md §7.3); interpretation belongs entirely to ATP.

A BTM delivery is made through the simulator's REST equipment endpoint
(`web-api.md`, `POST /api/trains/{id}/equipment/btm`), and the latest BTM
state appears in the next published `TRAIN_STATE` for that cab.

---

## 4. ATP → Simulator Messages

### 4.1 ATP_COMMAND — the only inbound action

ATP requests a normalized train action on its own cab's channel. ATP asks;
the physics decides the result.

```json
{
  "type": "atp_command",
  "train_id": "TRAIN001",
  "cab_id": 1,
  "drive_demand": -1.0,
  "door": "close",
  "atp_signal": "0001000"
}
```

| Field            | Type   | Required                                                                | Validation                                                  |
| ---------------- | ------ | ----------------------------------------------------------------------- | ----------------------------------------------------------- |
| `train_id`     | string | no                                                                      | If present, must equal the channel's train.                 |
| `cab_id`       | int    | no                                                                      | If present, must equal the channel's cab.                   |
| `drive_demand` | number | at least one of`drive_demand` / `door` / `atp_signal` is required | Finite,`-1.0 ≤ v ≤ 1.0`, JSON number (not bool/string). |
| `door`         | string | see above                                                               | Exactly`"open"` or `"close"`.                           |
| `atp_signal`   | string | see above                                                               | Non-empty, every character`"0"` or `"1"` (§4.2).       |

Processing pipeline (manager → core):

1. Validate framing and fields (fail → `ERROR invalid_atp_command`, nothing
   applied).
2. Map to the transport-neutral commands the REST API uses:
   - `drive_demand` → `TrainControlCommand(train_id, TrainControl(cab_id, drive_demand))`
   - `door` → `EquipmentCommand(train_id, EquipmentSet(key="door", command=door))`
   - `atp_signal` → decoded bit-by-bit into core commands (§4.2)
     A message may carry several fields; each contributes its commands in the
     order listed above.
3. Submit to `SimulationCore`'s command queue. Only `run_loop()` consumes the
   queue; ATP commands interleave with browser commands in arrival order and
   are applied at the same point in the update cycle (immediately, then on
   every fixed step).
4. If the core rejects a command (e.g. unknown train/cab), the ATP peer gets
   `ERROR command_rejected`; the other commands in the same message are
   unaffected.

Semantics of the two controls:

- `drive_demand`: signed normalized lever. Positive scales the traction
  limit, negative scales the deceleration limit; it persists until the next
  demand.
- `door`: flips the train-level door equipment state immediately; door state
  and movement are independent in this version.

All cabs carry equal authority: the same request from cab 1 or cab 2 has the
same effect; cabs are identity (§3.3 architectural).

### 4.2 atp_signal — binary protection signals

`atp_signal` is an ordered string of bits, e.g. `"0001000"`. The **leftmost
character is bit index 0**. Each bit position carries a meaning agreed
between ATP and the simulator; `"1"` means the meaning is active, `"0"`
inactive.

Bits are a *state assertion*: every position the received string defines is
applied (both `"1"` and `"0"`), and positions **beyond the string length keep
their previous value**. ATP state therefore evolves across messages -- the
current flags are derived from the most recent assertion of each position.

Decoding is a pure translation in `src/a_train/adapters/atp/signal.py`: each
bound bit index yields transport-neutral core commands (one per defined
position, ascending index order) which the manager submits through the
ordinary command queue. **The protection state machine lives in the core**, in
the train-level `stcs_atp` component (`domain/equipment.py`), so the flags
are deterministic world state: ordered by the queue, visible to REST and
WebSocket, restored by `reset`, and re-sent on reconnect by the
latest-snapshot catch-up publish (§1.4).

| Bit index | Meaning          | `"1"` asserts     | `"0"` asserts         | Stored flag                    |
| --------- | ---------------- | ------------------- | ----------------------- | ------------------------------ |
| 0         | reserved         | ignored             | ignored                 | --                             |
| 1         | traction cut-off | `traction_cut`    | `traction_release`    | `traction_cutoff` (persists) |
| 2         | service brake    | `brake_service`   | `brake_service_off`   | `service`                    |
| 3         | emergency brake  | `brake_emergency` | `brake_emergency_off` | `emergency`                  |

The three flags are stored and reported independently, exactly as asserted:
sending `"0010"` asserts service (index 2 `'1'`) while releasing emergency
(index 3 `'0'`), leaving `service: true, emergency: false`.

Combination with core physics state (implemented rule, placeholder-grade,
subject to future protection logic): during every fixed step, if the train's
speed is `0.0`, both brake flags are released automatically; the traction
cut-off persists until explicitly released. The flags feed into dynamics
(braking force, cut-off enforcement) in a future phase -- as with all
equipment in this version, movement is unaffected for now.

Unbound bit positions (index ≥ 4) are silently ignored, so ATP can send
longer strings today without breaking older or newer peers.

### 4.3 error (inbound)

ATP may report its own faults with the same envelope as §5:

```json
{ "type": "error", "code": "atp_internal", "detail": "..." }
```

The simulator logs the message (`WARNING`) and the session continues.

---

## 5. ERROR (simulator → ATP)

Any rejected inbound message is answered -- where the framing survives --
without stopping the simulation or any other cab's connection.

```json
{
  "type": "error",
  "code": "invalid_atp_command",
  "detail": "drive_demand must be a finite value in [-1.0, 1.0]",
  "train_id": "TRAIN001",
  "cab_id": 1
}
```

| Field                     | Type         | Notes                                   |
| ------------------------- | ------------ | --------------------------------------- |
| `code`                  | string       | Machine-readable category, table below. |
| `detail`                | string       | Human-readable explanation.             |
| `train_id` / `cab_id` | string / int | Always included on a live channel.      |

### 5.1 Code table

| Code                     | Raised when                                                       | Effect on session                                    |
| ------------------------ | ----------------------------------------------------------------- | ---------------------------------------------------- |
| `malformed_message`    | Line is not valid JSON / not an object / has no`type`.          | Session continues.                                   |
| `unknown_message_type` | `type` is not in the catalog (§2.4).                           | Session continues.                                   |
| `invalid_atp_command`  | Identity mismatch or field validation failure (§4.1 step 1).     | Nothing applied; session continues.                  |
| `command_rejected`     | The simulation core refused the resulting command (§4.1 step 4). | The other command of the same message still applies. |

---

## 6. Configuration and Observability

### 6.1 Configuring endpoints

Endpoints are configured at startup, one per cab
(`README.md` shows the CLI usage; `config.py` defines validation):

```bash
python -m a_train run --atp TRAIN001:1=127.0.0.1:9101 --atp TRAIN001:2=127.0.0.1:9102
python -m a_train run --atp-config atp.json
# atp.json: {"atp_endpoints": [{"train_id": "TRAIN001", "cab_id": 1,
#                               "host": "127.0.0.1", "port": 9101}]}
```

- `--atp TRAIN_ID:CAB_ID=HOST:PORT` is repeatable; file and flag entries are
  merged; a duplicate `(train_id, cab_id)` fails startup.
- Validation: non-empty `train_id`/`host`, `cab_id` ≥ 1, `port` 1-65535.
- The default configuration (no endpoints) starts the simulator with zero ATP
  connections.

### 6.2 Channel status

`GET /api/atp/status` reports every configured endpoint with its current
lifecycle state (IDLE / CONNECTING / READY / DISCONNECTED / STOPPED) and a
`ready` boolean; see `web-api.md`.

---

## 7. Guarantees

- Messages on one channel arrive in submission order (TCP, single writer).
- A misbehaving ATP peer is contained to its own channel: bad input is
  answered, slow draining is absorbed by the core's bounded snapshot queues
  (oldest snapshot dropped), and physics waits for no adapter I/O
  (architectural §2.6).
- The simulator runs ATP-free as configured: endpoint connections open,
  close, and fail independently of the simulation loop and of each other.

---

## Appendix: Typical Session

```text
ATP process starts (TCP server)          simulator connects, channel READY
simulator ──> {"type":"train_state",...}          (every published snapshot)
simulator ──> {"type":"train_state",...}
ATP     ──> {"type":"atp_command","drive_demand":-1.0}
simulator ──> {"type":"train_state","acceleration":-2.0,...}  (deceleration applied)
simulator ──> {"type":"train_state",...,"equipment":{"btm":[{"cab_id":1,"pending":true,"payload_b64":"ASOk/wCBcg==","received_count":1}]}}
ATP     ──> {"type":"atp_command","door":"open","drive_demand":0.5}
              (two commands: control, then equipment)
ATP     ──> {"type":"atp_command","atp_signal":"0001000"}
              (emergency brake asserted on bit 3; state lands in the core)
simulator ──> {"type":"train_state",...,"stcs_atp":{"traction_cutoff":false,"service":false,"emergency":true}}
simulator ──> {"type":"error","code":"invalid_atp_command",...}  (on a bad request)
```
