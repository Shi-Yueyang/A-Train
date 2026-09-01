# `a_train.simulation` — Implementation Design

This document explains how the simulation module is *implemented* today (Phases
0–2 complete, per `docs/TODO.md`) and how its parts fit together. The
authoritative contract is `docs/architectural.md` §2; this file describes the
code that realizes it.

## 1. Responsibility and boundaries

`simulation` is the simulation-time orchestration layer. It owns simulation
time, the fixed-step clock, the command queue, and the ordered update of the
simulated world — including the train aggregates it holds. It does **not**
contain HTTP, WebSocket, TCP, or YAML handling; those live in `adapters/`.

Dependency direction (§7.2):

```
adapters -> simulation -> domain
```

`simulation` never imports `adapters` or `web`. Only `bootstrap.py` is allowed
to instantiate `SimulationCore` and start `run_loop()`. The train world
(`domain.train`, `domain.physics`, `domain.equipment`) is pure domain code that
the core drives; `domain` never imports `simulation`. Train configuration is
supplied as frozen `TrainConfig` dataclasses at construction time.

### Module map

| File           | Role                                                                   | Phase        |
| -------------- | --------------------------------------------------------------------- | ------------ |
| `commands.py`  | Frozen, keyword-only command dataclasses + `CommandResult`.           | 0–1          |
| `snapshots.py` | `SimulationState`/`TimeMode` enums, `SimulationSnapshot`;<br>re-exports the domain-owned `TrainSnapshot` and nested equipment snapshots. | 0, 2 |
| `core.py`      | `SimulationCore`: `run_loop`, clock, dispatch, train ownership,<br>fixed-step update, snapshots, bounded subscriber queues. | 0–2 |
| `clock.py`     | Fixed-step accumulator (planned; currently folded into `core.py`).    | 1 → refactor |

> The clock logic currently lives inline in `core.py`; when it grows it moves
> into `clock.py` as a `Clock` class the core delegates to, with public
> behavior unchanged.

## 2. The single execution context

The central invariant (§2.5, §2.6):

> All world-state mutation happens on one execution context —
> `SimulationCore.run_loop()`. Everything else enqueues commands.

`bootstrap.py` starts exactly one `run_loop()` task and gives every adapter the
same `asyncio.Queue[Command]`. Adapters call `core.run()` / `core.step()` /
`core.submit_command(...)`; those are thin async wrappers that **submit** a
command and `await` its result. They never touch world state directly.

So the call graph for a train-control request is:

```
REST route  ->  core.submit_command(TrainControlCommand)  ->  command_queue
                                                                        |
                                                                        v
                                                        run_loop() _handle_command
                                                                        |
                                                        _dispatch -> _apply_train_control
                                                                        |
                                                        train.apply_control(payload)
                                                                        |
                                                        resolve result Future
                                                                        v
                                                     route resumes, returns snapshot
```

`run_loop()` is the only consumer of `command_queue` and the only writer of the
core's mutable fields and the train aggregates it owns.

## 3. Commands and results

Commands are `@dataclass(frozen=True, kw_only=True)` (`commands.py`). They are
plain data — no behaviour. Keyword-only avoids the dataclass default-ordering
problem so subclasses can add required payload fields (e.g. `StepCommand.delta`).

Each command carries a `sequence: int = 0`. The sequence is **stamped at enqueue
time**, not by the caller:

```python
async def _submit(self, template: Command) -> CommandResult:
    sequence = self._next_sequence()
    command = dataclasses.replace(template, sequence=sequence)
    future = asyncio.get_running_loop().create_future()
    self._results[sequence] = future
    self._command_queue.put_nowait(command)
    return await future
```

`dataclasses.replace` produces a new frozen instance with the assigned sequence.
Processing commands in `sequence` order at each fixed-step boundary makes the
update deterministic and reproducible (§2.6).

Results are returned, never raised into the loop (§2.6: "do not let adapter
exceptions enter `run_loop()`"). `_dispatch` wraps every handler in
`try/except` and returns a `CommandResult(ok=False, error=...)`. The REST layer
maps `ok=False` to HTTP 400.

The `sequence -> Future` map (`self._results`) decouples the result channel from
the command payload, keeping commands pure data.

## 4. State machine

`SimulationState` is `{STOPPED, RUNNING, PAUSED}` (§2.2). Transitions are
idempotent:

- `_apply_run`: `STOPPED|PAUSED -> RUNNING`; no-op if already `RUNNING`.
- `_apply_pause`: `RUNNING -> PAUSED`; no-op if already `PAUSED|STOPPED`.
- `_apply_reset`: any `-> STOPPED`, time `0.0`, accumulator cleared, world
  buffer cleared, and every owned train restored to its configured state via
  `train.reset()`.

Every state transition resets `_monotonic_ref = None` so that time spent in the
previous state (especially paused) is never carried into the next running
interval.

## 5. Clock and time modes

`TimeMode` is `{REALTIME, SCALED, MANUAL}` (§2.3).

- `REALTIME`: `simulation_delta = wall_delta`
- `SCALED`:    `simulation_delta = wall_delta * time_multiplier`
- `MANUAL`:    time advances only via explicit `step(delta)`

Time is measured with `time.monotonic()` — never calendar time. A fixed step
(default `0.05 s`) subdivides every delta. The accumulator holds the fractional
remainder for the next update, so a stalled process cannot produce one giant
physics jump (§2.3).

### Wall-clock tick (`_tick_wall_clock`)

When `RUNNING` and not `MANUAL`, `run_loop` drives the wall clock:

```python
now = time.monotonic()
if self._monotonic_ref is None:  # just (re)started / mode-changed
    self._monotonic_ref = now
    wall_delta = 0.0  # don't count time from before running
else:
    wall_delta = now - self._monotonic_ref
    self._monotonic_ref = now
self._accumulator += max(0.0, wall_delta) * multiplier
self._drain_fixed_steps()
# then wait for the next command OR the next step deadline:
wait = (fixed_step - accumulator) / multiplier
command = await _await_command(max(wait, _MIN_TICK_WAIT))
```

`_MIN_TICK_WAIT` (1 ms) prevents a tight spin when the next step is only a
float-epsilon away. `_await_command` re-checks the queue with `get_nowait()`
after a timeout to close the cancel race where a command arrives exactly as
the `wait_for` times out.

### Manual step

`step(delta)` is a control command handled immediately. It **reuses the same
accumulator and the same `_drain_fixed_steps` path** as wall-clock mode (§2.6),
so manual and real-time execution are one code path:

```python
self._accumulator += delta
self._drain_fixed_steps()
```

Only whole fixed steps advance `simulation_time`; the remainder is retained.
`step(0.10)` with `fixed_step=0.05` advances time by `0.10` (two steps); a
non-multiple like `0.12` advances `0.10` and keeps `0.02` for the next step.

## 6. The fixed-step update cycle (`_run_fixed_step`)

Per §2.5, each nominal fixed step performs:

1. **Apply queued world commands** in `sequence` order — drain
   `_world_buffer`, calling `_apply_world_command` (currently only
   `AtpStateCommand`, a no-op until Phase 3) and resolving each command's
   result Future.
2. **Advance simulation time** to the step end (`simulation_time += duration`).
3. **Update each train's equipment and physics in stable train-ID order** —
   `for train_id in self._train_ids_sorted: self._trains[train_id].step(duration)`.
4. **Produce a snapshot** — `_build_snapshot()` + `_publish_snapshot()`.

The train's own `step(dt)` runs the aggregate's stable order (apply accepted
controls → update equipment → resolve dynamics → integrate forward-only
motion), so the core only steps each aggregate once per fixed step.

## 7. Control vs. world commands

`_handle_command` splits commands into two families:

- **Control** (`Run`/`Pause`/`Reset`/`SetTimeMode`/`Step`/`TrainControl`):
  applied **immediately** on receipt (not deferred to a step boundary), then a
  fresh snapshot is built and the command's Future is resolved. This is what
  makes `await core.pause()` — and `await core.submit_command(TrainControlCommand)`
  — return a current snapshot right away.
- **World** (`AtpState`): **buffered** into `_world_buffer` and applied at the
  next fixed-step boundary (step 1 above). Its Future is resolved only after
  application. (Phase 3 fills in ATP-state application.)

### Why `TrainControlCommand` is immediate, not buffered

`docs/architectural.md` §2.6 says ATP and train-control commands are buffered
to the next fixed-step boundary. In `MANUAL` mode (the deterministic test mode,
§6.1) the "next fixed step" only happens when the caller invokes `step(delta)`.
If the REST handler awaited a buffered train-control command, the request
would block until a step occurs — and the step can only be issued after the
control request returns. That is a deadlock.

Train-control requests only change *control state* (the signed drive/door
demand); they never advance time. So they are applied immediately at the
aggregate boundary (`train.apply_control`), the way §3.6 describes
`apply_control` ("updates requested control state"). Physics still advances
only at the fixed-step boundary. This keeps MANUAL stepping deadlock-free and
deterministic while preserving the invariant that adapters never mutate world
state directly — every change goes through `run_loop()`.

`AtpStateCommand` remains a buffered world command; Phase 3 converts its bit
string into train control at the fixed step, where wall-clock modes make
buffering safe.

## 8. Train world ownership

`SimulationCore` owns the train aggregates, built at construction time from
frozen `TrainConfig` dataclasses and rebuilt state on `reset`:

```python
self._trains: dict[str, Train] = {cfg.train_id: Train(cfg) for cfg in train_configs}
self._train_ids_sorted: tuple[str, ...] = tuple(sorted(self._trains))
```

- **Routing.** `_apply_train_control(command)` looks up the train by
  `train_id` and calls `train.apply_control(command.payload)`. A missing train
  or an unconfigured cab or an out-of-range demand returns
  `CommandResult(ok=False)`, which the REST layer maps to HTTP 400; because
  `apply_control` validates before mutating, invalid input leaves state
  unchanged.
- **Reset.** `_apply_reset` calls `train.reset()` on every train, restoring
  configured physical state and clearing control/equipment runtime state.
- **Snapshots.** `_build_snapshot` collects `train.get_snapshot()` for each
  train in stable train-ID order into `SimulationSnapshot.trains`. The
  `TrainSnapshot` type lives in `domain.snapshots` (the domain owns it because
  the aggregate constructs it); `simulation.snapshots` re-exports it so the
  simulation public API keeps its import path.

The core never exposes a `Train` object; only frozen snapshots leave the core.

## 9. Snapshots and subscribers

`SimulationSnapshot` (with nested `TrainSnapshot` and equipment snapshots) is a
frozen dataclass containing only scalars, immutable tuples, and frozen nested
dataclasses (§2.6: never expose a mutable object to a client).

- `get_snapshot()` returns `self._latest_snapshot` — an immutable reference.
  Reading it is safe from any context because the object cannot be mutated;
  `run_loop` only ever *replaces* the reference.
- `_latest_snapshot` is refreshed after every fixed step **and** after every
  control transition (including train-control).
- `_publish_snapshot` pushes the latest snapshot to each subscriber queue
  (`self._snapshot_subscribers`). Bounded queues drop their oldest entry when
  full, so a slow WebSocket/ATP publisher can never block physics (§2.6).
- `subscribe(maxsize=64)` / `unsubscribe(queue)` let the WebSocket adapter
  register and release a bounded queue. `subscribe` seeds the queue with the
  current snapshot so a client immediately receives the latest state on
  connect, then one snapshot after every fixed step and control transition.

## 10. Key invariants

1. **One writer.** Only `run_loop()` mutates core fields and train aggregates;
   adapters enqueue.
2. **Pure commands.** Commands carry no behaviour; sequences are stamped at
   enqueue.
3. **Errors are values.** Invalid input → `CommandResult(ok=False)`, never an
   exception in the loop.
4. **No time leaks.** `monotonic_ref` resets on pause/stop/mode-change.
5. **One clock path.** Wall-clock and manual stepping share the accumulator and
   `_drain_fixed_steps`.
6. **Immutable snapshots.** Clients receive frozen, read-only views; snapshots
   cannot be used to mutate subsequent simulator state.
7. **Stable train order.** Trains update in sorted train-ID order every step,
   so multi-train snapshots are repeatable for the same initial state and
   command sequence.

## 11. Phase status

Aligned with `docs/TODO.md`:

| Capability                                              | Status      |
| ------------------------------------------------------- | ----------- |
| Module layout, data types, lifecycle                    | ✅ Phase 0  |
| `run_loop`, state machine, clock, time modes            | ✅ Phase 1  |
| Train world: aggregate, physics, equipment, train       | ✅ Phase 2  |
| snapshots, bounded subscriber queues, WebSocket publisher |             |
| ATP TCP/NDJSON integration (client, protocol, manager)  | ⏳ Phase 3  |
| Web UI: REST train routes + static browser client       | ⏳ Phase 4  |
| Release readiness (determinism, cross-platform CI)      | ⏳ Phase 5  |
