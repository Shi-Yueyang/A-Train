# A-Train ATP Protocol Specification

This document specifies the wire protocol between the simulator and external
ATP (Automatic Train Protection) processes, as **implemented** by
`src/a_train/adapters/atp/` (`protocol.py` framing and builders, `client.py`
transport, `manager.py` dispatch). The architectural boundary it enforces --
"ATP requests, physics decides" -- is in `architectural.md` §4.1. The
browser-facing HTTP/WebSocket API is a separate contract (`api-spec.md`).

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
the whole message is rejected (`invalid_train_command`, §5.1).

### 2.3 Units

```text
position        m   (distance from a fixed origin on a single linear track)
speed           m/s
acceleration    m/s²
drive_demand    dimensionless, [-1.0, 1.0]
```

### 2.4 Message catalog

| Type          | Wire value          | Direction        | Trigger                                                 | Spec  |
| ------------- | ------------------- | ---------------- | ------------------------------------------------------- | ----- |
| TRAIN_STATE   | `"train_state"`   | simulator → ATP | Cyclic: every published core snapshot.                  | §3.1 |
| BTM_RX        | `"btm_rx"`        | simulator → ATP | Event: when a BTM delivery for this cab lands.          | §3.2 |
| TRAIN_COMMAND   | `"train_command"` | ATP → simulator | Whenever ATP wants to move the train, work the doors, or assert protection signals. | §4.1 |
| ERROR (in)      | `"error"`         | ATP → simulator | ATP reports its own problem; logged only.     | §4.3 |
| ERROR (out)   | `"error"`         | simulator → ATP | Reply to any rejected or malformed input.               | §5   |

These four types are the complete protocol; unknown inbound `type` values are
answered with `ERROR` (§5.1).

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
  "direction": "forward"
}
```

| Field            | Type   | Notes                                                                                                 |
| ---------------- | ------ | ----------------------------------------------------------------------------------------------------- |
| `train_id`     | string | The channel's train.                                                                                  |
| `cab_id`       | int    | The channel's cab; both cabs of one train get identical physical values, differing only in`cab_id`. |
| `speed`        | number | m/s, never negative (forward-only model).                                                             |
| `acceleration` | number | m/s² of the last integrated step.                                                                    |
| `position`     | number | m along the linear track from the fixed origin.                                                       |
| `direction`    | string | `"forward"` in the current model; reserved for future multi-direction movement.                     |

TRAIN_STATE is a read-only observation: ATP derives its protection decisions
from it and acts back on the train only through `TRAIN_COMMAND` (§4.1;
architectural boundary §4.1 of architectural.md).

### 3.2 BTM_RX — opaque balise transmission

One-directional Train → ATP. The simulator models the BTM antenna, delivers
the bytes, and treats the payload as opaque (design principle "BTM is
opaque", architectural.md §7.3); interpretation belongs entirely to ATP.

```json
{
  "type": "btm_rx",
  "data": "ASOk/wCBcg=="
}
```

| Field    | Type   | Notes                                                                  |
| -------- | ------ | ---------------------------------------------------------------------- |
| `data` | string | Raw balise telegram, base64-encoded. Semantics belong entirely to ATP. |

Trigger: exactly one `btm_rx` per accepted BTM delivery for **this cab** --
deliveries are filtered per cab, and each channel carries only its own. A BTM
delivery is made through the simulator's REST equipment endpoint
(`api-spec.md`, `POST /api/trains/{id}/equipment/btm`). After a reconnect,
only fresh deliveries are published: each message corresponds to an increase
of the cab's delivery counter.

---

## 4. ATP → Simulator Messages

### 4.1 TRAIN_COMMAND — the only inbound action

ATP requests a normalized train action on its own cab's channel. ATP asks;
the physics decides the result.

```json
{
  "type": "train_command",
  "train_id": "TRAIN001",
  "cab_id": 1,
  "drive_demand": -1.0,
  "door": "close",
  "atp_signal": "0001000"
}
```

| Field          | Type   | Required | Validation                                             |
| -------------- | ------ | -------- | ------------------------------------------------------ |
| `train_id`     | string | no       | If present, must equal the channel's train.            |
| `cab_id`       | int    | no       | If present, must equal the channel's cab.              |
| `drive_demand` | number | at least one of `drive_demand` / `door` / `atp_signal` is required | Finite, `-1.0 ≤ v ≤ 1.0`, JSON number (not bool/string). |
| `door`         | string | see above | Exactly `"open"` or `"close"`.                        |
| `atp_signal`   | string | see above | Non-empty, every character `"0"` or `"1"` (§4.2).     |

Processing pipeline (manager → core):

1. Validate framing and fields (fail → `ERROR invalid_train_command`, nothing
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

### 4.2 atp_signal — binary protection signals (skeleton)

`atp_signal` is an ordered string of bits, e.g. `"0001000"`. The **leftmost
character is bit index 0**. Each bit position carries a meaning agreed
between ATP and the simulator; `"1"` means the meaning is active, `"0"`
inactive. Bits are a *state assertion* per message, not an event: only the
bits set in the received string are decoded, and nothing latches between
messages.

| Bit value | Meaning       |
| --------- | ------------- |
| `"1"`     | Active.       |
| `"0"`     | Inactive.     |

Decoding is registry-driven in `src/a_train/adapters/atp/signal.py`: each
bit index may be bound to a handler that returns transport-neutral core
commands, which the manager then submits through the ordinary command queue.
The bit-to-action binding is a work in progress (the indices below are
placeholders agreed for the skeleton):

| Bit index | Planned meaning    | Status   | Transfer on the train                          |
| --------- | ------------------ | -------- | ---------------------------------------------- |
| 0         | reserved           | n/a      | --                                             |
| 1         | traction cut-off   | planned  | Force drive demand to the braking side.        |
| 2         | service brake      | planned  | `drive_demand = -1.0` until the next demand.   |
| 3         | emergency brake    | planned  | As service brake, plus (TBD) harder constraint. |

**Current status: no bit is bound yet** -- a valid `atp_signal` is accepted,
answered nothing, and changes no state (the registry in `signal.py` is
empty). Active bits with no registered handler are silently ignored, so ATP
can send the field today and the simulator stays forward-compatible. When
meanings are implemented they are bound in `signal.HANDLERS` and documented
here; the wire format above is stable.

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
  "code": "invalid_train_command",
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

| Code                      | Raised when                                                       | Effect on session                                    |
| ------------------------- | ----------------------------------------------------------------- | ---------------------------------------------------- |
| `malformed_message`     | Line is not valid JSON / not an object / has no`type`.          | Session continues.                                   |
| `unknown_message_type`  | `type` is not in the catalog (§2.4).                           | Session continues.                                   |
| `invalid_train_command` | Identity mismatch or field validation failure (§4.1 step 1).     | Nothing applied; session continues.                  |
| `command_rejected`      | The simulation core refused the resulting command (§4.1 step 4). | The other command of the same message still applies. |

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
`ready` boolean; see `api-spec.md`.

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
ATP     ──> {"type":"train_command","drive_demand":-1.0}
simulator ──> {"type":"train_state","acceleration":-2.0,...}  (deceleration applied)
simulator ──> {"type":"btm_rx","data":"ASOk/wCBcg=="}        (balise passed)
ATP     ──> {"type":"train_command","door":"open","drive_demand":0.5}
              (two commands: control, then equipment)
ATP     ──> {"type":"train_command","atp_signal":"0001000"}
              (protection bits, §4.2; accepted, applied once handlers exist)
simulator ──> {"type":"error","code":"invalid_train_command",...}  (on a bad request)
```
