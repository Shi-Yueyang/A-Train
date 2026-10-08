"""Cab driving-system equipment."""

from __future__ import annotations

import math

from ..controls import DriverControl, DrivingSystemControl, StcsAtpControl
from ..physics import is_finite, is_positive_finite
from ..snapshots import DrivingSystemSnapshot
from .base import EquipmentIntent, TrainMotion


class DrivingSystem:
    """A cab's mode, direction, and acceleration handles."""

    type = "driving_system"
    MODES = ("traction", "off", "brake")
    DIRECTIONS = ("forward", "off", "backward")
    CONTROL_MODES = ("manual", "speed", "position")
    _HANDLE_TO_TRACK_SIGN = {"forward": 1, "off": 0, "backward": -1}
    _SPEED_GAIN = 4.0
    _AUTOMATIC_ACCELERATION_DEADBAND = 0.05
    _POSITION_TOLERANCE = 0.05

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
        self._manual_mode = initial_mode
        self._manual_direction = initial_direction
        self._manual_acceleration = initial_acceleration
        self._mode = initial_mode
        self._direction = initial_direction
        self._acceleration = initial_acceleration
        self._control_mode = "manual"
        self._target_speed: float | None = None
        self._target_position: float | None = None
        self._max_speed: float | None = None

    @property
    def key(self) -> str:
        return self._key

    @property
    def cab_id(self) -> int:
        return self._cab_id

    def apply_control(
        self, control: DrivingSystemControl, *, received_at: float | None = None
    ) -> None:
        if not isinstance(control, DrivingSystemControl):
            raise ValueError("driving system control is invalid")
        if control.control_mode is not None and control.control_mode not in self.CONTROL_MODES:
            raise ValueError(f"control_mode must be one of {self.CONTROL_MODES}")
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

        manual_fields = (
            control.mode is not None or control.direction is not None or acceleration is not None
        )
        requested_mode = control.control_mode
        if requested_mode is None:
            if manual_fields:
                requested_mode = "manual"
            elif control.target_speed is not None and self._control_mode == "speed":
                requested_mode = "speed"
            elif (
                control.target_position is not None or control.max_speed is not None
            ) and self._control_mode == "position":
                requested_mode = "position"
            else:
                requested_mode = self._control_mode

        if requested_mode == "manual":
            if any(
                value is not None
                for value in (control.target_speed, control.target_position, control.max_speed)
            ):
                raise ValueError("targets are only valid in speed or position control")
            self._manual_mode = control.mode if control.mode is not None else self._manual_mode
            self._manual_direction = (
                control.direction if control.direction is not None else self._manual_direction
            )
            self._manual_acceleration = (
                float(acceleration) if acceleration is not None else self._manual_acceleration
            )
            self._control_mode = "manual"
            self._target_speed = None
            self._target_position = None
            self._max_speed = None
            self._use_manual_handles()
            return

        if manual_fields:
            raise ValueError("manual handles cannot be changed in automatic control modes")

        if requested_mode == "speed":
            if control.target_position is not None or control.max_speed is not None:
                raise ValueError("position targets cannot be used in speed control")
            target_speed = control.target_speed
            if target_speed is None and self._control_mode == "speed":
                target_speed = self._target_speed
            if (
                target_speed is None
                or isinstance(target_speed, bool)
                or not is_finite(target_speed)
            ):
                raise ValueError("speed control requires a finite target_speed")
            self._control_mode = "speed"
            self._target_speed = float(target_speed)
            self._target_position = None
            self._max_speed = None
            self._set_effective_handles("brake", "off", 0.0)
            return

        if requested_mode == "position":
            if control.target_speed is not None:
                raise ValueError("target_speed cannot be used in position control")
            target_position = control.target_position
            max_speed = control.max_speed
            if self._control_mode == "position":
                target_position = (
                    self._target_position if target_position is None else target_position
                )
                max_speed = self._max_speed if max_speed is None else max_speed
            if (
                target_position is None
                or isinstance(target_position, bool)
                or not is_finite(target_position)
            ):
                raise ValueError("position control requires a finite target_position")
            if (
                max_speed is None
                or isinstance(max_speed, bool)
                or not is_positive_finite(max_speed)
            ):
                raise ValueError("position control requires a positive finite max_speed")
            self._control_mode = "position"
            self._target_speed = None
            self._target_position = float(target_position)
            self._max_speed = float(max_speed)
            self._set_effective_handles("brake", "off", 0.0)

    def read_state(self) -> DrivingSystemSnapshot:
        return DrivingSystemSnapshot(
            cab_id=self._cab_id,
            facing="forward" if self._facing == 1 else "backward",
            mode=self._mode,
            direction=self._direction,
            acceleration=self._acceleration,
            control_mode=self._control_mode,
            target_speed=self._target_speed,
            target_position=self._target_position,
            max_speed=self._max_speed,
            manual_mode=self._manual_mode,
            manual_direction=self._manual_direction,
            manual_acceleration=self._manual_acceleration,
        )

    def reset(self) -> None:
        self._mode = "off"
        self._direction = "off"
        self._acceleration = 0.0
        self._manual_mode = "off"
        self._manual_direction = "off"
        self._manual_acceleration = 0.0
        self._control_mode = "manual"
        self._target_speed = None
        self._target_position = None
        self._max_speed = None

    def step(self, dt: float, motion: TrainMotion) -> None:
        if self._control_mode == "manual":
            self._use_manual_handles()
            return

        if self._control_mode == "speed":
            desired_speed = self._target_speed
        else:
            assert self._target_position is not None and self._max_speed is not None
            distance = self._target_position - motion.position
            remaining = max(0.0, abs(distance) - self._POSITION_TOLERANCE)
            desired_speed = (
                math.copysign(
                    min(self._max_speed, math.sqrt(2.0 * motion.max_decel * remaining)),
                    distance,
                )
                if remaining > 0.0
                else 0.0
            )

        assert desired_speed is not None
        requested_accel = self._SPEED_GAIN * (desired_speed - motion.speed)
        requested_accel = max(
            -motion.max_decel,
            min(motion.max_traction_accel, requested_accel),
        )
        travel_sign = motion.speed * self._facing
        travel_direction = (
            "forward" if travel_sign > 0 else "backward" if travel_sign < 0 else "off"
        )
        if motion.speed != 0.0 and requested_accel * motion.speed < 0.0:
            effort = min(1.0, abs(requested_accel) / motion.max_decel)
            self._set_effective_handles("brake", travel_direction, effort)
            return
        if abs(requested_accel) < self._AUTOMATIC_ACCELERATION_DEADBAND:
            self._set_effective_handles("off", travel_direction, 0.0)
            return

        track_sign = 1 if requested_accel > 0.0 else -1
        cab_sign = track_sign * self._facing
        direction = "forward" if cab_sign > 0 else "backward"
        effort = min(1.0, abs(requested_accel) / motion.max_traction_accel)
        self._set_effective_handles("traction", direction, effort)

    def _use_manual_handles(self) -> None:
        self._set_effective_handles(
            self._manual_mode,
            self._manual_direction,
            self._manual_acceleration,
        )

    def _set_effective_handles(self, mode: str, direction: str, acceleration: float) -> None:
        self._mode = mode
        self._direction = direction
        self._acceleration = acceleration

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        intents: list[EquipmentIntent] = [
            EquipmentIntent(
                source=self._key,
                target="stcs_atp",
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
