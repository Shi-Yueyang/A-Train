"""Typed domain controls shared by trains, equipment, and intents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DoorControl:
    command: str


@dataclass(frozen=True)
class BtmControl:
    data: bytes
    cab_id: int | None = None


@dataclass(frozen=True)
class StcsAtpControl:
    """The complete writable surface of one ``stcs_atp_<cab_id>`` instance.

    Every field is optional; ``None`` leaves that state unchanged. ``command``
    is the raw ATP-to-train bit string; the door fields are observed door
    state fed back by the doors through the intent resolver. The handle
    fields are one driving system's full handle state, asserted per cab
    through the intent resolver to drive the direction/traction feedback
    bits.
    """

    command: str | None = None
    left_door_open: bool | None = None
    right_door_open: bool | None = None
    key_activation: bool | None = None
    cab_activation: bool | None = None
    cab_id: int | None = None
    direction: str | None = None
    mode: str | None = None


@dataclass(frozen=True, kw_only=True)
class DrivingSystemControl:
    """Handle positions of one cab's driving system (all fields optional)."""

    mode: str | None = None
    direction: str | None = None
    acceleration: float | None = None


@dataclass(frozen=True, kw_only=True)
class DriverControl:
    """A driving system's resolved request to the train, in track-relative
    terms. ``traction`` is signed (positive pulls toward increasing position)
    and scaled by the traction limit; ``brake`` opposes the current motion
    and is scaled by the deceleration limit. Emitted by equipment intents,
    never by adapters.
    """

    cab_id: int | None = None
    traction: float = 0.0
    brake: float = 0.0


@dataclass(frozen=True, kw_only=True)
class TrainControl:
    cab_id: int | None = None
    drive_demand: float | None = None
    active: bool | None = None
    key: bool | None = None


EquipmentControl = DoorControl | BtmControl | StcsAtpControl | DrivingSystemControl
Control = EquipmentControl | TrainControl | DriverControl
