"""Cab driving-system equipment."""

from __future__ import annotations

import math

from ..controls import DriverControl, DrivingSystemControl, StcsAtpControl
from ..snapshots import DrivingSystemSnapshot
from .base import EquipmentIntent


class DrivingSystem:
    """A cab's mode, direction, and acceleration handles."""

    type = "driving_system"
    MODES = ("traction", "off", "brake")
    DIRECTIONS = ("forward", "off", "backward")
    _HANDLE_TO_TRACK_SIGN = {"forward": 1, "off": 0, "backward": -1}

    def __init__(
        self,
        key: str,
        *,
        cab_id: int,
        facing: int,
        initial_mode: str = "off",
        initial_direction: str = "off",
        initial_acceleration: float = 0.0,
    ) -> None:
        if initial_mode not in self.MODES:
            raise ValueError(f"invalid initial mode: {initial_mode!r}")
        if initial_direction not in self.DIRECTIONS:
            raise ValueError(f"invalid initial direction: {initial_direction!r}")
        if facing not in (-1, 1):
            raise ValueError("facing must be +1 (track-increasing) or -1")
        self._key = key
        self._cab_id = cab_id
        self._facing = facing
        self._mode = initial_mode
        self._direction = initial_direction
        self._acceleration = initial_acceleration

    @property
    def key(self) -> str:
        return self._key

    @property
    def cab_id(self) -> int:
        return self._cab_id

    def apply_control(self, control: DrivingSystemControl) -> None:
        if not isinstance(control, DrivingSystemControl):
            raise ValueError("driving system control is invalid")
        if control.mode is not None and control.mode not in self.MODES:
            raise ValueError(f"driving mode must be one of {self.MODES}, got {control.mode!r}")
        if control.direction is not None and control.direction not in self.DIRECTIONS:
            raise ValueError(
                f"driving direction must be one of {self.DIRECTIONS}, got {control.direction!r}"
            )
        acceleration = control.acceleration
        if acceleration is not None and (
            isinstance(acceleration, bool)
            or not isinstance(acceleration, (int, float))
            or not math.isfinite(acceleration)
            or not 0.0 <= acceleration <= 1.0
        ):
            raise ValueError("driving acceleration must be a finite value in [0.0, 1.0]")
        self._mode = control.mode if control.mode is not None else self._mode
        self._direction = control.direction if control.direction is not None else self._direction
        self._acceleration = float(acceleration) if acceleration is not None else self._acceleration

    def read_state(self) -> DrivingSystemSnapshot:
        return DrivingSystemSnapshot(
            cab_id=self._cab_id,
            facing="forward" if self._facing == 1 else "backward",
            mode=self._mode,
            direction=self._direction,
            acceleration=self._acceleration,
        )

    def reset(self) -> None:
        self._mode = "off"
        self._direction = "off"
        self._acceleration = 0.0

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        intents: list[EquipmentIntent] = [
            EquipmentIntent(
                source=self._key,
                target=f"stcs_atp_{self._cab_id}",
                control=StcsAtpControl(
                    cab_id=self._cab_id, mode=self._mode, direction=self._direction
                ),
            )
        ]
        if self._mode != "off":
            if self._mode == "traction":
                traction = (
                    self._facing * self._HANDLE_TO_TRACK_SIGN[self._direction] * self._acceleration
                )
                brake = 0.0
            else:
                traction = 0.0
                brake = self._acceleration
            intents.append(
                EquipmentIntent(
                    source=self._key,
                    target="train",
                    control=DriverControl(cab_id=self._cab_id, traction=traction, brake=brake),
                )
            )
        return tuple(intents)
