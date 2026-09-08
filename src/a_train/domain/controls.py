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
    """The complete writable surface of the ``stcs_atp`` equipment.

    Every field is optional; ``None`` leaves that state unchanged. ``command``
    is the raw ATP-to-train bit string; the door fields are observed door
    state fed back by the doors through the intent resolver.
    """

    command: str | None = None
    left_door_open: bool | None = None
    right_door_open: bool | None = None


@dataclass(frozen=True, kw_only=True)
class TrainControl:
    cab_id: int | None = None
    drive_demand: float | None = None
    active: bool | None = None


EquipmentControl = DoorControl | BtmControl | StcsAtpControl
Control = EquipmentControl | TrainControl