"""Doors, cabs, BTM equipment, and train-local I/O behaviour (§3.5, §3.6, §4.6).

Every train-facing equipment capability is a narrow component behind the train
aggregate. A component keeps private mutable state, accepts plain-value
controls (e.g. ``apply_control("open")``), produces its own frozen snapshot via
``read_state``, and restores its configured state on ``reset``. It never
imports adapters, accesses the simulation clock, or modifies train physical
state directly. Adapters translate protocol data into equipment calls and
publish snapshot data.

Addon equipment (Cab, Door, BTM, I/O, and future equipment) implements the
``Equipment`` protocol and registers in ``EQUIPMENT_REGISTRY``. The train
iterates addon equipment generically; cab authority stays with the train
aggregate.
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
    """Common lifecycle interface for pluggable addon equipment."""

    @property
    def key(self) -> str:
        """Unique equipment type identifier (e.g. 'btm', 'io')."""
        ...

    def read_state(self) -> Any:
        """Return a frozen snapshot of current equipment state."""
        ...

    def reset(self) -> None:
        """Restore configured initial state."""
        ...


# -- Equipment registry -------------------------------------------------------

EQUIPMENT_REGISTRY: dict[str, Callable[..., Equipment]] = {}

# -- Door --------------------------------------------------------------------


class Door:
    """Train-level door state. Door motion is instantaneous on ``apply_control``."""

    key = "door"

    def __init__(self, *, initial_state: str = "closed") -> None:
        if initial_state not in ("open", "closed"):
            raise ValueError(f"invalid initial door state: {initial_state!r}")
        self._initial = initial_state
        self._state = initial_state

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
    """One cab's local state. The train aggregate owns cab authority."""

    def __init__(self, cab_id: int, *, initial_active: bool = False) -> None:
        self._cab_id = cab_id
        self._initial_active = initial_active
        self._active = initial_active

    @property
    def cab_id(self) -> int:
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


class CabEquipmentSet:
    """Composite cab equipment managing one ``Cab`` per cab (§3.5)."""

    key = "cab"

    def __init__(self, cab_ids: tuple[int, ...], initial_active_cab: int = 0) -> None:
        self._instances = {
            cid: Cab(cid, initial_active=(cid == initial_active_cab)) for cid in cab_ids
        }

    def apply_control(self, cab_id: int, command: str) -> None:
        """Route an activate/deactivate command to one cab."""
        instance = self._instances.get(cab_id)
        if instance is None:
            raise ValueError(
                f"cab command for {cab_id}: no equipment (known cabs: {sorted(self._instances)})"
            )
        instance.apply_control(command)

    def set_active(self, cab_id: int) -> None:
        """Make one cab active and every other cab inactive."""
        for cid, instance in self._instances.items():
            instance.apply_control("activate" if cid == cab_id else "deactivate")

    def read_state(self) -> tuple[CabSnapshot, ...]:
        return tuple(instance.read_state() for instance in self._instances.values())

    def reset(self) -> None:
        for instance in self._instances.values():
            instance.reset()


# -- BTM ---------------------------------------------------------------------


class BtmEquipment:
    """Simulated BTM equipment for one cab. Payloads are opaque bytes."""

    def __init__(self, cab_id: int) -> None:
        self._cab_id = cab_id
        self._pending: bytes | None = None
        self._received_count = 0

    @property
    def cab_id(self) -> int:
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


class BtmEquipmentSet:
    """Composite BTM equipment managing one ``BtmEquipment`` per cab (§4.6)."""

    key = "btm"

    def __init__(self, cab_ids: tuple[int, ...]) -> None:
        self._instances = {cid: BtmEquipment(cid) for cid in cab_ids}

    def deliver(self, cab_id: int, data: bytes) -> None:
        """Route an opaque byte array to one cab's BTM equipment."""
        instance = self._instances.get(cab_id)
        if instance is None:
            raise ValueError(
                f"BTM delivery for cab {cab_id}: no equipment "
                f"(known cabs: {sorted(self._instances)})"
            )
        instance.accept(data)

    def read_state(self) -> tuple[BtmSnapshot, ...]:
        return tuple(instance.read_state() for instance in self._instances.values())

    def reset(self) -> None:
        for instance in self._instances.values():
            instance.reset()


# -- Digital I/O -------------------------------------------------------------


@dataclass(frozen=True)
class IoConfig:
    """Per-train digital-signal mappings (§4.7)."""

    train_to_atp: IoMapping = DEFAULT_TRAIN_TO_ATP
    atp_to_train: IoMapping = DEFAULT_ATP_TO_TRAIN


class DigitalIo:
    """Named on/off values with bit-string framing in both directions."""

    key = "io"

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


# -- Equipment registration ---------------------------------------------------


def _create_cab(
    cab_ids: tuple[int, ...], initial_active_cab: int = 0, **_kwargs: Any
) -> CabEquipmentSet:
    return CabEquipmentSet(cab_ids, initial_active_cab)


def _create_door(initial_door_state: str = "closed", **_kwargs: Any) -> Door:
    return Door(initial_state=initial_door_state)


def _create_btm(cab_ids: tuple[int, ...], **_kwargs: Any) -> BtmEquipmentSet:
    return BtmEquipmentSet(cab_ids)


def _create_io(io_config: IoConfig | None = None, **_kwargs: Any) -> DigitalIo:
    return DigitalIo(io_config)


EQUIPMENT_REGISTRY["cab"] = _create_cab
EQUIPMENT_REGISTRY["door"] = _create_door
EQUIPMENT_REGISTRY["btm"] = _create_btm
EQUIPMENT_REGISTRY["io"] = _create_io
