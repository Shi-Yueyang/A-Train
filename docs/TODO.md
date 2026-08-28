# Train Simulator Implementation Plan

This plan follows the architecture in `architectural.md`. Complete phases in
order. A phase passes only when every listed criterion is demonstrated by the
integration suite or its stated manual acceptance check.

## Phase 0: Project Foundation

Create the Python package, dependency configuration, static web asset layout,
and development commands described in the repository structure.

### Foundation Work

* Create the `src/a_train` package and the module boundaries in Section 7.2.
* Configure Python, FastAPI, PyYAML, pytest, formatting, and test commands in
  `pyproject.toml`.
* Add `python -m a_train` as the application entry point.
* Add an application factory and lifecycle ownership in `bootstrap.py`.
* Add the integration-test application and ATP-server helpers.

### Foundation Passing Criteria

* `python -m a_train --help` exits successfully and documents the run and
  scenario-test commands.
* The application starts and stops cleanly without a loaded scenario.
* `pytest` discovers and runs the integration suite.
* The test fixture starts the real application and a controllable TCP test ATP
  server without using mocked production modules.

## Phase 1: Headless Simulation Core

Implement the deterministic, command-driven core without relying on the web
UI or a real ATP process.

### Core Work

* Implement immutable commands, snapshots, scenario schemas, and world state.
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

## Phase 2: Scenario Loading and Exact-Time Events

Implement YAML scenarios and their deterministic event execution model.

### Scenario Work

* Implement YAML parsing in `scenario/loader.py` and immutable types in
  `scenario/schema.py`.
* Implement load-time validation of scenario structure, event IDs, times, and
  registered event payloads.
* Implement the stable `(at, sequence, event)` event heap.
* Split nominal fixed steps at event times.
* Implement failure status and retry-on-run behavior for event handlers.

### Scenario Passing Criteria

* Loading a malformed scenario through the public API returns a validation
  error and leaves the previously loaded scenario unchanged.
* An event scheduled at `10.01 s` in a `0.05 s` step changes observable state
  at `10.01 s`, not `10.05 s`.
* Events at the same time execute in their source-file order.
* Repeated manual stepping never triggers a successful event more than once.
* A failed event pauses the simulation, exposes its ID and error in the public
  simulation status, and is retried before physics advances after `run`.

## Phase 3: Train World and Public State

Implement the initial linear-track train behavior, signals, equipment, and
transport-neutral snapshots.

### Train World Work

* Implement train state, traction, braking, acceleration, speed, and position.
* Implement cabs, door state, BTM equipment, digital I/O, and linear signals.
* Implement stable train-ID update order.
* Build immutable snapshots and bounded subscriber queues.

### Train World Passing Criteria

* A scenario with multiple trains produces stable, repeatable public snapshots
  using the same seed and command sequence.
* A train-control request submitted through REST changes movement only after
  the defined simulation boundary.
* A signal scenario event changes the observable signal state at its scheduled
  time.
* A slow WebSocket test client cannot prevent another client from receiving a
  later state snapshot or delay manual `step(delta)` completion.
* Snapshot responses cannot be used by a client to mutate subsequent simulator
  state.

## Phase 4: ATP TCP/NDJSON Integration

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
* An `ATP_STATE` message from the test server affects train braking through the
  train model; it never directly sets speed or position.
* A BTM scenario event sends its Base64 payload unchanged in a `BTM_RX`
  message.
* Disconnecting or sending malformed data from one ATP test server is reported
  without stopping the simulation or another cab's connection.
* The recorded NDJSON contains simulation time, direction, message type, and
  train/cab identity for every recorded protocol message.

## Phase 5: Web Control and Live State

Implement the browser-facing controls and state display as a thin client of
the existing APIs.

### Web Integration Work

* Implement REST routes and validation models for scenario loading, run,
  pause, reset, time mode, step, signals, and train controls.
* Implement WebSocket snapshot publishing.
* Implement the static web client with simulation controls and live state.
* Serve the static client from the FastAPI application.

### Web Integration Passing Criteria

* REST requests reject invalid mode, multiplier, and step values with a clear
  client error and leave simulation state unchanged.
* A WebSocket client receives the initial snapshot followed by snapshots after
  a control-state transition and after a manual step.
* The browser can load a scenario, choose `MANUAL` or `SCALED` mode, run,
  pause, reset, and advance time without accessing simulator internals.
* Browser-displayed time, state, train position, and speed match the latest
  WebSocket snapshot.
* The entire Phase 5 integration suite runs headlessly without opening a real
  browser window.

## Phase 6: End-to-End Scenarios and Release Readiness

Turn the system into a reproducible simulator that can run documented
scenarios from the command line and in continuous integration.

### Release Work

* Add `basic.yaml`, `overspeed.yaml`, and `btm.yaml` scenarios.
* Implement the command-line scenario runner in `MANUAL` mode.
* Add configuration and operational documentation for ATP endpoints and logs.
* Run the complete integration suite on Linux and Windows.

### Release Passing Criteria

* `python -m a_train test scenarios/basic.yaml`, `overspeed.yaml`, and
  `btm.yaml` each exit with code `0` when their assertions pass.
* Intentionally failing a scenario assertion exits non-zero and reports the
  scenario name, event ID, simulation time, and expected versus actual value.
* Re-running each scenario with the same seed produces identical recorded
  state and protocol traffic after excluding connection-establishment timing.
* The complete integration suite passes on Linux and Windows.
* README instructions allow a new developer to install dependencies, start the
  simulator, connect a test ATP process, and run all scenarios.

## Deferred Until a New Phase

* More than one-dimensional track topology.
* High-fidelity 3D graphics.
* Multiplayer or distributed simulation.
* ATP implementation, fail-safe behavior, or safety certification.
