"""Doors, cabs, BTM equipment, and train-local I/O behaviour (§3.5, §3.6, §4.6).

Every train-facing equipment capability is a narrow component behind the train
aggregate. A component keeps private mutable state, accepts deliveries or
commands via ``receive``, updates during ``step(dt)``, produces its own frozen
snapshot, and restores its configured state on ``reset``. It never imports
adapters, accesses the simulation clock, or modifies train physical state
directly. Adapters translate protocol data into equipment commands and publish
snapshot data.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass

from .io import (
    DEFAULT_ATP_TO_TRAIN,
    DEFAULT_TRAIN_TO_ATP,
    IoMapping,
    from_bit_string,
    to_bit_string,
)
from .snapshots import BtmSnapshot, CabSnapshot, DoorSnapshot, IoSnapshot

# -- Door --------------------------------------------------------------------


@dataclass(frozen=True)
class DoorCommand:
    """A door open/close request routed through the train aggregate."""

    command: str  # "open" or "close"


class Door:
    """Train-level door state. Door motion is instantaneous on ``receive``."""

    def __init__(self, *, initial_state: str = "closed") -> None:
        if initial_state not in ("open", "closed"):
            raise ValueError(f"invalid initial door state: {initial_state!r}")
        self._initial = initial_state
        self._state = initial_state

    @property
    def closed(self) -> bool:
        return self._state == "closed"

    def receive(self, message: object) -> None:
        if isinstance(message, DoorCommand):
            if message.command not in ("open", "close"):
                raise ValueError(f"invalid door command: {message.command!r}")
            self._state = message.command

    def step(self, dt: float) -> None:  # noqa: ARG002
        return

    def get_snapshot(self) -> DoorSnapshot:
        return DoorSnapshot(state=self._state)

    def reset(self) -> None:
        self._state = self._initial


# -- Cab ---------------------------------------------------------------------


@dataclass(frozen=True)
class CabState:
    """An active/inactive update for a cab."""

    active: bool


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

    def receive(self, message: object) -> None:
        if isinstance(message, CabState):
            self._active = message.active

    def step(self, dt: float) -> None:  # noqa: ARG002
        return

    def get_snapshot(self) -> CabSnapshot:
        return CabSnapshot(cab_id=self._cab_id, active=self._active)

    def reset(self) -> None:
        self._active = self._initial_active


# -- BTM ---------------------------------------------------------------------


@dataclass(frozen=True)
class BtmDelivery:
    """An opaque byte delivery to a cab's BTM equipment (§4.6)."""

    cab_id: int
    data: bytes


class BtmEquipment:
    """Simulated BTM equipment for one cab. Payloads are opaque bytes."""

    def __init__(self, cab_id: int) -> None:
        self._cab_id = cab_id
        self._pending: bytes | None = None
        self._received_count = 0

    @property
    def cab_id(self) -> int:
        return self._cab_id

    def receive(self, message: object) -> None:
        if isinstance(message, BtmDelivery):
            if message.cab_id != self._cab_id:
                raise ValueError(
                    f"BTM delivery for cab {message.cab_id} does not match "
                    f"equipment cab {self._cab_id}"
                )
            self._pending = bytes(message.data)
            self._received_count += 1

    def step(self, dt: float) -> None:  # noqa: ARG002
        return

    def get_snapshot(self) -> BtmSnapshot:
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


@dataclass(frozen=True)
class IoBits:
    """A raw bit-string update for one direction."""

    direction: str  # "train_to_atp" or "atp_to_train"
    bits: str


@dataclass(frozen=True)
class IoNamed:
    """A named-value update for one direction."""

    direction: str
    values: Mapping[str, bool]


class DigitalIo:
    """Named on/off values with bit-string framing in both directions."""

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

    def receive(self, message: object) -> None:
        if isinstance(message, IoBits):
            target = self._target(message.direction)
            mapping = self._mapping(message.direction)
            target.update(from_bit_string(message.bits, mapping))
        elif isinstance(message, IoNamed):
            target = self._target(message.direction)
            for name, value in message.values.items():
                if name in target:
                    target[name] = bool(value)

    def step(self, dt: float) -> None:  # noqa: ARG002
        return

    def get_snapshot(self) -> IoSnapshot:
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
