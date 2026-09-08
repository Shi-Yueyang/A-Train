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
    command: str


@dataclass(frozen=True, kw_only=True)
class TrainControl:
    cab_id: int | None = None
    drive_demand: float | None = None
    active: bool | None = None


EquipmentControl = DoorControl | BtmControl | StcsAtpControl
Control = EquipmentControl | TrainControl