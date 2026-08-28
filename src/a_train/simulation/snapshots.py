"""Frozen, transport-neutral simulation snapshot types.

Snapshots are read-only views of the world produced by the core. They contain
only scalar values, immutable tuples, and frozen nested dataclasses; they never
expose a train object, list, or mutable internal collection (§2.6).

The train-world snapshot types live in ``domain.snapshots`` (the domain owns
them because the train aggregate constructs them). This module re-exports
``TrainSnapshot`` so the simulation public API keeps its existing import path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..domain.snapshots import (
    BtmSnapshot,
    CabSnapshot,
    DoorSnapshot,
    IoSnapshot,
    TrainSnapshot,
)


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
class SimulationSnapshot:
    """The complete read-only state produced by the core (§2.5)."""

    simulation_state: SimulationState = SimulationState.STOPPED
    simulation_time: float = 0.0
    time_mode: TimeMode = TimeMode.MANUAL
    time_multiplier: float = 1.0
    trains: tuple[TrainSnapshot, ...] = ()


__all__ = [
    "BtmSnapshot",
    "CabSnapshot",
    "DoorSnapshot",
    "IoSnapshot",
    "SimulationSnapshot",
    "SimulationState",
    "TimeMode",
    "TrainSnapshot",
]
