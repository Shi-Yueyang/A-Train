"""Factories for configured equipment instances."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import Equipment, EquipmentContext
from .btm import Btm
from .door import Door
from .driving_system import DrivingSystem
from .stcs_atp import StcsAtp

EQUIPMENT_FACTORIES: dict[str, Callable[..., Equipment[Any]]] = {}


def _create_door(
    key: str,
    ctx: EquipmentContext,
    *,
    initial_state: str | None = None,
    side: str | None = None,
) -> Door:
    if initial_state is None:
        initial_state = ctx.initial_door_state
    if side is None:
        prefix = key.split("_", 1)[0]
        side = prefix if prefix in ("left", "right") else None
    return Door(key, initial_state=initial_state, side=side)


def _create_btm(key: str, _ctx: EquipmentContext) -> Btm:
    return Btm(key, int(key.removeprefix("btm_")))


def _create_stcs_atp(key: str, _ctx: EquipmentContext) -> StcsAtp:
    return StcsAtp(key, cab_id=int(key.removeprefix("stcs_atp_")))


def _create_driving_system(
    key: str,
    ctx: EquipmentContext,
    *,
    facing: int | None = None,
    initial_mode: str = "off",
    initial_direction: str = "off",
    initial_acceleration: float = 0.0,
) -> DrivingSystem:
    cab_id = int(key.removeprefix("driving_"))
    if facing is None:
        facing = ctx.cab_facings[cab_id]
    return DrivingSystem(
        key,
        cab_id=cab_id,
        facing=facing,
        initial_mode=initial_mode,
        initial_direction=initial_direction,
        initial_acceleration=initial_acceleration,
    )


EQUIPMENT_FACTORIES.update(
    {
        "door": _create_door,
        "btm": _create_btm,
        "stcs_atp": _create_stcs_atp,
        "driving_system": _create_driving_system,
    }
)
