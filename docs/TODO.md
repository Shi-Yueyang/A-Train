# Train Simulator Implementation Plan

This plan follows the architecture in `architectural.md`. Complete phases in
order. A phase passes only when every listed criterion is demonstrated by the
integration suite or its stated manual acceptance check.

## Phase 0: Project Foundation

Create the Python package, dependency configuration, static web asset layout,
and development commands described in the repository structure.

### Foundation Work

* Create the `src/a_train` package and the module boundaries in Section 7.2.
* Configure Python, FastAPI, pytest, formatting, and test commands in
  `pyproject.toml`.
* Add `python -m a_train` as the application entry point.
* Add an application factory and lifecycle ownership in `bootstrap.py`.
* Add the integration-test application and ATP-server helpers.

### Foundation Passing Criteria

* `python -m a_train --help` exits successfully and documents the run command.
* The application starts and stops cleanly.
* `pytest` discovers and runs the integration suite.
* The test fixture starts the real application and a controllable TCP test ATP
  server without using mocked production modules.

## Phase 1: Headless Simulation Core

Implement the deterministic, command-driven core without relying on the web
UI or a real ATP process.

### Core Work

* Implement immutable commands, snapshots, and world state.
* Implement the single `asyncio` `SimulationCore.run_loop()` task.
* Implement `STOPPED`, `RUNNING`, and `PAUSED` transitions.
* Implement `REALTIME`, `SCALED`, and `MANUAL` time modes.
* Implement the fixed-step accumulator and immediate control-command handling.

### Core Passing Criteria

* An integration test starts the complete application, calls the public REST
  API to select `MANUAL` mode, advances simulation time, and observes the
  expected time and state through the REST API.
* A paused simulation does not advance when real wall-clock time passes.
* A `SCALED` multiplier changes simulated elapsed time by the configured
  factor when the core is running.
* `run`, `pause`, and `reset` are idempotent when invoked through public API
  endpoints.
* Reset restores the initial world state, simulation time `0.0`, and
  `STOPPED` state.

## Phase 2: Train World and Public State

Implement the initial forward-only train model, its train-facing equipment,
and transport-neutral snapshots.

### Train World Work

* Implement the `Train` aggregate API: `apply_control`, `step`,
  `get_snapshot`, and `reset`.
* Define frozen per-train configuration and private mutable physical and
  control state.
* Validate train configuration, cab identity, and the normalized signed
  drive demand at the aggregate boundary.
* Implement pure fixed-step physics for drive force, zero-speed clamping,
  and forward-only position integration.
* Implement cab, door, BTM equipment, and digital I/O as train-facing
  components with plain-value control calls, `read_state`, and `reset`.
* Define the stable aggregate update order: apply accepted controls, resolve
  dynamics, then construct a snapshot.
* Implement stable train-ID update order and immutable train snapshots with
  optional nested equipment snapshots.
* Build bounded subscriber queues for simulation snapshots.

### Train World Passing Criteria

* Multiple trains produce stable, repeatable public snapshots using the same
  initial state and command sequence.
* A valid train-control request submitted through REST changes train control
  state only at the defined simulation boundary; invalid train, cab, or demand
  input returns a clear error and leaves state unchanged.
* At each fixed step, positive drive demand scales the traction limit and is
  gated by door-closed state; negative drive demand scales the deceleration
  limit.
* Deceleration that would stop a train during a step leaves its speed and applied
  acceleration at zero and never decreases its position.
* A train reset restores its configured physical state and clears control and
  equipment runtime state.
* An equipment component can add an optional immutable nested snapshot without
  changing existing physical snapshot fields or requiring adapter changes.
* A slow WebSocket test client cannot prevent another client from receiving a
  later state snapshot or delay manual `step(delta)` completion.
* Snapshot responses cannot be used by a client to mutate subsequent simulator
  state.

## Phase 2.5: Manual Web Demo

Provide a small browser-based demo for a human to verify the completed Phase 1
and Phase 2 behavior against the public API. The page subscribes to the live
`/ws` snapshot stream so state updates continuously without polling, and
submits commands through the REST API. This is a manual verification tool, not
the complete browser client planned for Phase 4.

### Demo Work

* Serve a static browser page from the FastAPI application.
* Display the current simulation state, time mode, time multiplier, simulation
  time, and each train's position, speed, acceleration, and control state.
* Provide controls to run, pause, reset, select `MANUAL` or `SCALED` mode, set
  a time multiplier, and advance a configurable manual step.
* Provide controls for the signed drive demand and for door state for a
  selected train and cab.
* Subscribe to the `/ws` snapshot stream on page load and render every received
  snapshot. Commands are submitted through the REST API; the resulting snapshot
  is delivered back over the WebSocket, so the page never polls.
* Reconnect to `/ws` automatically if the socket drops, and indicate the
  connection status in the page.
* Surface rejected commands and validation failures in the page without
  changing the displayed simulator state.

### Demo Acceptance Check

* A human can start the simulator, open the served page, and observe the
  initial `STOPPED` state and simulation time `0.0`.
* In `MANUAL` mode, a human can apply a positive drive demand, advance time,
  and observe increasing position and speed; applying a negative drive demand
  then visibly reduces speed without allowing reverse motion.
* Run, pause, reset, time-mode changes, door commands, and invalid control
  inputs produce the same observable state or error result as their REST API
  responses.
* In `REALTIME` or `SCALED` mode, a human can start the simulation and watch
  simulation time and train state advance continuously without any manual
  refresh.
* Reset visibly restores the configured initial state and clears control and
  equipment runtime state.

## Phase 3: ATP TCP/NDJSON Integration

Implement the external ATP boundary and connect it to the running simulation.

### ATP Integration Work

* Implement protocol message validation and NDJSON framing.
* Implement one reconnecting TCP client per configured train cab.
* Send `HELLO`, handle `HELLO_ACK`, and publish `TRAIN_STATE` and `BTM_RX`.
* Convert accepted `ATP_STATE` messages into queued core commands.
* Record train-to-ATP and ATP-to-train traffic as NDJSON.

### ATP Integration Passing Criteria

* The application connects to the integration test ATP server and completes
  the `HELLO` / `HELLO_ACK` exchange for each configured cab.
* A manual simulation step produces a correctly framed `TRAIN_STATE` message
  with the matching train and cab identifiers.
* An `ATP_STATE` message from the test server affects train dynamics through the
  train model; it never directly sets speed or position.
* Disconnecting or sending malformed data from one ATP test server is reported
  without stopping the simulation or another cab's connection.
* The recorded NDJSON contains simulation time, direction, message type, and
  train/cab identity for every recorded protocol message.

## Phase 4: Web Control and Live State

Implement the browser-facing controls and state display as a thin client of
the existing APIs.

### Web Integration Work

* Implement REST routes and validation models for run, pause, reset, time
  mode, step, signals, and train controls.
* Implement WebSocket snapshot publishing.
* Implement the static web client with simulation controls and live state.
* Serve the static client from the FastAPI application.

### Web Integration Passing Criteria

* REST requests reject invalid mode, multiplier, and step values with a clear
  client error and leave simulation state unchanged.
* A WebSocket client receives the initial snapshot followed by snapshots after
  a control-state transition and after a manual step.
* The browser can choose `MANUAL` or `SCALED` mode, run, pause, reset, and
  advance time without accessing simulator internals.
* Browser-displayed time, state, train position, and speed match the latest
  WebSocket snapshot.
* The entire Phase 4 integration suite runs headlessly without opening a real
  browser window.

## Phase 5: Release Readiness

Turn the system into a reproducible simulator that runs reliably in continuous
integration.

### Release Work

* Add configuration and operational documentation for ATP endpoints and logs.
* Run the complete integration suite on Linux and Windows.

### Release Passing Criteria

* Re-running the same initial state and command sequence produces identical
  recorded state and protocol traffic after excluding connection-establishment
  timing.
* The complete integration suite passes on Linux and Windows.
* README instructions allow a new developer to install dependencies, start the
  simulator, and connect a test ATP process.

## Deferred Until a New Phase

* More than one-dimensional track topology.
* High-fidelity 3D graphics.
* Multiplayer or distributed simulation.
* ATP implementation, fail-safe behavior, or safety certification.
