"""Cab driving-system equipment."""

from __future__ import annotations

import math

from ..controls import DriverControl, DrivingSystemControl, StcsAtpControl
from ..physics import is_finite, is_positive_finite
from ..snapshots import DrivingControlOption, DrivingSystemSnapshot
from .base import EquipmentIntent, TrainMotion
from .driving_law import DRIVING_LAWS, DrivingLaw, DrivingTargets


class DrivingSystem:
    """A cab's mode, direction, and acceleration handles."""

    type = "driving_system"
    MODES = ("traction", "off", "brake")
    DIRECTIONS = ("forward", "off", "backward")
    # The selectable control surface: manual plus every registered law,
    # published in the snapshot so clients never hard-code the list.
    CONTROL_OPTIONS = (DrivingControlOption("manual", "Manual"),) + tuple(
        DrivingControlOption(name, law_cls.label, law_cls.targets)
        for name, law_cls in DRIVING_LAWS.items()
    )
    CONTROL_MODES = tuple(option.value for option in CONTROL_OPTIONS)
    _TARGET_FIELDS = ("target_speed", "target_position", "max_speed")
    _TARGET_VALIDATORS = {
        "target_speed": is_finite,
        "target_position": is_finite,
        "max_speed": is_positive_finite,
    }
    _HANDLE_TO_TRACK_SIGN = {"forward": 1, "off": 0, "backward": -1}
    _AUTOMATIC_ACCELERATION_DEADBAND = 0.05

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
        self._law: DrivingLaw | None = None
        self._targets: dict[str, float] = {}

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
        command_targets = self._command_targets(control)
        requested_mode = control.control_mode
        if requested_mode is None:
            requested_mode = "manual" if manual_fields else self._control_mode

        if requested_mode == "manual":
            if command_targets:
                raise ValueError("targets are only valid with a control law")
            self._manual_mode = control.mode if control.mode is not None else self._manual_mode
            self._manual_direction = (
                control.direction if control.direction is not None else self._manual_direction
            )
            self._manual_acceleration = (
                float(acceleration) if acceleration is not None else self._manual_acceleration
            )
            self._control_mode = "manual"
            self._law = None
            self._targets = {}
            self._use_manual_handles()
            return

        if manual_fields:
            raise ValueError("manual handles cannot be changed while a control law is armed")

        law_cls = DRIVING_LAWS[requested_mode]
        for name in command_targets:
            if name not in law_cls.targets:
                raise ValueError(f"{name} cannot be used with control law {requested_mode!r}")
        updating = requested_mode == self._control_mode and self._law is not None
        targets: dict[str, float] = {}
        for name in law_cls.targets:
            value = command_targets.get(name)
            if value is None and updating:
                value = self._targets.get(name)
            if (
                value is None
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not self._TARGET_VALIDATORS[name](value)
            ):
                raise ValueError(f"control law {requested_mode!r} requires a valid {name}")
            targets[name] = float(value)
        self._control_mode = requested_mode
        self._law = law_cls()
        self._targets = targets
        self._set_effective_handles("brake", "off", 0.0)

    def _command_targets(self, control: DrivingSystemControl) -> dict[str, float]:
        return {
            name: value
            for name in self._TARGET_FIELDS
            if (value := getattr(control, name)) is not None
        }

    def read_state(self) -> DrivingSystemSnapshot:
        return DrivingSystemSnapshot(
            cab_id=self._cab_id,
            facing="forward" if self._facing == 1 else "backward",
            mode=self._mode,
            direction=self._direction,
            acceleration=self._acceleration,
            control_mode=self._control_mode,
            target_speed=self._targets.get("target_speed"),
            target_position=self._targets.get("target_position"),
            max_speed=self._targets.get("max_speed"),
            manual_mode=self._manual_mode,
            manual_direction=self._manual_direction,
            manual_acceleration=self._manual_acceleration,
            law=self._control_mode if self._law is not None else None,
            control_options=self.CONTROL_OPTIONS,
        )

    def reset(self) -> None:
        self._mode = "off"
        self._direction = "off"
        self._acceleration = 0.0
        self._manual_mode = "off"
        self._manual_direction = "off"
        self._manual_acceleration = 0.0
        self._control_mode = "manual"
        self._law = None
        self._targets = {}

    def step(self, dt: float, motion: TrainMotion) -> None:
        if self._law is None:
            self._use_manual_handles()
            return

        # The law plans the bounded acceleration request; the handle mapping
        # below is the driver-room actuator model and stays here (§3.5).
        requested_accel = self._law.step(dt, motion, DrivingTargets(**self._targets), self._facing)
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
