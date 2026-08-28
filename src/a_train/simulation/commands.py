"""Frozen command types and command-result types (§2.6).

Commands are the only way to mutate world state. Adapters and the core API
construct frozen command dataclasses and enqueue them onto the core's
``asyncio.Queue``; only ``SimulationCore.run_loop()`` consumes the queue.

Each command carries a monotonically increasing ``sequence`` assigned at enqueue
time so that fixed-step application order is stable and reproducible. Commands
are keyword-only so subclasses can add required payload fields without hitting
the dataclass default-ordering constraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, kw_only=True)
class Command:
    """Base type for every command submitted to the simulation core."""

    sequence: int = 0


@dataclass(frozen=True, kw_only=True)
class RunCommand(Command):
    """Transition the core to ``RUNNING`` (idempotent while already running)."""


@dataclass(frozen=True, kw_only=True)
class PauseCommand(Command):
    """Transition the core to ``PAUSED`` (idempotent while paused/stopped)."""


@dataclass(frozen=True, kw_only=True)
class ResetCommand(Command):
    """Restore the initial world state and set simulation time to zero."""


@dataclass(frozen=True, kw_only=True)
class SetTimeModeCommand(Command):
    """Select ``REALTIME``, ``SCALED``, or ``MANUAL`` time advancement."""

    mode: str
    time_multiplier: float | None = None


@dataclass(frozen=True, kw_only=True)
class StepCommand(Command):
    """Advance simulation time by ``delta`` seconds (``MANUAL`` mode only)."""

    delta: float


@dataclass(frozen=True, kw_only=True)
class AtpStateCommand(Command):
    """Apply an external ATP's digital outputs through the train model."""

    train_id: str
    cab_id: int
    atp_to_train: str


@dataclass(frozen=True, kw_only=True)
class TrainControlCommand(Command):
    """A train-control request submitted through the REST API."""

    train_id: str
    payload: Any


@dataclass(frozen=True, kw_only=True)
class CommandResult:
    """Structured result of a command; invalid input is reported here rather
    than raised, so adapter exceptions never enter ``run_loop()`` (§2.6)."""

    ok: bool = True
    error: str | None = None
