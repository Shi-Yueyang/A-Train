"""Frozen, transport-neutral snapshot types for the train world (§2.6, §3.6).

Snapshots are read-only views produced by the train aggregate. They contain
only scalar values, immutable tuples, and frozen nested dataclasses; they never
expose a mutable internal object. Equipment state (cabs, doors, BTM, I/O) is
in the ``equipment`` dict keyed by equipment type, so new equipment can be
added without changing TrainSnapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CabSnapshot:
    """Read-only view of one cab's local state."""

    cab_id: int
    active: bool = False


@dataclass(frozen=True)
class DoorSnapshot:
    """Read-only view of the train door state."""

    state: str = "closed"


@dataclass(frozen=True)
class BtmSnapshot:
    """Read-only view of the BTM equipment for one cab/train.

    The BTM payload is opaque bytes; this snapshot only reports whether a
    delivery is pending and its base64 form plus the total delivered count.
    """

    cab_id: int = 0
    pending: bool = False
    payload_b64: str | None = None
    received_count: int = 0


@dataclass(frozen=True)
class IoSnapshot:
    """Read-only view of the digital I/O bit strings and named values."""

    train_to_atp: str = ""
    atp_to_train: str = ""
    values: tuple[tuple[str, bool], ...] = ()


@dataclass(frozen=True)
class TrainSnapshot:
    """Read-only view of a single train at a point in simulation time.

    Physical fields keep their names and meanings across versions. Equipment
    state is in the ``equipment`` dict keyed by equipment type.
    """

    train_id: str
    cab_ids: tuple[int, ...] = ()
    speed: float = 0.0
    acceleration: float = 0.0
    position: float = 0.0
    direction: str = "forward"
    drive_demand: float = 0.0
    equipment: dict[str, Any] = field(default_factory=dict)
