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
* Support digital ON/OFF I/O.
* Provide a simple **web UI**, not high-end 3D graphics.
* Run completely **headless** for automated testing.
* Support deterministic scenarios.
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
│  │  Command Queue + Scenario Event Scheduler                   │  │
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

**Simulator process** contains:

* train physics
* train equipment
* BTM simulation
* digital I/O
* signal model
* simulation state machine and fixed-step clock
* scenario loading and event scheduling
* serialized command processing and state snapshots
* ATP connections
* Web API

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

The simulator communicates with ATP via TCP/NDJSON. ATP processes are independent. A failure of one should not crash the simulator. No fail-safe behavior (e.g. automatic braking on ATP loss) is defined; see non-goals.

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

The Simulation Core owns simulation time, scenario event scheduling, and the
ordered update of the simulated world. It exposes commands to run, pause,
reset, and select the time mode. It does not contain HTTP, WebSocket, or TCP
connection handling; those adapters submit commands to the core and publish
state produced by it.

The core is the sole writer of train state. Browser controls and ATP input are
converted into commands. Control commands are applied immediately by the core
loop; world commands are applied at the next nominal fixed-step boundary.

## 2.2 Simulation State

```text
STOPPED --run--> RUNNING --pause--> PAUSED
   ^                 |                 |
   |------ reset ----+------ reset -----+
```

| State       | Meaning                                                  |
| ----------- | -------------------------------------------------------- |
| `STOPPED` | A scenario is loaded but no simulation time advances.    |
| `RUNNING` | The core advances simulation time and updates the world. |
| `PAUSED`  | World state and simulation time are frozen.              |

`run` is idempotent while already running, and `pause` is idempotent while
paused or stopped. `reset` restores the scenario's initial world state,
sets simulation time to zero, clears events that were already triggered, and
leaves the core stopped.

## 2.3 Clock and Time Modes

Simulation time is measured in seconds from the start of the loaded scenario:

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
greater than `1.0` accelerate the scenario, and values between `0` and `1.0`
slow it down. `MANUAL` mode makes automated tests independent of scheduling
latency and machine speed.

To prevent a stalled process or debugger break from producing an unexpectedly
large physics update, wall-clock updates are subdivided into fixed simulation
steps. The initial implementation uses a configurable fixed step of `0.05 s`.
Any remaining fractional duration is retained for the next update. The same
fixed-step path is used for real-time, scaled, and manual advancement.

## 2.4 Scenario Events

A scenario contains an initial world state and an ordered list of predefined
events. Each event has a unique identifier, a scheduled simulation time, and
a payload describing an action handled by the simulator.

```yaml
events:
  - id: signal-clear-001
    at: 30.0
    type: signal.set_aspect
    payload:
      signal_id: S12
      aspect: clear
  - id: balise-001
    at: 120.0
    type: btm.transmit
    payload:
      train_id: TRAIN001
      data: ASOk/wCBcg==
```

Event times are non-negative seconds relative to scenario start. Event payloads
are validated when the scenario is loaded, before it can enter `RUNNING`.
Malformed, unknown, duplicate, or unhandleable events cause scenario loading
to fail with a clear error; events are not silently skipped.

When a nominal fixed step reaches an event's scheduled time, the core splits
the step at that exact time: it updates physics up to the event time, triggers
the event, then updates the remaining duration. Events therefore take effect
at their declared simulation time, not at the next `0.05 s` boundary. Events
with the same `at` value execute in their order in the scenario file. The core
triggers each successful event exactly once and keeps a triggered-event record
for UI state, logs, and tests.

Scenarios are immutable while running. Loading or replacing a scenario is
allowed only while stopped, and it performs a reset.

## 2.5 Update Cycle

All simulation mutations occur on one simulation execution context. Network
readers and browser handlers may run concurrently, but enqueue commands rather
than changing world objects directly. This gives the core a deterministic order
of operations and avoids locking inside train physics.

The core processes control commands as soon as it receives them. These are
`run`, `pause`, `reset`, `set_time_mode`, scenario loading, and manual step
requests. A control command updates state and produces a snapshot immediately;
it does not wait for a physics step.

For every nominal fixed simulation step, the core performs the following
sequence. If an event occurs within the step, repeat steps 2 through 4 for the
duration before and after that event.

```text
1. Apply queued ATP and train-control commands in arrival order.
2. Advance simulation time to the next due event or the nominal-step end.
3. Update each train's equipment and physics for that duration in stable train-ID order.
4. Trigger all events due at the current simulation time.
5. Produce a state snapshot for the Web API and ATP Manager.
```

The snapshot is read-only and includes the current simulation state, mode,
time multiplier, simulation time, train state, and recently triggered events.
The core also produces a snapshot after every control-state transition and
terminal event-handler error. The Web API may publish snapshots at a lower
display rate, but it must not alter their contents or advance the simulation.

The initial core API is:

```text
load_scenario(scenario)
run()
pause()
reset()
set_time_mode(mode, time_multiplier=None)
step(delta)                 # valid only in MANUAL mode
submit_command(command)
get_snapshot()
```

`step(delta)` accepts a non-negative duration and returns only after all whole
fixed steps and due events have been processed. This is the primary interface
for deterministic headless tests.

## 2.6 Implementation Guide

Implement `SimulationCore` as the orchestration module for clock, commands,
events, train models, and snapshots. Put physics in `train.py` and protocol
parsing and transport in `atp.py`; neither module advances simulation time.

### Data Types and Ownership

Define `SimulationState` and `TimeMode` as `Enum` types. Define `Command` and
`SimulationSnapshot` as frozen `dataclass` types. Define the immutable
`Scenario` and `ScenarioEvent` types in `scenario/schema.py`. Keep mutable
world state private to `SimulationCore`.

`get_snapshot()` returns a newly constructed `SimulationSnapshot` containing
only scalar values, immutable tuples, and frozen nested snapshot dataclasses.
It never returns a train object, list, dictionary, or other mutable internal
collection.

Represent a scheduled event internally with its time and its source-file
sequence number:

```text
(event.at, event.sequence, event)
```

Store these tuples in a `heapq`. The sequence number makes equal-time event
order explicit and stable. `scenario/loader.py` validates event IDs, times,
and YAML structure. `SimulationCore.load_scenario()` validates each event type
and payload against the event-handler registry, then populates the heap during
`reset()`.

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
nominal fixed step and event split, then resolves the result. This keeps manual
and wall-clock execution on exactly the same code path.

### Commands, Events, and Snapshots

Define the command types `RunCommand`, `PauseCommand`, `ResetCommand`,
`SetTimeModeCommand`, `StepCommand`, `AtpStateCommand`, and
`TrainControlCommand`. Assign each command a monotonically increasing enqueue
sequence and process commands in that sequence at the beginning of every fixed
step. Return a structured command result for invalid input; do not let adapter
exceptions enter `run_loop()`.

Dispatch scenario events through one registry mapping every supported
`event.type` to a handler. Each handler validates its payload before mutating
the world. Record an event as triggered only after its handler succeeds. On
failure, retain the event and its error in simulation status, transition to
`PAUSED`, and publish an error snapshot. On the next `RunCommand`, retry that
event before processing a later event or advancing physics.

Build one snapshot at the end of each nominal fixed step, after every control
state transition, and after every terminal event-handler error. Publish it to
a bounded `asyncio.Queue[SimulationSnapshot]` owned by each subscriber task.
When a subscriber queue is full, discard its oldest snapshot before adding the
new one. WebSocket and ATP publisher tasks consume their own queues; they
never perform network I/O in `run_loop()`, so a slow client cannot delay
physics or event execution.

### Test Strategy

Use integration tests only. Each test starts the application through
`bootstrap.py` with its real `SimulationCore`, scenario loader, FastAPI
application, WebSocket publisher, and ATP adapter. Replace external ATP
processes with a controllable test TCP server that speaks the production
NDJSON protocol.

Each integration test loads a YAML scenario, selects `MANUAL` mode through the
REST API, advances time through `POST /api/simulation/step`, and verifies
observable behavior through REST responses, WebSocket snapshots, and the test
ATP server's received and sent protocol messages. Tests must not access core
or domain objects directly.

The integration suite must cover complete workflows for run/pause/reset,
real-time multiplier configuration, exact-time event scheduling, train
movement, BTM transmission, ATP brake commands, event-handler retry after a
failure, and scenario assertion events. Each scenario supplies a fixed random
seed so a failure can be reproduced exactly.

# 3. Train Model

A train contains: train dynamics and door state management

The train model is responsible for physical behavior. It should not know the internal implementation of ATP.

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
 │ emergency_brake = true
 ▼
Train Brake Controller
 │
 ▼
Brake Force
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

## 4.2 Protocol Overview

The protocol uses **TCP + NDJSON** (one JSON object per line).

Example:

```text
{"type":"train_state","train_id":"TRAIN001",...}\n
{"type":"train_state","train_id":"TRAIN001",...}\n
{"type":"btm_rx","data":"ASOk/wCBcg==",...}\n
```

TCP provides the transport. The newline provides application-level message framing.

**Connection model:** ATP acts as the TCP server. Simulator acts as the TCP client.

```text
Simulator                         ATP
    │                              │
    │──── TCP CONNECT ────────────>│
    │                              │
    │<──── HELLO_ACK ─────────────│
    │                              │
    │──── TRAIN_STATE ───────────>│
    │<──── ATP_STATE ─────────────│
```

Each ATP process has exactly one connection to the simulator.

**Connection sequence:**

```text
1. Simulator starts
2. External ATP process is running
3. Simulator connects to ATP TCP server
4. Simulator sends HELLO
5. ATP sends HELLO_ACK
6. Simulation begins
```

Example:

```json
{
  "type": "hello",
  "train_id": "TRAIN001",
  "cab_id": 1
}
```

ATP responds:

```json
{
  "type": "hello_ack",
  "accepted": true
}
```

## 4.3 Message Types

```text
HELLO
HELLO_ACK

TRAIN_STATE
ATP_STATE

BTM_RX

HEARTBEAT
HEARTBEAT_ACK

ERROR
```

Every message should contain:

```json
{
  "type": "..."
}
```

| Field    | Purpose      |
| -------- | ------------ |
| `type` | Message type |

For messages involving train/cab identification:

```json
{
  "train_id": "TRAIN001",
  "cab_id": 1
}
```

## 4.4 TRAIN_STATE

This is the primary cyclic message (Train → ATP).

Example:

```json
{
  "type": "train_state",

  "train_id": "TRAIN001",
  "cab_id": 1,

  "speed": 22.31,
  "acceleration": -0.15,
  "position": 15320.4,
  "direction": "forward",

  "train_to_atp": "1110"
}
```

Units:

```text
speed          m/s
acceleration   m/s²
position       m
```

Position is the distance along the single linear track from a fixed origin (see non-goals).

## 4.5 ATP_STATE

ATP sends its current outputs (ATP → Train).

Example:

```json
{
  "type": "atp_state",
  "atp_to_train": "100"
}
```

## 4.6 BTM Protocol

BTM data flows in one direction only: Train → ATP.

```text
TRAIN ────── BTM_RX ──────> ATP
```

The simulator is responsible for simulating the BTM equipment and sending BTM data to ATP. ATP is responsible for interpreting BTM data.

BTM data is treated as an **opaque byte array** by the simulator. The simulator should not interpret the BTM payload.

Example binary data:

```text
01 23 A4 FF 00 81 72
```

is encoded as Base64:

```text
ASOk/wCBcg==
```

The message becomes:

```json
{
  "type": "btm_rx",
  "data": "ASOk/wCBcg=="
}
```

## 4.7 Digital I/O

Digital signals are represented as bit strings.

Each bit position has a defined meaning. The bit string is ordered from bit 0 (leftmost) to bit N (rightmost).

The meaning of each bit is **configurable** per train type or ATP configuration. The tables below are examples only.

**Train → ATP** (`train_to_atp`):

Example configuration:

| Bit | Signal           |
| --- | ---------------- |
| 0   | cab_active       |
| 1   | doors_closed     |
| 2   | vigilance        |
| 3   | emergency_handle |

Example:

```json
"train_to_atp": "1100"
```

Means: cab_active=1, doors_closed=1, vigilance=0, emergency_handle=0.

**ATP → Train** (`atp_to_train`):

Example configuration:

| Bit | Signal          |
| --- | --------------- |
| 0   | warning         |
| 1   | service_brake   |
| 2   | emergency_brake |

Example:

```json
"atp_to_train": "100"
```

Means: warning=1, service_brake=0, emergency_brake=0.

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
POST   /api/simulation/scenario
POST   /api/simulation/time-mode

POST   /api/signals/{id}
POST   /api/trains/{id}/commands

POST   /api/btm/inject
```

`POST /api/simulation/start` invokes the core's idempotent `run()` command.
`POST /api/simulation/time-mode` accepts a mode and, for `SCALED` mode, a
positive `time_multiplier`. `POST /api/simulation/step` accepts a non-negative
`delta` and is valid only in `MANUAL` mode. The API submits these commands to
the core; it does not modify simulation objects directly.

Example:

```http
POST /api/btm/inject
```

```json
{
  "train_id": "TRAIN001",
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

The UI can update train position, speed, signals, BTM, ATP state, digital I/O, and faults without polling continuously.

The state message includes `simulation_state`, `simulation_time`, `time_mode`,
`time_multiplier`, and recently triggered events in addition to world state.

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
Load scenario
      ↓
Select MANUAL mode
      ↓
Advance time with step(delta)
      ↓
Submit signal or train command
      ↓
Advance to a scheduled event
      ↓
Check ATP output
      ↓
PASS / FAIL
```

without opening a browser.

## 6.2 Scenario Format

Scenarios are optional. The simulator can run in manual mode without a scenario.

Use YAML for human-readable scenarios.

Example:

```yaml
name: overspeed_test
random_seed: 12345

trains:
  - id: TRAIN001
    position: 1000
    speed: 20

events:
  - id: signal-danger-001
    at: 10.0
    type: signal.set_aspect
    payload:
      signal_id: S001
      aspect: RED

  - id: btm-telegram-001
    at: 20.0
    type: btm.transmit
    payload:
      train_id: TRAIN001
      cab_id: 1
      data: "ASOk/wCBcg=="

  - id: atp-output-assertion-001
    at: 30.0
    type: assert.atp_output
    payload:
      train_id: TRAIN001
      cab_id: 1
      atp_to_train: "100"
```

Scenario event types, including test assertions, are validated by registered
event handlers at load time. The scenario runner uses `MANUAL` mode and
`step(delta)` so its results do not depend on wall-clock timing.

Scenarios should be executable from the command line:

```bash
simulator test scenarios/overspeed.yaml
```

## 6.3 Recording

Record important Train ↔ ATP traffic as NDJSON for debugging and audit.

Example:

```text
{"time":10.00,"direction":"train_to_atp","type":"train_state",...}
{"time":10.02,"direction":"atp_to_train","type":"atp_state",...}
{"time":20.00,"direction":"train_to_atp","type":"btm_rx",...}
```

---

# 7. Implementation

## 7.1 Technology Stack

**Backend** — Python:

```text
Python
├── FastAPI
├── asyncio
├── PyYAML
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
│   ├── bootstrap.py                # Creates the core and adapters; owns process startup and shutdown.
│   │
│   ├── simulation/                 # Simulation-time orchestration; no HTTP, TCP, or YAML parsing.
│   │   ├── __init__.py             # Public simulation-core API.
│   │   ├── clock.py                # Fixed-step accumulator and monotonic wall-clock conversion.
│   │   ├── commands.py             # Frozen command types and command-result types.
│   │   ├── core.py                 # Single `run_loop()` owner of mutable world state.
│   │   ├── events.py               # Scheduled-event heap and event-handler registry.
│   │   └── snapshots.py            # Frozen, transport-neutral simulation snapshot types.
│   │
│   ├── domain/                     # Train-world rules; independent of time loop and external transports.
│   │   ├── __init__.py             # Public domain types.
│   │   ├── train.py                # Train aggregate and stable per-step update entry point.
│   │   ├── physics.py              # Traction, braking, acceleration, speed, and position calculations.
│   │   ├── equipment.py            # Doors, cabs, BTM equipment, and train-local I/O behavior.
│   │   ├── signals.py              # Linear-track signal state and signal-aspect rules.
│   │   └── io.py                   # Named digital-signal definitions and bit-string conversion.
│   │
│   ├── scenario/                   # Scenario file model and validation; does not run simulation steps.
│   │   ├── __init__.py             # Public scenario-loading API.
│   │   ├── schema.py               # Frozen scenario and event data models.
│   │   └── loader.py               # YAML parsing and load-time schema validation.
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
│   ├── test_scenario_events.py     # Scenario loading, scheduling, ordering, and failure workflows.
│   ├── test_train_movement.py      # Train physics and signal behavior observed through the public API.
│   ├── test_atp_integration.py     # Train-state publication and ATP-command application over TCP/NDJSON.
│   ├── test_btm_integration.py     # End-to-end BTM event delivery to an ATP peer.
│   └── test_websocket_state.py     # Public state snapshots delivered through WebSocket.
│
├── scenarios/                      # Version-controlled runnable scenario fixtures.
│   ├── basic.yaml                  # Minimal train movement scenario.
│   ├── overspeed.yaml              # ATP braking-response scenario.
│   └── btm.yaml                    # BTM transmission scenario.
│
├── docs/
│   ├── architectural.md            # System architecture and module contracts.
│   └── TODO.md                     # Deferred implementation work.
│
├── pyproject.toml                  # Build metadata, dependencies, tooling, and test configuration.
└── README.md                       # Installation, running, and development instructions.
```

Dependency direction is strictly inward:

```text
adapters -------------> simulation -> domain
simulation -----------> scenario schemas
bootstrap -----------> adapters, simulation, scenario
web ------------------> REST and WebSocket adapters
```

`domain` never imports `simulation`, `scenario`, `adapters`, or `web`.
`scenario` never imports `simulation`, `domain`, `adapters`, or `web`.
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
