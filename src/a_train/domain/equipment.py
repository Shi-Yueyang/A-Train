"""Doors, cabs, BTM equipment, and train-local I/O behaviour (§3.5, §3.6, §4.6).

Every train-facing equipment capability is a narrow component behind the train
aggregate. A component keeps private mutable state, accepts plain-value
controls (e.g. ``apply_control("open")``), produces its own frozen snapshot via
``read_state``, and restores its configured state on ``reset``. It never
imports adapters, accesses the simulation clock, or modifies train physical
state directly. Adapters translate protocol data into equipment calls and
publish snapshot data.

Addon equipment (Cab, Door, BTM, I/O, and future equipment) implements the
``Equipment`` protocol: a ``key`` naming the type plus a ``slot`` naming the
instance (empty for train-level singletons, the cab id for per-cab equipment).
``EQUIPMENT_FACTORIES`` maps type key to a factory and may be invoked any
number of times; a factory receives ``(slot, EquipmentContext, **params)`` —
instance identity, train-scope configuration, and per-instance overrides. The
train holds one flat list of instances and iterates it generically.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .io import (
    DEFAULT_ATP_TO_TRAIN,
    DEFAULT_TRAIN_TO_ATP,
    IoMapping,
    from_bit_string,
    to_bit_string,
)
from .snapshots import BtmSnapshot, CabSnapshot, DoorSnapshot, IoSnapshot

# -- Equipment protocol -------------------------------------------------------


@runtime_checkable
class Equipment(Protocol):
    """Common lifecycle interface for pluggable addon equipment instances."""

    @property
    def key(self) -> str:
        """Equipment type identifier (e.g. 'btm', 'io')."""
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


# -- Digital I/O -------------------------------------------------------------


@dataclass(frozen=True)
class IoConfig:
    """Per-train digital-signal mappings (§4.7)."""

    train_to_atp: IoMapping = DEFAULT_TRAIN_TO_ATP
    atp_to_train: IoMapping = DEFAULT_ATP_TO_TRAIN


class DigitalIo:
    """Named on/off values with bit-string framing in both directions."""

    key = "io"
    slot = ""

    _DIRECTIONS = ("train_to_atp", "atp_to_train")

    def __init__(self, config: IoConfig | None = None) -> None:
        self._config = config if config is not None else IoConfig()
        self._train_values: dict[str, bool] = {
            s.name: False for s in self._config.train_to_atp.signals
        }
        self._atp_values: dict[str, bool] = {
            s.name: False for s in self._config.atp_to_train.signals
        }

    def _target(self, direction: str) -> dict[str, bool]:
        if direction not in self._DIRECTIONS:
            raise ValueError(f"invalid I/O direction: {direction!r}")
        return self._atp_values if direction == "atp_to_train" else self._train_values

    def _mapping(self, direction: str) -> IoMapping:
        if direction == "atp_to_train":
            return self._config.atp_to_train
        return self._config.train_to_atp

    def update_bits(self, direction: str, bits: str) -> None:
        """Apply a raw bit-string update to one direction (§4.7)."""
        target = self._target(direction)
        target.update(from_bit_string(bits, self._mapping(direction)))

    def update_named(self, direction: str, values: Mapping[str, bool]) -> None:
        """Apply named on/off values to one direction."""
        target = self._target(direction)
        for name, value in values.items():
            if name in target:
                target[name] = bool(value)

    def read_state(self) -> IoSnapshot:
        return IoSnapshot(
            train_to_atp=to_bit_string(self._train_values, self._config.train_to_atp),
            atp_to_train=to_bit_string(self._atp_values, self._config.atp_to_train),
            values=tuple(
                (s.name, self._train_values.get(s.name, False))
                for s in self._config.train_to_atp.signals
            ),
        )

    def reset(self) -> None:
        self._train_values = {s.name: False for s in self._config.train_to_atp.signals}
        self._atp_values = {s.name: False for s in self._config.atp_to_train.signals}


# -- Equipment factory registration -------------------------------------------


@dataclass(frozen=True, kw_only=True)
class EquipmentContext:
    """Train-scope configuration factories may need to build instances."""

    io_config: IoConfig
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


def _create_io(slot: str | int, ctx: EquipmentContext) -> DigitalIo:
    return DigitalIo(ctx.io_config)


EQUIPMENT_FACTORIES["cab"] = _create_cab
EQUIPMENT_FACTORIES["door"] = _create_door
EQUIPMENT_FACTORIES["btm"] = _create_btm
EQUIPMENT_FACTORIES["io"] = _create_io
