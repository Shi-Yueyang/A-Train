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
* Implement cab, door, and BTM equipment as train-facing
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

## Phase 3.1: ATP Communication Channel

Establish the fully configured, persistent TCP transport to the external ATP
processes: the connection is set up end to end and stays up. There is no
application-level handshake — the channel carries content the moment TCP
opens (one ATP server per cab makes the endpoint mapping the identity);
message semantics, content publishing, and keepalive remain in Phase 3.2.

### ATP Channel Work

* Configure ATP endpoints (train ID, cab ID, host, port) through the `run`
  command (CLI flags and/or a config file) so a production server connects to
  real ATP processes; no endpoints configured means the simulator runs ATP
  with zero connections.
* Implement the client connection-state machine: connect, retry, and reconnect
  per docs/atp-api.md §1.
* Implement one reconnecting TCP client per configured train cab.
* Hold each established connection open indefinitely: the reader consumes
  incoming framed lines without content-level interpretation, and the writer
  accepts framed bytes; malformed-message `ERROR` semantics and application
  messages arrive in Phase 3.2.
* Report each cab's connection state (e.g. `CONNECTING` / `HANDSHAKING` /
  `READY` / `DISCONNECTED`) in the log so an operator can verify the channel
  without a test server.

### ATP Channel Passing Criteria

* The application opens a TCP connection for each configured ATP endpoint and
  is `READY` as soon as TCP opens; the endpoints come from the `run` command
  configuration, exercised in tests through the same wiring the production
  server uses.
* A `READY` connection with no further traffic stays open, and NDJSON lines
  sent in either direction are consumed or written intact without dropping
  the session.
* A test server that is down at startup is retried and the channel reaches
  `READY` once it appears, without restarting the simulator.
* Dropping one ATP test server connection is reported without stopping the
  simulation or another cab's connection, and that cab's connection
  re-establishes on its own.

## Phase 3.2: ATP Protocol and Content Publishing

Speak the NDJSON protocol over the established channel and send the real
content.

### ATP Protocol and Content Work

* Implement protocol message validation and NDJSON framing.
* Reject or report malformed or unexpected messages with `ERROR` semantics.
* Publish `TRAIN_STATE` for each cab from the core's snapshots after each
  nominal fixed step and control-state transition.
* Deliver BTM payload data as `BTM_RX` messages when the train model accepts
  a BTM delivery.

### ATP Protocol and Content Passing Criteria

* Sending malformed data from one ATP test server is reported without
  stopping the simulation or another cab's connection.
* A manual simulation step produces a correctly framed `TRAIN_STATE` message
  with the matching train and cab identifiers.
* A BTM delivery through the equipment endpoint produces a `BTM_RX` message
  whose decoded `data` equals the delivered bytes.

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
  state and protocol traffic (observed through the test ATP server) after
  excluding connection-establishment timing.
* The complete integration suite passes on Linux and Windows.
* README instructions allow a new developer to install dependencies, start the
  simulator, and connect a test ATP process.

## Deferred Until a New Phase

* Train <-> ATP traffic recording (§6.2): removed with Phase 3.2; re-add as
  its own phase if audit trails are needed.
* More than one-dimensional track topology.
* High-fidelity 3D graphics.
* Multiplayer or distributed simulation.
* ATP implementation, fail-safe behavior, or safety certification.
