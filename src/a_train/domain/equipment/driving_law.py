"""Replaceable speed-planning control laws for the driving system (§3.5).

A law turns its declared targets and the current train motion into one
bounded, signed acceleration request in track terms. The driving system
keeps the actuator half: it maps that request into the physical traction/
brake and direction handles and asserts the resulting train intent. Each
law declares its operator-facing ``label`` and the ``DrivingTargets``
fields it consumes, so the equipment validates commands and clients build
their control lists from published metadata. A law is freshly instantiated
whenever a control law is armed or switched; ``reset()`` clears any
per-instance planner state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from .base import TrainMotion


@dataclass(frozen=True)
class DrivingTargets:
    """Target values for the armed control law; unused fields stay ``None``."""

    target_speed: float | None = None
    target_position: float | None = None
    max_speed: float | None = None


class DrivingLaw(Protocol):
    """Stateful speed planner armed as the driving system's automatic control."""

    label: str
    targets: tuple[str, ...]

    def step(self, dt: float, motion: TrainMotion, targets: DrivingTargets, facing: int) -> float:
        """Return the bounded signed acceleration request in track terms."""
        ...

    def reset(self) -> None: ...


def _track_acceleration(desired_speed: float, motion: TrainMotion, gain: float) -> float:
    """Proportional speed tracking, clamped to the train's acceleration limits."""

    requested_accel = gain * (desired_speed - motion.speed)
    return max(-motion.max_decel, min(motion.max_traction_accel, requested_accel))


class HoldSpeedLaw:
    """Track a signed speed setpoint.

    A rearward (cab-relative) setpoint drives the train rearward — unlike
    the position law, reversing here is the operator's explicit command,
    not an unattended controller maneuver.
    """

    label = "Hold speed"
    targets = ("target_speed",)

    _SPEED_GAIN = 4.0

    def step(self, dt: float, motion: TrainMotion, targets: DrivingTargets, facing: int) -> float:
        assert targets.target_speed is not None
        return _track_acceleration(targets.target_speed, motion, self._SPEED_GAIN)

    def reset(self) -> None:
        # The hold law is stateless.
        return None


class StoppingEnvelopeLaw:
    """Drive to an absolute track position ahead of the cab facing.

    Pursues only targets ahead of the cab facing, capped by ``max_speed``
    and slowed by ``v = sqrt(2 * max_decel * remaining)``; a behind or
    overshot target demands zero speed and is never reversed to.
    """

    label = "Stopping envelope"
    targets = ("target_position", "max_speed")

    _SPEED_GAIN = 4.0
    _POSITION_TOLERANCE = 0.05

    def step(self, dt: float, motion: TrainMotion, targets: DrivingTargets, facing: int) -> float:
        assert targets.target_position is not None and targets.max_speed is not None
        ahead = (targets.target_position - motion.position) * facing
        remaining = max(0.0, ahead - self._POSITION_TOLERANCE)
        desired_speed = (
            facing * min(targets.max_speed, math.sqrt(2.0 * motion.max_decel * remaining))
            if remaining > 0.0
            else 0.0
        )
        return _track_acceleration(desired_speed, motion, self._SPEED_GAIN)

    def reset(self) -> None:
        # The envelope law is stateless.
        return None


DRIVING_LAWS: dict[str, type[DrivingLaw]] = {
    "hold_speed": HoldSpeedLaw,
    "stopping_envelope": StoppingEnvelopeLaw,
}
