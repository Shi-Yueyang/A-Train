"""Frozen, transport-neutral snapshot types for the train world (§2.6, §3.6).

Snapshots are read-only views produced by the train aggregate. They contain
only scalar values, immutable tuples, and frozen nested dataclasses; they never
expose a mutable internal object. Cab activation is native train state and is
reported per configured cab in ``TrainSnapshot.cabs``. Equipment state (doors,
BTM) is represented as individually addressed entries, so new equipment can be
added without changing TrainSnapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CabSnapshot:
    """Read-only view of one cab's key, activation state, and track facing."""

    cab_id: int
    active: bool = False
    key: bool = False
    facing: str = "forward"


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
class DrivingSystemSnapshot:
    """Read-only view of one cab's driving system (driver-room handles).

    ``mode`` is ``"traction"`` / ``"off"`` / ``"brake"``, ``direction`` is
    ``"forward"`` / ``"off"`` / ``"backward"`` (cab-relative), and
    ``acceleration`` is the handle effort in ``[0.0, 1.0]``. ``facing``
    repeats the cab's track-facing for display.
    """

    cab_id: int = 0
    facing: str = "forward"
    mode: str = "off"
    direction: str = "off"
    acceleration: float = 0.0


@dataclass(frozen=True)
class SignalState:
    """One named boolean signal, ordered by bit index in its snapshot list."""

    name: str
    value: bool


@dataclass(frozen=True)
class StcsAtpSnapshot:
    """Read-only view of the ATP protection equipment state.

    ``train_in_states`` and ``train_out_states`` list every decoded signal by
    name in bit order; ``train_out_signal`` is the same train-out state as one
    bit string, ``last_command`` the raw last ATP assertion, and
    ``last_command_time`` the wall-clock time (POSIX seconds) at which the
    core applied that assertion (``None`` until the first command).
    """

    last_command: str | None = None
    last_command_time: float | None = None
    train_out_signal: str = ""
    train_in_states: tuple[SignalState, ...] = ()
    train_out_states: tuple[SignalState, ...] = ()


@dataclass(frozen=True)
class EquipmentSnapshot:
    """Read-only state for one uniquely addressed equipment instance.

    ``key`` is the transport-neutral REST address; ``cab_id`` carries the
    owning cab for cab-scoped equipment (BTM, driving systems, STCS ATP) and
    is ``None`` for train-level instances such as doors.
    """

    type: str
    key: str
    state: Any
    cab_id: int | None = None


@dataclass(frozen=True)
class TrainSnapshot:
    """Read-only view of a single train at a point in simulation time.

    Physical fields keep their names and meanings across versions. Cab
    activation is native train state exposed as one entry per configured cab.
    ``direction`` is derived from the current speed sign: ``"forward"``,
    ``"backward"``, or ``"stopped"`` at zero speed. ``drive_demand`` is the
    held legacy lever and has no effect while any driving system is engaged
    (the engaged driving system overwrites it). Equipment state is a stable
    tuple of individually addressed entries.
    """

    train_id: str
    cabs: tuple[CabSnapshot, ...] = ()
    speed: float = 0.0
    acceleration: float = 0.0
    position: float = 0.0
    direction: str = "stopped"
    drive_demand: float = 0.0
    equipment: tuple[EquipmentSnapshot, ...] = field(default_factory=tuple)
