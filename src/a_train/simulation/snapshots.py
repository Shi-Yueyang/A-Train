"""Frozen, transport-neutral simulation snapshot types.

Snapshots are read-only views of the world produced by the core. They contain
only scalar values, immutable tuples, and frozen nested dataclasses; they never
expose a train object, list, or mutable internal collection (see §2.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SimulationState(Enum):
    """Lifecycle state of the simulation core (§2.2)."""

    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


class TimeMode(Enum):
    """How simulation time advances while the core is running (§2.3)."""

    REALTIME = "REALTIME"
    SCALED = "SCALED"
    MANUAL = "MANUAL"


@dataclass(frozen=True)
class TrainSnapshot:
    """Read-only view of a single train at a point in simulation time."""

    train_id: str
    speed: float = 0.0
    acceleration: float = 0.0
    position: float = 0.0
    direction: str = "forward"


@dataclass(frozen=True)
class TriggeredEventRecord:
    """A scenario event that has been triggered exactly once."""

    event_id: str
    at: float
    type: str


@dataclass(frozen=True)
class SimulationSnapshot:
    """The complete read-only state produced by the core (§2.5)."""

    simulation_state: SimulationState = SimulationState.STOPPED
    simulation_time: float = 0.0
    time_mode: TimeMode = TimeMode.MANUAL
    time_multiplier: float = 1.0
    trains: tuple[TrainSnapshot, ...] = ()
    recent_events: tuple[TriggeredEventRecord, ...] = ()
