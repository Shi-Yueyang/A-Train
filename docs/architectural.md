# Train Simulator Architecture Design Document

**Primary goal:** Simple, deterministic, testable, cross-platform train simulation that communicates with external ATP processes via a browser-based UI.

---

# 1. Overview

## 1.1 Goals

The system shall:

* Run on **Linux and Windows**.
* Simulate one or more trains.
* Support trains with **one or two cabs**.
* Communicate with external ATP processes using **TCP**.
* Use a **text-based protocol** for easy debugging.
* Transport arbitrary binary BTM datagrams.
* Provide a simple **web UI**, not high-end 3D graphics.
* Run completely **headless** for automated testing.
* Make it easy to add new train types.

Non-goals for the initial version:

* ATP implementation.
* High-fidelity 3D rendering.
* Multiplayer.
* Distributed simulation.
* Extremely high-performance physics.
* Real-world safety certification.
* ATP fail-safe / fault-tolerance behavior.
* Track topology beyond a single linear track.

## 1.2 High-Level Architecture

```text
                         ┌──────────────────────┐
                         │       Browser        │
                         │                      │
                         │ HTML / TypeScript    │
                         │ SVG / CSS            │
                         └──────────┬───────────┘
                                    │
                              HTTP / WebSocket
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────┐
│                       SIMULATOR PROCESS                           │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                     Web API Server                         │  │
│  │                    FastAPI + WebSocket                     │  │
│  └──────────────────────────┬─────────────────────────────────┘  │
│                             │                                    │
│  ┌──────────────────────────▼─────────────────────────────────┐  │
│  │                     Simulation Core                        │  │
│  │                                                            │  │
│  │  State Machine + Fixed-Step Clock                           │  │
│  │  Command Queue                                               │  │
│  │  Train Models + Read-Only State Snapshots                   │  │
│  └──────────────────────────┬─────────────────────────────────┘  │
│                             │                                    │
│                    ┌────────┴─────────┐                          │
│                    │ ATP Manager      │                          │
│                    │ snapshots/commands│                         │
│                    └────────┬─────────┘                          │
│                             │                                    │
│                 ┌───────────┴───────────┐                        │
│                 │                       │                        │
│          ATP Connection A       ATP Connection B                 │
│                 │                       │                        │
│              TCP Client             TCP Client                    │
└─────────────────┼───────────────────────┼────────────────────────┘
                  │                       │
                  │ TCP                   │ TCP
                  ▼                       ▼
          ┌───────────────┐       ┌───────────────┐
          │ ATP Process A │       │ ATP Process B │
          │     Cab 1     │       │     Cab 2     │
          └───────────────┘       └───────────────┘
```

The central architectural principle is:

> **The Simulator Core owns the simulated physical world. ATP processes observe the world and issue commands through a defined protocol.**

ATP never directly accesses simulator objects.

## 1.3 Process Architecture

There are three categories of processes.

**Simulator process** is organised in three layers (the same split as the
repository structure in §7.2):

* **Domain model** — train aggregates (physics and controls), train-local
  equipment (doors, cabs, BTM, ATP protection, §3.5), and the world model
  (linear track and signals).
* **Simulation core** — simulation state machine and fixed-step clock,
  serialized command processing, and read-only state snapshots.
* **Adapters** (in-process) — ATP connections and the Web API. They translate
  external protocols into core commands and never touch domain objects
  directly.

**External ATP processes** — ATP is an external system, not implemented by this project. Each cab connects to its own ATP process:

```text
Train
│
├── Cab 1
│   └── External ATP Process 1
│
└── Cab 2
    └── External ATP Process 2
```

The simulator communicates with ATP via TCP/NDJSON. ATP processes are independent. A failure of one should not crash the simulator. No fail-safe behavior (e.g. automatic deceleration on ATP loss) is defined; see non-goals.

**Browser** is a presentation and control client. It must not contain core simulation logic.

```text
Browser
   │
   ├── display state
  ├── submit commands
   └── control simulation
```

# 2. Simulation

## 2.1 Responsibilities

The Simulation Core owns simulation time and the ordered update of the
simulated world. It exposes commands to run, pause, reset, and select the time
mode. It does not contain HTTP, WebSocket, or TCP connection handling; those
adapters submit commands to the core and publish state produced by it.

The core is the sole writer of train state. Browser controls and ATP input are
converted into commands. Control commands are applied immediately by the core
loop.

## 2.2 Simulation State

```text
STOPPED --run--> RUNNING --pause--> PAUSED
   ^                 |                 |
   |------ reset ----+------ reset -----+
```

| State       | Meaning                                                  |
| ----------- | -------------------------------------------------------- |
| `STOPPED` | No simulation time advances.                             |
| `RUNNING` | The core advances simulation time and updates the world. |
| `PAUSED`  | World state and simulation time are frozen.              |

`run` is idempotent while already running, and `pause` is idempotent while
paused or stopped. `reset` restores the simulator's initial world state, sets
simulation time to zero, and leaves the core stopped.

## 2.3 Clock and Time Modes

Simulation time is measured in seconds from reset:

```text
simulation_time = 0.0 at reset
```

While the core is running, each update calculates a non-negative elapsed
monotonic wall-clock duration `wall_delta`. The core never uses calendar time
for simulation calculations.

| Mode         | Time advancement                                                          |
| ------------ | ------------------------------------------------------------------------- |
| `REALTIME` | `simulation_delta = wall_delta`                                         |
| `SCALED`   | `simulation_delta = wall_delta * time_multiplier`                       |
| `MANUAL`   | Simulation time advances only through an explicit`step(delta)` command. |

`time_multiplier` is a positive finite number. `1.0` is real time, values
greater than `1.0` accelerate the simulation, and values between `0` and `1.0`
slow it down. `MANUAL` mode makes automated tests independent of scheduling
latency and machine speed.

To prevent a stalled process or debugger break from producing an unexpectedly
large physics update, wall-clock updates are subdivided into fixed simulation
steps. The initial implementation uses a configurable fixed step of `0.05 s`.
Any remaining fractional duration is retained for the next update. The same
fixed-step path is used for real-time, scaled, and manual advancement.

## 2.4 Update Cycle

All simulation mutations occur on one simulation execution context. Network
readers and browser handlers may run concurrently, but enqueue commands rather
than changing world objects directly. This gives the core a deterministic order
of operations and avoids locking inside train physics.

The core processes control commands as soon as it receives them. These are
`run`, `pause`, `reset`, `set_time_mode`, and manual step requests. A control
command updates state and produces a snapshot immediately; it does not wait
for a physics step.

For every nominal fixed simulation step, the core performs the following
sequence.

```text
1. Apply queued ATP and train-control commands in arrival order.
2. Advance simulation time by the nominal fixed-step duration.
3. Update each train's equipment and physics in stable train-ID order.
4. Produce a state snapshot for the Web API and ATP Manager.
```

The snapshot is read-only and includes the current simulation state, mode,
time multiplier, simulation time, and train state. The core also produces a
snapshot after every control-state transition. The Web API may publish
snapshots at a lower display rate, but it must not alter their contents or
advance the simulation.

The initial core API is:

```text
run()
pause()
reset()
set_time_mode(mode, time_multiplier=None)
step(delta)                 # valid only in MANUAL mode
submit_command(command)
get_snapshot()
```

`step(delta)` accepts a non-negative duration and returns only after all whole
fixed steps have been processed. This is the primary interface for
deterministic headless tests.

## 2.5 Implementation Guide

Implement `SimulationCore` as the orchestration module for clock, commands,
train models, and snapshots. Put physics in `train.py` and protocol parsing
and transport in `atp.py`; neither module advances simulation time.

### Data Types and Ownership

Define `SimulationState` and `TimeMode` as `Enum` types. Define `Command` and
`SimulationSnapshot` as frozen `dataclass` types. Keep mutable world state
private to `SimulationCore`.

`get_snapshot()` returns a newly constructed `SimulationSnapshot` containing
only scalar values, immutable tuples, and frozen nested snapshot dataclasses.
It never returns a train object, list, dictionary, or other mutable internal
collection.

### Core Loop

Run `SimulationCore` and all FastAPI, WebSocket, and TCP adapter tasks on one
`asyncio` event loop. Start exactly one `SimulationCore.run_loop()` task; it
is the sole simulation execution context. Adapters submit frozen commands to
an `asyncio.Queue[Command]`, and only `run_loop()` consumes that queue or
mutates world state. Code running outside the event loop must schedule queue
writes with `loop.call_soon_threadsafe()`.

While stopped, paused, or in `MANUAL` mode, `run_loop()` waits for the next
command. While running in a wall-clock mode, it waits for either the next
command or the next nominal fixed-step deadline. It applies control commands
immediately. It buffers ATP and train-control commands until the next nominal
fixed-step boundary, where it applies them in enqueue-sequence order.

For real-time modes, measure elapsed duration with `time.monotonic()`, apply
the active multiplier, and add the result to an accumulator. While the
accumulator contains at least one fixed step, execute the Chapter 2 update
cycle and subtract that step. Reset the monotonic-clock reference whenever the
core pauses, stops, or changes time mode so paused time is never accumulated.

In `MANUAL` mode, `step(delta)` enqueues a `StepCommand(delta)` and awaits its
completion result. `run_loop()` handles that command immediately, adds `delta`
to the same accumulator used by wall-clock modes, completes every resulting
nominal fixed step, then resolves the result. This keeps manual and wall-clock
execution on exactly the same code path.

### Commands and Snapshots

Define the command types `RunCommand`, `PauseCommand`, `ResetCommand`,
`SetTimeModeCommand`, `StepCommand`, and
`TrainControlCommand`. Assign each command a monotonically increasing enqueue
sequence and process commands in that sequence. Return a structured command
result for invalid input; do not let adapter
exceptions enter `run_loop()`.

Build one snapshot at the end of each nominal fixed step and after every
control-state transition. Publish it to a bounded
`asyncio.Queue[SimulationSnapshot]` owned by each subscriber task.
When a subscriber queue is full, discard its oldest snapshot before adding the
new one. WebSocket and ATP publisher tasks consume their own queues; they
never perform network I/O in `run_loop()`, so a slow client cannot delay
physics.

### Test Strategy

Use integration tests only. Each test starts the application through
`bootstrap.py` with its real `SimulationCore`, FastAPI application, WebSocket
publisher, and ATP adapter. Replace external ATP processes with a controllable
test TCP server that speaks the production NDJSON protocol.

Each integration test configures the required initial state, selects `MANUAL`
mode through the REST API, advances time through `POST /api/simulation/step`,
and verifies observable behavior through REST responses, WebSocket snapshots,
and the test ATP server's received and sent protocol messages. Tests must not
access core or domain objects directly.

The integration suite must cover complete workflows for run/pause/reset,
real-time multiplier configuration, train movement, BTM transmission, and ATP
state commands. Each test uses a fixed random seed so a failure can be
reproduced exactly.

# 3. Train Model

Equipment is modeled as a flat collection of uniquely keyed instances. Each
instance has a behavior `type` and an instance `key`; train-scoped equipment
such as `left_door` and `right_door`, and cab-scoped equipment such as `btm_1`, use the same
addressing model. Snapshots preserve this shape as an array of `{type, key,
state}` entries. Adapters address equipment by key and do not infer identity
from list position or an implicit slot.

## 3.1 Responsibility and Boundary

The train model owns mutable train state and converts accepted control commands
into physical motion. In this version no train-facing equipment affects the
physical integration; future train rules may explicitly define such effects. It does not know whether a command originated from the
browser, an ATP process, or a test. Adapters identify the train and cab, then
submit a transport-neutral command to the simulation core.

The model must not expose mutable state to adapters. The core reads immutable
snapshots for publication, and only the core invokes the train's per-step
update method.

ATP requests train actions; it never sets position, speed, or acceleration
directly. The train model determines the physical result of drive requests.

## 3.2 Configuration and State

Each train has immutable configuration and mutable runtime state.

| Category       | Required values                                                                                                            |
| -------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Configuration  | Train ID, one or two cab IDs, initial cab activation flags, initial position, maximum traction acceleration, and maximum deceleration. |
| Physical state | Position in metres, speed in metres per second, and acceleration in metres per second squared.                              |
| Control state  | Signed drive demand and door state.                                                                                         |

The cab activation flags and initial physical state are supplied by simulator
configuration. `reset` restores those configured values and clears all control
state, including the door state.

Acceleration limits are positive, finite, per-train configuration values.
Drive demand is a signed, normalized lever in `[-1.0, 1.0]`: positive scales
the maximum traction acceleration, negative scales the maximum deceleration.
The model knows force and speed only; there is no separate brake concept.
This version models forward-only movement: position never decreases and speed
never becomes negative.

## 3.3 Controls

The train accepts these normalized commands from any configured cab. Cabs are
train-local equipment (§3.5) and carry no authority; a cab's activation flag
never affects control acceptance or dynamics:

| Command              | Range or value     | Effect                                                                                                  |
| -------------------- | ------------------ | ------------------------------------------------------------------------------------------------------- |
| Drive demand         | `-1.0` to `1.0` | Signed normalized force lever: positive requests a proportion of maximum traction acceleration, negative requests a proportion of maximum deceleration. |
| Door command         | Open or close      | Changes the train door state.                                                                            |

## 3.4 Per-Step Dynamics

The simulation core updates every train once for each fixed simulation step in
stable train-ID order. For a step duration `dt`, the train resolves one
acceleration value from the signed drive demand:

1. If drive demand is greater than zero, use maximum traction acceleration scaled by the demand.
2. Otherwise, if drive demand is less than zero, use negative maximum deceleration scaled by the demand magnitude.
3. Otherwise, use zero acceleration.

The model integrates the resolved acceleration over `dt`, clamps the resulting
speed to zero or greater, and updates position using the average of the prior
and resulting speed. If deceleration would bring the train to rest within a
step, the model clamps speed to zero and uses only the distance travelled
before stopping. It must not create reverse movement through numerical
integration.

Acceleration recorded in the public snapshot is the acceleration actually
applied during that step. At standstill with no effective drive command, it
is zero.

## 3.5 Train-Facing Equipment Boundary

Doors, cabs, BTM, and ATP protection state are train-local equipment. Their
state may be included in a train snapshot, but their transport and protocol
handling remain outside the train model.

BTM payloads are opaque byte arrays. The train model can receive a BTM
delivery request for a cab, but does not interpret its contents; the ATP
adapter delivers the payload over the protocol.

## 3.6 Implementation Guide

Implement the train model in `domain/train.py` as the aggregate that owns one
train's mutable state. Keep calculations that do not require aggregate state
in `domain/physics.py`, and keep cab, door, BTM, and ATP-brake behavior in
`domain/equipment.py`. The simulation core calls only a
small aggregate API:

```text
apply_control(command)
step(dt)
get_snapshot()
reset()
```

`apply_control(command)` validates the command's train and cab identity,
updates requested control state, and returns a structured result. It does not
advance time or mutate position, speed, or acceleration. `step(dt)` is the
aggregate coordination point: it collects reference-free intents emitted by
equipment and resolves them centrally before integrating the physical state.
The intent contract is present, but the current resolver has no domain effects
yet. `get_snapshot()` returns a newly constructed immutable train snapshot; it
never exposes the aggregate or mutable equipment objects.

Represent configuration with frozen dataclasses and runtime state with private
mutable dataclasses. Validate all numeric configuration and control inputs at
the boundary: values must be finite, acceleration limits must be positive, and
normalized demands must be in the inclusive range `-1.0` through `1.0`.

### Physics Integration

`physics.py` should provide pure functions for resolving acceleration and
integrating forward-only motion. The aggregate supplies the prior physical
state, effective control state, configured limits, and `dt`; the functions
return the resulting physical state without side effects. Keep the stop-within-
a-step calculation in this module so every train type applies the same
zero-speed clamping rule.

The first implementation should use constant acceleration over a fixed step.
For a train with initial speed $v_0$, resolved acceleration $a$, and step
duration $dt$, calculate the unconstrained speed as:

$$
v_1 = v_0 + a \cdot dt
$$

When $v_1 \geq 0$, advance position using:

$$
x_1 = x_0 + \frac{v_0 + v_1}{2} \cdot dt
$$

When deceleration would make $v_1 < 0$, calculate the stopping duration
$t_{stop} = -v_0 / a$, advance only for $t_{stop}$, and return zero speed and
zero acceleration. This makes manual and wall-clock modes share identical
train movement behavior.

### Extensible Equipment

Model each train-facing equipment capability behind a narrow interface owned
by the train aggregate. An equipment component may keep private mutable state,
accept plain-value controls or deliveries, and create its own immutable
snapshot. It must not import adapters, access the simulation clock,
or modify train physical state directly.

```text
Equipment component
  key                       # equipment type identifier
      apply_control(...)        # receives the component's typed control object
      emit_intents()            # reference-free requests for train-level effects
  read_state()
  reset()
```

An intent identifies a source equipment key, a target equipment key or the
reserved `train` target, and a control object accepted directly by the target's
`apply_control()` method. Equipment never holds references to other equipment
and never applies an intent itself. The aggregate coordinates components in a
documented, stable order: apply accepted controls, collect and resolve intents,
advance train dynamics, then construct the snapshot. New equipment such as
vigilance, pantograph control, or passenger systems can be added by implementing
this interface and extending the aggregate's configuration and snapshot types.
Do not add protocol-specific behavior to an equipment component; adapters
translate protocol data into equipment commands and publish snapshot data.

Keep snapshot extensions backward-compatible: add an optional frozen nested
snapshot for new equipment rather than changing existing physical-state field
meanings. This lets the WebSocket and ATP adapters evolve independently while
the simulation core continues to treat each train as one aggregate.

---

# 4. ATP Protocol

## 4.1 ATP Interface

The ATP interface is a strict boundary.

```text
                    TRAIN
                       │
                       │
                   TCP / NDJSON
                       │
               ┌───────▼────────┐
               │ ATP Process    │
               └────────────────┘
```

ATP communicates directly with the simulator through the protocol. ATP never receives Python/C++/Java objects.

ATP must never directly modify the train's physical state. For example:

```text
ATP
 │
 │ drive demand = -1.0
 ▼
Train Control
 │
 ▼
Decelerating Force
 │
 ▼
Train Physics
 │
 ▼
Speed / acceleration
```

Not:

```text
ATP
 │
 └── train.speed = 0
```

This maintains a clean separation between control and physical simulation.

## 4.2 Protocol Specification

The wire protocol itself -- transport and framing, connection lifecycle and
reconnection, the full message catalog with field-level definitions
(`TRAIN_STATE`, `ATP_COMMAND`, `ERROR`), validation and error codes,
configuration and observability -- is specified in
**[`atp-api.md`](atp-api.md)**, which mirrors the implementation in
`src/a_train/adapters/atp/`.

In one paragraph: each ATP process is a TCP server serving exactly one cab;
the simulator dials every configured endpoint, streams `train_state`
      observations with the current per-cab BTM entry embedded in the
snapshot, and accepts `atp_command` action requests from ATP, mapping them
onto the same transport-neutral core commands the Web API submits. Invalid
input is answered with `ERROR` without affecting the simulation. The interface
boundary that makes this safe is §4.1: ATP requests, physics decides.

---

# 5. Web & API

## 5.1 Web Architecture

The web UI should communicate only with the simulator.

```text
Browser
   │
   ├── HTTP → REST API
   │
   └── WebSocket → live state
                       │
                       ▼
                  Simulator Core
```

The browser does **not** communicate directly with ATP.

## 5.2 REST API

Example endpoints:

```text
GET    /api/status
GET    /api/trains
GET    /api/trains/{id}

POST   /api/simulation/start
POST   /api/simulation/pause
POST   /api/simulation/step
POST   /api/simulation/reset
POST   /api/simulation/time-mode

POST   /api/signals/{id}
POST   /api/trains/{id}/commands
POST   /api/trains/{id}/equipment/{key}
```

`POST /api/trains/{id}/equipment/{key}` is the generic equipment boundary:
the JSON body is mapped to a transport-neutral equipment command and applied
immediately by the core. See `web-api.md` for the per-equipment fields.

`POST /api/simulation/start` invokes the core's idempotent `run()` command.
`POST /api/simulation/time-mode` accepts a mode and, for `SCALED` mode, a
positive `time_multiplier`. `POST /api/simulation/step` accepts a non-negative
`delta` and is valid only in `MANUAL` mode. The API submits these commands to
the core; it does not modify simulation objects directly.

Example:

```http
POST /api/trains/TRAIN001/equipment/btm
```

```json
{
  "cab_id": 1,
  "data": "ASOk/wCBcg=="
}
```

## 5.3 WebSocket

WebSocket provides live state updates.

```text
Simulator ───────────────> Browser

{
  "type": "state",
  "time": 123.45,
  "trains": [...]
}
```

The UI can update train position, speed, signals, BTM, and faults without polling continuously.

The state message includes `simulation_state`, `simulation_time`, `time_mode`,
`time_multiplier`, and world state.

---

# 6. Testing & Automation

## 6.1 Automation

Automation must not depend on the web UI.

The preferred hierarchy is:

```text
              Simulator Core
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
      CLI          REST       Python API
       │            │            │
       └────────────┼────────────┘
                    ▼
                 Testing
```

A test should be able to do:

```text
Select MANUAL mode
      ↓
Advance time with step(delta)
      ↓
Submit signal or train command
      ↓
Check ATP output
      ↓
PASS / FAIL
```

without opening a browser.

## 6.2 Recording

**Not implemented** (removed from Phase 3.2 scope; see TODO deferred list).
The original intent stands: record important Train ↔ ATP traffic as NDJSON
for debugging and audit, one line per message carrying simulation time,
direction, message type, and train/cab identity. Note the key collision to
resolve on re-addition: `train_state` carries its own `direction` field.

---

# 7. Implementation

## 7.1 Technology Stack

**Backend** — Python:

```text
Python
├── FastAPI
├── asyncio
├── SQLite
└── pytest
```

**Frontend** — TypeScript:

```text
TypeScript
HTML
CSS
SVG
WebSocket
```

No React is required initially. The UI can be a simple static web application.

## 7.2 Repository Structure

```text
train-simulator/
│
├── src/a_train/
│   ├── __init__.py                 # Public package version and exports only.
│   ├── __main__.py                 # `python -m a_train` command-line entry point.
│   ├── config.py                   # ATP endpoint configuration for `run` (atp-api.md §6, Phase 3.1).
│   ├── bootstrap.py                # Creates the core and adapters; owns process startup and shutdown.
│   │
│   ├── simulation/                 # Simulation-time orchestration; no HTTP or TCP handling.
│   │   ├── __init__.py             # Public simulation-core API.
│   │   ├── clock.py                # Fixed-step accumulator and monotonic wall-clock conversion.
│   │   ├── commands.py             # Frozen command types and command-result types.
│   │   ├── core.py                 # Single `run_loop()` owner of mutable world state.
│   │   └── snapshots.py            # Frozen, transport-neutral simulation snapshot types.
│   │
│   ├── domain/                     # Train-world rules; independent of time loop and external transports.
│   │   ├── __init__.py             # Public domain types.
│   │   ├── train.py                # Train aggregate and stable per-step update entry point.
│   │   ├── physics.py              # Drive force, acceleration, speed, and position calculations.
│   │   ├── equipment.py            # Doors, cabs, BTM, and ATP-brake equipment behavior.
│   │   └── signals.py              # Linear-track signal state and signal-aspect rules.
│   │
│   ├── adapters/                   # I/O boundaries that translate external data into core commands.
│   │   ├── __init__.py             # Adapter package marker; no runtime behavior.
│   │   ├── api/
│   │   │   ├── __init__.py         # FastAPI application factory export.
│   │   │   ├── app.py              # FastAPI construction, lifespan, and dependency wiring.
│   │   │   ├── routes.py           # REST request handlers that submit core commands.
│   │   │   ├── websocket.py        # Snapshot-to-browser WebSocket publisher task.
│   │   │   └── schemas.py          # HTTP request and response validation models.
│   │   └── atp/
│   │       ├── __init__.py         # ATP adapter public exports.
│   │       ├── protocol.py         # NDJSON encode/decode and protocol-message validation.
│   │       ├── client.py           # One reconnecting TCP client for one external ATP process.
│   │       └── manager.py          # Creates clients and bridges snapshots and ATP commands.
│
├── web/                            # Static browser client; contains no simulation rules.
│   ├── index.html                  # Application document and static asset entry point.
│   ├── app.ts                      # UI state, REST controls, and WebSocket consumption.
│   └── style.css                   # Browser presentation styles.
│
├── tests/                          # End-to-end integration tests only.
│   ├── conftest.py                 # Starts the complete application and test ATP server.
│   ├── support/
│   │   ├── app.py                  # Application lifecycle and REST/WebSocket test client helpers.
│   │   └── atp_server.py           # Controllable production-protocol TCP server for test ATP peers.
│   ├── test_simulation_control.py  # Run, pause, reset, manual stepping, and time-mode workflows.
│   ├── test_train_movement.py      # Train physics and signal behavior observed through the public API.
│   ├── test_atp_integration.py     # Train-state publication and ATP-command application over TCP/NDJSON.
│   ├── test_btm_integration.py     # End-to-end BTM delivery to an ATP peer.
│   └── test_websocket_state.py     # Public state snapshots delivered through WebSocket.
│
├── docs/
│   ├── architectural.md            # System architecture and module contracts.
│   ├── atp-api.md                  # ATP TCP/NDJSON wire protocol contract.
│   ├── web-api.md                  # Implemented HTTP/WebSocket API contract.
│   └── TODO.md                     # Deferred implementation work.
│
├── pyproject.toml                  # Build metadata, dependencies, tooling, and test configuration.
└── README.md                       # Installation, running, and development instructions.
```

Dependency direction is strictly inward:

```text
adapters -------------> simulation -> domain
bootstrap -----------> adapters, simulation
web ------------------> REST and WebSocket adapters
```

`domain` never imports `simulation`, `adapters`, or `web`.
`simulation` never imports `adapters` or `web`. `bootstrap.py` is the only
production module allowed to assemble these components and start background
tasks.

## 7.3 Design Principles

1. **Headless first** — The simulator must work without the browser.
2. **ATP is external** — ATP is an independent process and communicates only through the protocol.
3. **Protocol is a contract** — The protocol is documented independently of Python classes.
4. **BTM is opaque** — The simulator transports BTM bytes but does not interpret ATP-specific BTM semantics.
5. **Physics owns physical state** — ATP requests actions; the train model determines the physical result.
6. **GUI is replaceable** — The web UI is just another client.
7. **Everything should be automatable** — Every GUI operation should have an equivalent API/command.
8. **Deterministic simulation** — Simulation time should be independent from wall-clock time wherever practical.
9. **Record everything important** — Train ↔ ATP communication should be easily logged.
