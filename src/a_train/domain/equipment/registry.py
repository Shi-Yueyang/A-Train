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


def _cab_id_from_key(key: str, prefix: str, cab_id: int | None) -> int:
    if cab_id is not None:
        return cab_id
    try:
        return int(key.removeprefix(prefix))
    except ValueError:
        raise ValueError(f"{key!r} requires an explicit cab_id") from None


def _create_btm(key: str, _ctx: EquipmentContext, *, cab_id: int | None = None) -> Btm:
    return Btm(key, _cab_id_from_key(key, "btm_", cab_id))


def _create_stcs_atp(key: str, _ctx: EquipmentContext, *, cab_id: int | None = None) -> StcsAtp:
    return StcsAtp(key, cab_id=_cab_id_from_key(key, "stcs_atp_", cab_id))


def _create_driving_system(
    key: str,
    ctx: EquipmentContext,
    *,
    cab_id: int | None = None,
    facing: int | None = None,
    initial_mode: str = "off",
    initial_direction: str = "off",
    initial_acceleration: float = 0.0,
) -> DrivingSystem:
    cab_id = _cab_id_from_key(key, "driving_", cab_id)
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
