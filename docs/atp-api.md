# A-Train ATP Protocol Specification

This document specifies the wire protocol between the simulator and external
ATP (Automatic Train Protection) processes, as **implemented** by
`src/a_train/adapters/atp/` (`protocol.py` framing and builders,
`connection.py` transport, `manager.py` dispatch). The architectural boundary it enforces --
"ATP requests, physics decides" -- is in `architectural.md` §4.1. The
browser-facing HTTP/WebSocket API is a separate contract (`web-api.md`).

---

## 1. Transport and Connection

### 1.1 Roles and topology

| Aspect         | Rule                                                                                                            |
| -------------- | --------------------------------------------------------------------------------------------------------------- |
| Transport      | TCP, one persistent connection per ATP peer.                                                                    |
| TCP server     | The **simulator**. It accepts ATP client connections on each configured listener.                         |
| TCP client     | The **ATP process**. It connects to A-Train and keeps the connection open.                                  |
| Stream content | NDJSON: one JSON object per line,`\n`-terminated, UTF-8.                                                      |
| Channel identity | **None.** Connections carry no cab or train binding; the endpoint list is pure network addressing. Equipment addressing and command targets live entirely in the message payloads. |

The channel is live the moment TCP opens, and the simulator starts publishing
immediately. Every connected ATP peer receives the **same** whole-train
broadcast: all channels carry byte-identical messages.

### 1.2 Framing rules

- Every message is a single JSON **object** on one line; the newline is the
  message delimiter.
- The object must contain a string `"type"` field; anything else (invalid
  JSON, non-object, missing `type`) is answered with an `ERROR`
  (`malformed_message`, §5.1) and the connection stays up.
- A line longer than 64 KiB is a stream error: the session is dropped and the
  ATP client may reconnect.

### 1.3 Channel lifecycle

```text
A-Train listener: STOPPED ──start()──> READY ──shutdown──> STOPPED
ATP client:       disconnected ──TCP open──> connected ──close/error──> disconnected
```

`GET /api/atp/status` reports listener readiness and the number of connected
ATP clients (§6.2). Listener `READY` means A-Train is accepting connections;
it does not mean that an ATP client is currently connected.

### 1.4 Reconnection and backoff

ATP owns reconnect policy. It may retry after a failed connection or a dropped
session. A-Train remains listening and accepts the next connection; it does
not dial ATP or run a reconnect loop.

On every accepted connection the simulator publishes the latest snapshot
immediately, so a reconnected ATP peer is brought up to date.

### 1.5 Dead-link detection

Link failure detection is TCP-based: a closed or reset socket ends the
session. A-Train continues listening; the ATP client may reconnect (§1.4).

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

The wire protocol carries no train identity: the simulator hosts exactly one
train and the ATP_COMMAND `cab_id` names the train implicitly. `cab_id` is
**required** on every inbound `ATP_COMMAND` and is the sole routing selector
for cab-scoped actions. Outbound `TRAIN_STATE` equipment entries carry
`cab_id` so each peer can pick the instances it cares out of the identical
broadcast. An inbound message carrying `train_id`, or missing/invalid
`cab_id`, is rejected (`invalid_atp_command`, §5.1).

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

Cab key state, activation, and facing are native train state and are **not
sent** on the ATP channel; they remain observable through REST and WebSocket
(`web-api.md`). `TRAIN_STATE.equipment` is a flat array carrying **only the
ATP-relevant equipment instances**: those whose `type` is `btm` or
`stcs_atp_duo` / `stcs_atp_solo`. All other equipment (doors, driving
systems, ...) is filtered out before sending. Each entry has `type`,
`cab_id`, and `state` fields, and peers address instances by the
`(type, cab_id)` pair: the BTM for cab 1 is the entry with
`type: "btm"` and `cab_id: 1`. Internal instance keys are never sent. ATP
must not look for type-grouped `equipment.btm` objects; select the entry by
type and cab. Each `(type, cab_id)` pair is unique on the train, enforced at
configuration time.

### 3.1 TRAIN_STATE — cyclic world observation

Sent once per READY peer for every snapshot the core publishes (after each
fixed simulation step while running, and after each immediately-applied
control). All peers receive the same whole-train content.

```json
{
  "type": "train_state",
  "speed": 22.31,
  "acceleration": -0.15,
  "position": 15320.40,
  "direction": "forward",
  "equipment": [
    { "type": "btm", "cab_id": 1, "state": { "pending": false, "payload_b64": null, "received_count": 0 } },
    { "type": "stcs_atp_duo", "cab_id": 1, "state": {
        "train_out_signal": "110110000000000000000000000000"
    } }
  ]
}
```

| Field            | Type   | Notes                                                                                                                                                  |
| ---------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `speed`        | number | m/s, signed: negative means rearward travel.                                                                                                           |
| `acceleration` | number | m/s² of the last integrated step.                                                                                                                     |
| `position`     | number | m along the linear track from the fixed origin; may decrease with rearward motion.                                                                    |
| `direction`    | string | Derived from the speed sign: `"forward"`, `"backward"`, or `"stopped"`.                                                                              |
| `equipment`    | array | Whole-train equipment entries, each shaped as `{type, cab_id, state}`: only `btm` and `stcs_atp_duo` / `stcs_atp_solo` instances. An `stcs_atp_*` entry's `state` is only `{train_out_signal}` -- the raw `last_command`, its `last_command_time`, and the named state maps stay in REST/WebSocket (§4.2). |

`TRAIN_STATE` remains a read-only observation: ATP derives its protection decisions from it and acts back on the train only through `ATP_COMMAND` (§4.1; architectural boundary §4.1 of architectural.md). The simulator filters every other equipment type out of the wire message; the full equipment state remains available through the Web API (`web-api.md`).

BTM payloads are carried as part of the train snapshot itself under the
entry whose `(type, cab_id)` pair is `("btm", N)`, in `TRAIN_STATE`. The
simulator models the BTM antenna and delivers the bytes but treats the
payload as opaque (design principle "BTM is opaque", architectural.md §7.3);
interpretation belongs entirely to ATP.

A BTM delivery is made through the simulator's REST equipment endpoint
(`web-api.md`, `POST /api/trains/{id}/equipment/btm_1`), and the latest BTM
state appears in the next published `TRAIN_STATE`.

---

## 4. ATP → Simulator Messages

### 4.1 ATP_COMMAND — the only inbound action

ATP requests a normalized train action and names the acting cab in the
message; the connection carries no cab binding. ATP asks; the physics decides
the result.

```json
{
  "type": "atp_command",
  "cab_id": 1,
  "drive_demand": -1.0,
  "door": "close",
  "atp_signal": "0001000"
}
```

| Field            | Type   | Required                                                                | Validation                                                  |
| ---------------- | ------ | ----------------------------------------------------------------------- | ----------------------------------------------------------- |
| `cab_id`       | int    | **yes**                                                                 | Positive integer. Must match one of the train's cabs; an unconfigured cab is rejected by the core (§4.1 step 4). |
| `drive_demand` | number | at least one of`drive_demand` / `door` / `atp_signal` is required | Finite,`-1.0 ≤ v ≤ 1.0`, JSON number (not bool/string). |
| `door`         | string | see above                                                               | Exactly`"open"` or `"close"`.                           |
| `atp_signal`   | string | see above                                                               | Non-empty, every character`"0"` or `"1"` (§4.2).       |

A `train_id` field is **not part of the protocol** and makes the whole
message invalid.

Processing pipeline (manager → core):

1. Validate framing and fields (fail → `ERROR invalid_atp_command`, nothing
   applied).
2. Map to the transport-neutral commands the REST API uses, stamping the
   domain train identity from the core:
   - `drive_demand` → `TrainControlCommand(train, TrainControl(cab_id, drive_demand))`
   - `door` → two `EquipmentCommand` values targeting `left_door` and `right_door` with the same command
   - `atp_signal` → one STCS ATP command targeting the named `cab_id`, containing the raw signal (§4.2)
      A message may carry several fields; each contributes its commands in the
      order listed above.
3. Submit to `SimulationCore`'s command queue. Only `run_loop()` consumes the
   queue; ATP commands interleave with browser commands in arrival order and
   are applied at the same point in the update cycle (immediately, then on
   every fixed step).
4. If the core rejects a command (e.g. a cab that is not configured on the
   train, or has no STCS ATP instance), the ATP peer gets
   `ERROR command_rejected`; the other commands in the same message are
   unaffected.

Semantics of the two controls:

- `drive_demand`: signed normalized lever. Positive scales the traction
  limit, negative scales the deceleration limit as a brake-style force that
  clamps at zero speed and never moves a standing train rearward; it persists
  until the next demand. While any cab's driving system is engaged (its mode
  handle not `off`) it **overwrites** the legacy lever, but the lever value
  persists and applies again once every driving system is off.
- `door`: flips the train-level door equipment state immediately; door state
  and movement are independent in this version.

All cabs carry equal authority: any peer may command any configured cab --
the `cab_id` in the message is a routing selector, not an
authorization check; cabs are identity (§3.3 architectural).

### 4.2 atp_signal — binary protection signals

`atp_signal` is an ordered string of bits, e.g. `"0001000"`. The **leftmost
character is bit index 0**. Each bit position carries a meaning agreed
between ATP and the simulator; `"1"` means the meaning is active, `"0"`
inactive.

Bits are a *state assertion*: every position the received string defines is
applied (both `"1"` and `"0"`), and positions **beyond the string length keep
their previous value**. ATP state therefore evolves across messages -- the
current flags are derived from the most recent assertion of each position.

The ATP manager performs the transport translation in
`src/a_train/adapters/atp/manager.py`.
The addressed cab's `stcs_atp_duo_<cab_id>` component
(`domain/equipment/stcs_atp.py`) then applies each bit to a plain internal
boolean state (the *train-in states*). Both state maps are exposed as
`StcsAtpSnapshot.train_in_states` and `train_out_states`: `{name, value}`
entries in bit order, visible in REST and WebSocket. The ATP wire format
carries only the compact `train_out_signal` bit string (§3.1). The train-in
states are reset to `false` by `reset`, and a shorter signal leaves
unmentioned bit positions unchanged.

| Bit index | Train-in state |
| --------- | -------------- |
| 0 | `emergency_brake_1` |
| 1 | `emergency_brake_2` |
| 2 | `maximum_service_brake_7` |
| 3 | `ato_enable` |
| 4 | `turnback_activation` |
| 5 | `powerless_passed_command` |
| 6 | `cut_off_traction` |
| 7 | `service_brake_4` |
| 8 | `service_brake_1` |
| 9 | `open_left_door_permit_1` |
| 10 | `open_left_door_permit_2` |
| 11 | `open_right_door_permit_1` |
| 12 | `open_right_door_permit_2` |
| 13 | `powerless_passed_select` |
| 14 | `c2_authorized` |
| 15 | `c2_zero_speed` |
| 16 | `turnback_indicator` |

The raw signal is also retained as `last_command` for diagnostics, together
with `last_command_time` -- the wall-clock time (POSIX seconds, UTC) at which
the core applied that assertion (both REST and WebSocket only; they are
filtered out of `TRAIN_STATE`, §3.1). An empty or other non-binary command is
invalid.

The STCS ATP component also maintains a plain internal `train_out_states` map
for the 30 train-side feedback bits. It is exposed as `train_out_signal` (bit string) and
`train_out_states` (named entries) in `StcsAtpSnapshot`. The remaining
train-originated bits (buttons, panel states, and -- on a cab without a fitted
switch box -- the `system_switch_*` bits) have no simulator-side source and are asserted through the Web API equipment endpoint; `c2_control_state_1_1` / `c2_control_state_1_2` instead follow `system_switch_c2` and `c2_control_state_2_1` / `c2_control_state_2_2` follow `system_switch_cbtc` (all four low on AUTO) -- those four bits are STCS-internal derivations, blockable like the brake feedbacks
(`web-api.md` §3.5, `POST .../equipment/stcs_atp_duo_<cab_id>` with
`train_out_signal`); the derived feedback bits below always reflect real
train state, unless that signal's derivation has been blocked through the Web
API (`web-api.md`), in which case the bit keeps its last value — and an
operator assertion on a blocked bit sticks — until unblocked. Blocking
concerns only this train-side derivation: ATP-to-train bits are asserted as
always, and blocked feedback bits simply reach ATP as frozen bit values. The
following feedback states are derived from ATP command states:
`emergency_brake_1_inner_feedback` is active-low: it is `false` when
`emergency_brake_1` is active and `true` otherwise. Likewise,
`emergency_brake_2_inner_feedback` is `false` when `emergency_brake_2` is
active and `true` otherwise.
`emergency_brake_feedback` aggregates the pair: it is `true` when either
emergency brake is active and `false` when both are clear, and
`service_brake_7_feedback` is active-low: it is `false` when
`maximum_service_brake_7` is active and `true` otherwise:

| Bit | Train-out state |
| ---: | --- |
| 0 | `emergency_brake_1_inner_feedback` |
| 1 | `emergency_brake_2_inner_feedback` |
| 2 | `emergency_brake_feedback` |
| 3 | `service_brake_7_feedback` |
| 4 | `cab_activation` |
| 5 | `direction_handle_forward_1` |
| 6 | `direction_handle_forward_2` |
| 7 | `direction_handle_backward` |
| 8 | `sleep_signal` |
| 9 | `traction_handle_traction` |
| 10 | `traction_handle_brake` |
| 11 | `turnback_button` |
| 12 | `turnback_activation_feedback` |
| 13 | `left_door_open_button` |
| 14 | `right_door_open_button` |
| 15 | `left_door_close_button` |
| 16 | `right_door_close_button` |
| 17 | `key_activation` |
| 18 | `left_door_open_permit_feedback` |
| 19 | `right_door_open_permit_feedback` |
| 20 | `door_state_1` |
| 21 | `door_state_2` |
| 22 | `cbtc_authorized_command` |
| 23 | `c2_control_state_1_1` |
| 24 | `c2_control_state_2_1` |
| 25 | `system_switch_c2` |
| 26 | `system_switch_auto` |
| 27 | `system_switch_cbtc` |
| 28 | `c2_control_state_1_2` |
| 29 | `c2_control_state_2_2` |

`door_state_1` and `door_state_2` (bits 20-21) reflect the physical door
state: true while `left_door` / `right_door` is open. Each door reports its
current state as an equipment intent carrying the matching cab's
`stcs_atp_duo_<cab_id>` control type
(`StcsAtpControl` door-feedback fields); the train aggregate resolves the
feedback whenever equipment changes, steps, resets, or is constructed with a
configured open door.

`direction_handle_forward_1` / `direction_handle_forward_2` (bits 5-6),
`direction_handle_backward` (bit 7), `traction_handle_traction` (bit 9), and
`traction_handle_brake` (bit 10) mirror the driving systems' raw handle
positions of the driving system feeding this instance (see §3.1 and
architectural.md §3.5). `direction_handle_forward_1` and
`direction_handle_forward_2` are duplicate wires of one
'direction handle forward' signal and always read identically (the
`_1`/`_2` suffix carries no cab meaning); `direction_handle_backward`,
`traction_handle_traction`, and `traction_handle_brake` mirror the other
handle positions. Each `driving_X`
equipment asserts its full handle state to the matching cab's
`stcs_atp_duo_<cab_id>` through an
`StcsAtpControl` intent resolved by the train aggregate.

Asserting `maximum_service_brake_7` (bit 2) is not merely recorded: the
The addressed cab's `stcs_atp_duo_<cab_id>` equipment emits a `train`-target intent asserting a full brake
effort, which the train applies as a motion-opposing protection brake on every
step the bit remains asserted, including rearward braking of a
track-negative train. The brake clamps to a stop within a step and produces no
force at standstill. The protection brake acts as a constraint alongside the
driver: an engaged driving system cannot clear it, and releasing the bit
(bit `0`) removes the braking force.

Unbound bit positions (index ≥ 17) are silently ignored, so ATP can send
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
without stopping the simulation or any other connection.

```json
{
  "type": "error",
  "code": "invalid_atp_command",
  "detail": "drive_demand must be a finite value in [-1.0, 1.0]"
}
```

| Field      | Type   | Notes                                   |
| ---------- | ------ | --------------------------------------- |
| `code`   | string | Machine-readable category, table below. |
| `detail` | string | Human-readable explanation.             |

The envelope carries no identity fields: the session that asked is the
session that is answered.

### 5.1 Code table

| Code                     | Raised when                                                       | Effect on session                                    |
| ------------------------ | ----------------------------------------------------------------- | ---------------------------------------------------- |
| `malformed_message`    | Line is not valid JSON / not an object / has no`type`.          | Session continues.                                   |
| `unknown_message_type` | `type` is not in the catalog (§2.4).                           | Session continues.                                   |
| `invalid_atp_command`  | Missing/invalid `cab_id`, a forbidden `train_id` field, or a field validation failure (§4.1 step 1). | Nothing applied; session continues.                  |
| `command_rejected`     | The simulation core refused the resulting command (§4.1 step 4). | The other command of the same message still applies. |

---

## 6. Configuration and Observability

### 6.1 Configuring listeners

Listeners are configured in the train configuration file passed to
`--train-config`, as the top-level `atp` array of local addresses for A-Train
to bind
(`README.md` shows the CLI usage; `config.py` defines validation):

```json
{
  "train": { "train_id": "TRAIN001", "cabs": [ ... ], "physics": { ... } },
  "atp": [
    { "host": "127.0.0.1", "port": 9101 },
    { "host": "127.0.0.1", "port": 9102 }
  ]
}
```

```bash
python -m a_train run --train-config train.json
```

- Each entry has exactly `host` and `port`; unknown fields fail startup. The
  whole file is validated before the server starts.
- Validation: non-empty `host` and `port` 1-65535. ATP connects to the
  configured address. A listener is not bound to a cab: every accepted peer
  receives the same broadcast and may command any cab. Multiple listener
  addresses may be configured.
- A missing or empty `atp` array starts without an ATP listener.

The `train` object in the same file defines the single train's cabs, physics,
and equipment instances; it is unrelated to the ATP listener addresses and required in
every configuration file.

### 6.2 Channel status

`GET /api/atp/status` reports each configured listener (`host`, `port`),
listener lifecycle (`READY` / `STOPPED`), a `ready` boolean, and `active_peers`;
see `web-api.md`.

---

## 7. Guarantees

- Messages on one channel arrive in submission order (TCP, single writer).
- A misbehaving ATP peer is contained to its own channel: bad input is
  answered, slow draining is absorbed by the core's bounded snapshot queues
  (oldest snapshot dropped), and physics waits for no adapter I/O
  (architectural §2.6).
- The simulator runs ATP-free as configured. Accepted peer sessions open,
  close, and fail independently of the simulation loop and of each other.

---

## Appendix: Typical Session

```text
simulator starts (TCP server)            ATP process connects, channel READY
simulator ──> {"type":"train_state",...}        (every published snapshot)
simulator ──> {"type":"train_state",...}
ATP     ──> {"type":"atp_command","cab_id":1,"drive_demand":-1.0}
simulator ──> {"type":"train_state","acceleration":-2.0,...}  (deceleration applied)
simulator ──> {"type":"train_state",...,"equipment":[...,{"type":"btm","cab_id":1,"state":{"pending":true,"payload_b64":"ASOk/wCBcg==","received_count":1}}]}
ATP     ──> {"type":"atp_command","cab_id":1,"door":"open","drive_demand":0.5}
              (two commands: control, then equipment)
ATP     ──> {"type":"atp_command","cab_id":1,"atp_signal":"0001000"}
              (protection bits asserted; state lands in the core)
simulator ──> {"type":"train_state",...,"equipment":[...,{"type":"stcs_atp_duo","cab_id":1,"state":{"train_out_signal":"0110001111..."}}]}
simulator ──> {"type":"error","code":"invalid_atp_command",...}  (on a bad request)
```
