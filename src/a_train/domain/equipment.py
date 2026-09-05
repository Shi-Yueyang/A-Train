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
    def type(self) -> str:
        """Equipment type identifier (e.g. 'btm')."""
        ...

    @property
    def key(self) -> str:
        """Unique instance identity within one train."""
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

    type = "door"

    def __init__(self, key: str, *, initial_state: str = "closed") -> None:
        if initial_state not in ("open", "closed"):
            raise ValueError(f"invalid initial door state: {initial_state!r}")
        self._key = key
        self._initial = initial_state
        self._state = initial_state

    @property
    def key(self) -> str:
        return self._key

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

    type = "cab"

    def __init__(self, key: str, cab_id: int, *, initial_active: bool = False) -> None:
        self._key = key
        self._cab_id = cab_id
        self._initial_active = initial_active
        self._active = initial_active

    @property
    def cab_id(self) -> int:
        return self._cab_id

    @property
    def key(self) -> str:
        return self._key

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

    type = "btm"

    def __init__(self, key: str, cab_id: int) -> None:
        self._key = key
        self._cab_id = cab_id
        self._pending: bytes | None = None
        self._received_count = 0

    @property
    def cab_id(self) -> int:
        return self._cab_id

    @property
    def key(self) -> str:
        return self._key

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
    """Train-level ATP equipment that records the most recent command."""

    type = "stcs_atp"

    def __init__(self, key: str) -> None:
        self._key = key
        self._last_command: str | None = None

    @property
    def key(self) -> str:
        return self._key

    def apply_control(self, command: str) -> None:
        """Record the command without interpreting it."""
        self._last_command = command

    def read_state(self) -> StcsAtpSnapshot:
        return StcsAtpSnapshot(last_command=self._last_command)

    def reset(self) -> None:
        self._last_command = None


# -- Equipment factory registration -------------------------------------------


@dataclass(frozen=True, kw_only=True)
class EquipmentContext:
    """Train-scope configuration factories may need to build instances."""

    initial_door_state: str
    initial_active_cab: int


def _create_cab(
    key: str,
    ctx: EquipmentContext,
    *,
    initial_active: bool | None = None,
) -> Cab:
    cab_id = int(key.removeprefix("cab_"))
    if initial_active is None:
        initial_active = cab_id == ctx.initial_active_cab
    return Cab(key, cab_id, initial_active=initial_active)


def _create_door(
    key: str,
    ctx: EquipmentContext,
    *,
    initial_state: str | None = None,
) -> Door:
    if initial_state is None:
        initial_state = ctx.initial_door_state
    return Door(key, initial_state=initial_state)


def _create_btm(key: str, _ctx: EquipmentContext) -> Btm:
    return Btm(key, int(key.removeprefix("btm_")))


def _create_stcs_atp(key: str, _ctx: EquipmentContext) -> StcsAtp:
    return StcsAtp(key)


EQUIPMENT_FACTORIES["cab"] = _create_cab
EQUIPMENT_FACTORIES["door"] = _create_door
EQUIPMENT_FACTORIES["btm"] = _create_btm
EQUIPMENT_FACTORIES["stcs_atp"] = _create_stcs_atp
