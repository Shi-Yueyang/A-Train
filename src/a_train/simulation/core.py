"""Single ``run_loop()`` owner of mutable world state (§2.5, §2.6).

The core owns simulation time, the fixed-step clock, the command queue, and the
ordered update of the simulated world. Adapters (FastAPI, WebSocket, ATP) and
the core's own public API submit frozen ``Command`` objects to an
``asyncio.Queue``; only ``run_loop()`` consumes that queue or mutates world
state.

Control commands (``run``/``pause``/``reset``/``set_time_mode``/``step`` and
the train-control command) are applied immediately and produce a snapshot at
once. ``AtpStateCommand`` is buffered and applied in enqueue sequence at the
next nominal fixed-step boundary (Phase 3 fills in its application).
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import time
from collections.abc import Sequence

from ..domain.train import Train, TrainConfig
from .commands import (
    AtpStateCommand,
    Command,
    CommandResult,
    PauseCommand,
    ResetCommand,
    RunCommand,
    SetTimeModeCommand,
    StepCommand,
    TrainControlCommand,
)
from .snapshots import SimulationSnapshot, SimulationState, TimeMode

_EPSILON = 1e-9
_DEFAULT_FIXED_STEP = 0.05
# Smallest wall-clock wait in a wall-clock tick; prevents a tight spin when the
# next fixed step is only a float-epsilon away.
_MIN_TICK_WAIT = 0.001
_DEFAULT_SUBSCRIBER_MAXSIZE = 64

# Commands applied at the next fixed-step boundary rather than immediately.
_WORLD_COMMAND_TYPES: tuple[type[Command], ...] = (AtpStateCommand,)


class SimulationCore:
    """Sole writer of train state and sole consumer of the command queue."""

    def __init__(
        self,
        command_queue: asyncio.Queue[Command],
        snapshot_subscribers: list[asyncio.Queue[SimulationSnapshot]],
        *,
        train_configs: Sequence[TrainConfig] = (),
        fixed_step: float = _DEFAULT_FIXED_STEP,
    ) -> None:
        self._command_queue = command_queue
        self._snapshot_subscribers = snapshot_subscribers
        self._fixed_step = fixed_step

        self._state = SimulationState.STOPPED
        self._time_mode = TimeMode.MANUAL
        self._time_multiplier = 1.0
        self._simulation_time = 0.0
        self._accumulator = 0.0
        self._monotonic_ref: float | None = None

        self._sequence = 0
        self._results: dict[int, asyncio.Future[CommandResult]] = {}
        self._world_buffer: list[Command] = []

        self._trains: dict[str, Train] = {cfg.train_id: Train(cfg) for cfg in train_configs}
        self._train_ids_sorted: tuple[str, ...] = tuple(sorted(self._trains))

        self._latest_snapshot = self._build_snapshot()

    # -- Public API ------------------------------------------------------

    async def run(self) -> CommandResult:
        return await self._submit(RunCommand())

    async def pause(self) -> CommandResult:
        return await self._submit(PauseCommand())

    async def reset(self) -> CommandResult:
        return await self._submit(ResetCommand())

    async def set_time_mode(self, mode: str, time_multiplier: float | None = None) -> CommandResult:
        return await self._submit(SetTimeModeCommand(mode=mode, time_multiplier=time_multiplier))

    async def step(self, delta: float) -> CommandResult:
        return await self._submit(StepCommand(delta=delta))

    async def submit_command(self, command: Command) -> CommandResult:
        return await self._submit(command)

    def get_snapshot(self) -> SimulationSnapshot:
        return self._latest_snapshot

    def subscribe(
        self, maxsize: int = _DEFAULT_SUBSCRIBER_MAXSIZE
    ) -> asyncio.Queue[SimulationSnapshot]:
        """Register a bounded snapshot queue and seed it with the current state.

        Subscribers run their own publisher task that drains the queue; when the
        queue is full, the core discards the oldest snapshot before adding the
        new one, so a slow client can never delay physics (§2.6).
        """

        queue: asyncio.Queue[SimulationSnapshot] = asyncio.Queue(maxsize=maxsize)
        queue.put_nowait(self._latest_snapshot)
        self._snapshot_subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[SimulationSnapshot]) -> None:
        if queue in self._snapshot_subscribers:
            self._snapshot_subscribers.remove(queue)

    # -- Main loop -------------------------------------------------------

    async def run_loop(self) -> None:
        self._latest_snapshot = self._build_snapshot()
        self._publish_snapshot()
        while True:
            if self._state == SimulationState.RUNNING and self._time_mode != TimeMode.MANUAL:
                await self._tick_wall_clock()
            else:
                command = await self._command_queue.get()
                self._handle_command(command)

    async def _tick_wall_clock(self) -> None:
        now = time.monotonic()
        if self._monotonic_ref is None:
            self._monotonic_ref = now
            wall_delta = 0.0
        else:
            wall_delta = now - self._monotonic_ref
            self._monotonic_ref = now

        multiplier = self._time_multiplier if self._time_mode == TimeMode.SCALED else 1.0
        self._accumulator += max(0.0, wall_delta) * multiplier
        self._drain_fixed_steps()

        if self._state == SimulationState.RUNNING and self._time_mode != TimeMode.MANUAL:
            remaining = self._fixed_step - self._accumulator
            wait = remaining / multiplier if multiplier > 0 else remaining
            command = await self._await_command(max(wait, _MIN_TICK_WAIT))
            if command is not None:
                self._handle_command(command)

    async def _await_command(self, wait: float) -> Command | None:
        try:
            return await asyncio.wait_for(self._command_queue.get(), timeout=wait)
        except asyncio.TimeoutError:
            # Re-check in case a command arrived concurrently with the timeout.
            try:
                return self._command_queue.get_nowait()
            except asyncio.QueueEmpty:
                return None

    def _handle_command(self, command: Command) -> None:
        result = self._dispatch(command)
        if isinstance(command, _WORLD_COMMAND_TYPES):
            # Buffered world commands are applied at the next fixed step; their
            # result is resolved after application.
            return
        self._latest_snapshot = self._build_snapshot()
        self._publish_snapshot()
        self._resolve(command.sequence, result)

    def _dispatch(self, command: Command) -> CommandResult:
        try:
            if isinstance(command, RunCommand):
                return self._apply_run()
            if isinstance(command, PauseCommand):
                return self._apply_pause()
            if isinstance(command, ResetCommand):
                return self._apply_reset()
            if isinstance(command, SetTimeModeCommand):
                return self._apply_set_time_mode(command)
            if isinstance(command, StepCommand):
                return self._apply_step(command)
            if isinstance(command, TrainControlCommand):
                return self._apply_train_control(command)
            if isinstance(command, _WORLD_COMMAND_TYPES):
                self._world_buffer.append(command)
                return CommandResult()
            return CommandResult(ok=False, error=f"unknown command: {type(command).__name__}")
        except Exception as exc:  # noqa: BLE001 - never escape into run_loop
            return CommandResult(ok=False, error=f"command failed: {exc}")

    # -- Control command handlers ---------------------------------------

    def _apply_run(self) -> CommandResult:
        if self._state == SimulationState.RUNNING:
            return CommandResult()
        self._state = SimulationState.RUNNING
        self._monotonic_ref = None
        return CommandResult()

    def _apply_pause(self) -> CommandResult:
        if self._state != SimulationState.RUNNING:
            return CommandResult()
        self._state = SimulationState.PAUSED
        self._monotonic_ref = None
        return CommandResult()

    def _apply_reset(self) -> CommandResult:
        self._state = SimulationState.STOPPED
        self._simulation_time = 0.0
        self._accumulator = 0.0
        self._monotonic_ref = None
        self._world_buffer.clear()
        for train in self._trains.values():
            train.reset()
        return CommandResult()

    def _apply_set_time_mode(self, command: SetTimeModeCommand) -> CommandResult:
        try:
            mode = TimeMode(command.mode)
        except ValueError:
            return CommandResult(ok=False, error=f"unknown time mode: {command.mode!r}")
        multiplier = command.time_multiplier
        if mode == TimeMode.SCALED and (
            multiplier is None or multiplier <= 0 or not math.isfinite(multiplier)
        ):
            return CommandResult(
                ok=False,
                error="time_multiplier must be a positive finite number for SCALED mode",
            )
        self._time_mode = mode
        self._time_multiplier = multiplier if mode == TimeMode.SCALED else 1.0
        # Reset the monotonic reference so a mode change never carries over
        # elapsed time from the previous mode.
        self._monotonic_ref = None
        return CommandResult()

    def _apply_step(self, command: StepCommand) -> CommandResult:
        if self._time_mode != TimeMode.MANUAL:
            return CommandResult(ok=False, error="step is only valid in MANUAL mode")
        if command.delta < 0 or not math.isfinite(command.delta):
            return CommandResult(ok=False, error="delta must be a non-negative finite number")
        self._accumulator += command.delta
        self._drain_fixed_steps()
        return CommandResult()

    def _apply_train_control(self, command: TrainControlCommand) -> CommandResult:
        train = self._trains.get(command.train_id)
        if train is None:
            return CommandResult(ok=False, error=f"unknown train: {command.train_id}")
        result = train.apply_control(command.payload)
        return CommandResult(ok=result.ok, error=result.error)

    # -- Fixed-step update cycle (§2.5) ----------------------------------

    def _drain_fixed_steps(self) -> None:
        while self._accumulator >= self._fixed_step - _EPSILON:
            self._accumulator -= self._fixed_step
            self._run_fixed_step(self._fixed_step)

    def _run_fixed_step(self, duration: float) -> None:
        # 1. Apply queued ATP/train-control commands in arrival order.
        for command in list(self._world_buffer):
            result = self._apply_world_command(command)
            self._resolve(command.sequence, result)
        self._world_buffer.clear()
        # 2. Advance simulation time to the nominal-step end.
        self._simulation_time += duration
        # 3. Update each train's equipment and physics in stable train-ID order.
        for train_id in self._train_ids_sorted:
            self._trains[train_id].step(duration)
        # 4. Produce a read-only state snapshot.
        self._latest_snapshot = self._build_snapshot()
        self._publish_snapshot()

    def _apply_world_command(self, command: Command) -> CommandResult:
        if isinstance(command, AtpStateCommand):
            return self._apply_atp_state(command)
        return CommandResult(ok=False, error=f"unknown world command: {type(command).__name__}")

    def _apply_atp_state(self, command: AtpStateCommand) -> CommandResult:
        # Phase 3: convert the atp_to_train bit string into train control through
        # the train model. No trains accept ATP state yet.
        train = self._trains.get(command.train_id)
        if train is None:
            return CommandResult(ok=False, error=f"unknown train: {command.train_id}")
        return CommandResult()

    # -- Helpers ---------------------------------------------------------

    async def _submit(self, template: Command) -> CommandResult:
        sequence = self._next_sequence()
        command = dataclasses.replace(template, sequence=sequence)
        future: asyncio.Future[CommandResult] = asyncio.get_running_loop().create_future()
        self._results[sequence] = future
        self._command_queue.put_nowait(command)
        return await future

    def _next_sequence(self) -> int:
        sequence = self._sequence
        self._sequence += 1
        return sequence

    def _resolve(self, sequence: int, result: CommandResult) -> None:
        future = self._results.pop(sequence, None)
        if future is not None and not future.done():
            future.set_result(result)

    def _build_snapshot(self) -> SimulationSnapshot:
        trains = tuple(self._trains[tid].get_snapshot() for tid in self._train_ids_sorted)
        return SimulationSnapshot(
            simulation_state=self._state,
            simulation_time=self._simulation_time,
            time_mode=self._time_mode,
            time_multiplier=self._time_multiplier,
            trains=trains,
        )

    def _publish_snapshot(self) -> None:
        snapshot = self._latest_snapshot
        for queue in self._snapshot_subscribers:
            while queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            try:
                queue.put_nowait(snapshot)
            except asyncio.QueueFull:
                pass
