"""Doors, cabs, and BTM equipment behaviour (§3.5, §3.6, atp-api.md §3.2).

Every train-facing equipment capability is a narrow component behind the train
aggregate. A component keeps private mutable state, accepts plain-value
controls (e.g. ``apply_control("open")``), produces its own frozen snapshot via
``read_state``, and restores its configured state on ``reset``. It never
imports adapters, accesses the simulation clock, or modifies train physical
state directly. Adapters translate protocol data into equipment calls and
publish snapshot data.

Addon equipment (Cab, Door, BTM, StcsAtp, and future equipment) implements
the ``Equipment`` protocol: a ``key`` naming the type plus a ``slot`` naming
the instance (empty for train-level singletons, the cab id for per-cab
equipment). ``EQUIPMENT_FACTORIES`` maps type key to a factory and may be
invoked any number of times; a factory receives ``(slot, EquipmentContext,
**params)`` — instance identity, train-scope configuration, and per-instance
overrides. The train holds one flat list of instances and iterates it
generically. Components that need to observe core physics state may also
implement an optional ``step(dt, speed)`` hook, called generically by the
train after integration.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .snapshots import BtmSnapshot, CabSnapshot, DoorSnapshot, StcsAtpSnapshot

# -- Equipment protocol -------------------------------------------------------


@runtime_checkable
class Equipment(Protocol):
    """Common lifecycle interface for pluggable addon equipment instances."""

    @property
    def key(self) -> str:
        """Equipment type identifier (e.g. 'btm')."""
        ...

    @property
    def slot(self) -> str | int:
        """Instance identity within the type ('' for train-level singletons)."""
        ...

    def read_state(self) -> Any:
        """Return a frozen snapshot of current equipment state."""
        ...

    def reset(self) -> None:
        """Restore configured initial state."""
        ...


# -- Equipment factories ------------------------------------------------------

EQUIPMENT_FACTORIES: dict[str, Callable[..., Equipment]] = {}

# -- Door --------------------------------------------------------------------


class Door:
    """One train door's state. Door motion is instantaneous on ``apply_control``."""

    key = "door"

    def __init__(self, slot: str = "", *, initial_state: str = "closed") -> None:
        if initial_state not in ("open", "closed"):
            raise ValueError(f"invalid initial door state: {initial_state!r}")
        self._slot = slot
        self._initial = initial_state
        self._state = initial_state

    @property
    def slot(self) -> str:
        return self._slot

    @property
    def closed(self) -> bool:
        return self._state == "closed"

    def apply_control(self, command: str) -> None:
        """Accept ``"open"`` or ``"close"``."""
        if command not in ("open", "close"):
            raise ValueError(f"invalid door command: {command!r}")
        self._state = "open" if command == "open" else "closed"

    def read_state(self) -> DoorSnapshot:
        return DoorSnapshot(state=self._state)

    def reset(self) -> None:
        self._state = self._initial


# -- Cab ---------------------------------------------------------------------


class Cab:
    """One cab's local state: an activation flag with no control-side effect."""

    key = "cab"

    def __init__(self, cab_id: int, *, initial_active: bool = False) -> None:
        self._cab_id = cab_id
        self._initial_active = initial_active
        self._active = initial_active

    @property
    def cab_id(self) -> int:
        return self._cab_id

    @property
    def slot(self) -> int:
        return self._cab_id

    @property
    def active(self) -> bool:
        return self._active

    def apply_control(self, command: str) -> None:
        """Accept ``"activate"`` or ``"deactivate"``."""
        if command not in ("activate", "deactivate"):
            raise ValueError(f"invalid cab command: {command!r}")
        self._active = command == "activate"

    def read_state(self) -> CabSnapshot:
        return CabSnapshot(cab_id=self._cab_id, active=self._active)

    def reset(self) -> None:
        self._active = self._initial_active


# -- BTM ---------------------------------------------------------------------


class Btm:
    """Simulated BTM equipment for one cab. Payloads are opaque bytes."""

    key = "btm"

    def __init__(self, cab_id: int) -> None:
        self._cab_id = cab_id
        self._pending: bytes | None = None
        self._received_count = 0

    @property
    def cab_id(self) -> int:
        return self._cab_id

    @property
    def slot(self) -> int:
        return self._cab_id

    def accept(self, data: bytes) -> None:
        self._pending = bytes(data)
        self._received_count += 1

    def read_state(self) -> BtmSnapshot:
        return BtmSnapshot(
            cab_id=self._cab_id,
            pending=self._pending is not None,
            payload_b64=base64.b64encode(self._pending).decode("ascii")
            if self._pending is not None
            else None,
            received_count=self._received_count,
        )

    def reset(self) -> None:
        self._pending = None
        self._received_count = 0


# -- ATP protection state -------------------------------------------------------


class StcsAtp:
    """Train-level ATP protection state asserted by ``atp_signal`` bits.

    Skeleton semantics (atp-api.md §4.2): bit handlers translate into named
    commands that assert or release one flag each; the train dispatcher calls
    ``apply_control`` through the core command queue, so history is ordered
    and replayable. The ``step`` hook combines the stored ATP state with the
    core's physical state: a full stop releases both brake bits (placeholder
    release rule; the traction cut-off persists). Nothing feeds back into
    dynamics yet -- like all equipment in this version.
    """

    key = "stcs_atp"

    _COMMANDS = (
        "traction_cut",
        "traction_release",
        "brake_service",
        "brake_service_off",
        "brake_emergency",
        "brake_emergency_off",
    )

    def __init__(self, slot: str = "") -> None:
        self._slot = slot
        self._traction_cutoff = False
        self._service = False
        self._emergency = False

    @property
    def slot(self) -> str:
        return self._slot

    def apply_control(self, command: str) -> None:
        """Assert or release one protection flag."""
        if command not in self._COMMANDS:
            raise ValueError(f"invalid stcs_atp command: {command!r}")
        if command == "traction_cut":
            self._traction_cutoff = True
        elif command == "traction_release":
            self._traction_cutoff = False
        elif command == "brake_service":
            self._service = True
        elif command == "brake_service_off":
            self._service = False
        elif command == "brake_emergency":
            self._emergency = True
        elif command == "brake_emergency_off":
            self._emergency = False

    def step(self, _dt: float, speed: float) -> None:
        if speed == 0.0:
            self._service = False
            self._emergency = False

    def read_state(self) -> StcsAtpSnapshot:
        return StcsAtpSnapshot(
            traction_cutoff=self._traction_cutoff,
            service=self._service,
            emergency=self._emergency,
        )

    def reset(self) -> None:
        self._traction_cutoff = False
        self._service = False
        self._emergency = False


# -- Equipment factory registration -------------------------------------------


@dataclass(frozen=True, kw_only=True)
class EquipmentContext:
    """Train-scope configuration factories may need to build instances."""

    initial_door_state: str
    initial_active_cab: int


def _create_cab(
    slot: str | int,
    ctx: EquipmentContext,
    *,
    initial_active: bool | None = None,
) -> Cab:
    cab_id = int(slot)
    if initial_active is None:
        initial_active = cab_id == ctx.initial_active_cab
    return Cab(cab_id, initial_active=initial_active)


def _create_door(
    slot: str | int,
    ctx: EquipmentContext,
    *,
    initial_state: str | None = None,
) -> Door:
    if initial_state is None:
        initial_state = ctx.initial_door_state
    return Door(str(slot), initial_state=initial_state)


def _create_btm(slot: str | int, _ctx: EquipmentContext) -> Btm:
    return Btm(int(slot))


def _create_stcs_atp(slot: str | int, _ctx: EquipmentContext) -> StcsAtp:
    return StcsAtp(str(slot))


EQUIPMENT_FACTORIES["cab"] = _create_cab
EQUIPMENT_FACTORIES["door"] = _create_door
EQUIPMENT_FACTORIES["btm"] = _create_btm
EQUIPMENT_FACTORIES["stcs_atp"] = _create_stcs_atp
