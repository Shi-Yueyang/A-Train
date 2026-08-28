"""Traction, braking, acceleration, speed, and position calculations (§3.4, §3.6).

Physics is a set of pure functions: they take prior physical state, the effective
control state, the configured acceleration limits, and a step duration, and
return the resulting physical state without side effects. Every train type
applies the same rules, so the stop-within-a-step clamping lives here.

Forward-only model: position never decreases and speed is never negative. When
braking would reverse the train within a step, the train stops at the resting
point and the acceleration actually applied during that step is zero (§3.6).
"""

from __future__ import annotations

import math
from typing import Protocol


def is_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def is_positive_finite(value: float) -> bool:
    return is_finite(value) and value > 0.0


def is_normalized(value: float) -> bool:
    """Return True when ``value`` is a finite number in the inclusive 0.0..1.0 range."""

    return is_finite(value) and 0.0 <= value <= 1.0


class ControlState(Protocol):
    """Structural shape of the control state physics reads."""

    emergency_brake: bool
    service_brake_demand: float
    traction_demand: float
    doors_closed: bool


class Limits(Protocol):
    """Structural shape of the per-train acceleration limits."""

    max_traction_accel: float
    max_service_brake_decel: float
    max_emergency_brake_decel: float


def resolve_acceleration(
    control: ControlState,
    limits: Limits,
) -> float:
    """Resolve the single acceleration value for a step using §3.4 priority.

    Emergency braking wins over service braking, which wins over traction, which
    only applies when every door is closed. The returned value is signed: positive
    for traction, negative for braking, zero at rest with no effective command.
    """

    if control.emergency_brake:
        return -limits.max_emergency_brake_decel
    if control.service_brake_demand > 0.0:
        return -limits.max_service_brake_decel * control.service_brake_demand
    if control.traction_demand > 0.0 and control.doors_closed:
        return limits.max_traction_accel * control.traction_demand
    return 0.0


def integrate_forward(
    *,
    position: float,
    speed: float,
    acceleration: float,
    dt: float,
) -> tuple[float, float, float]:
    """Advance forward-only motion over ``dt`` and return the new state.

    Returns ``(new_position, new_speed, applied_acceleration)``. When braking
    would bring the train to rest within the step, the train stops at the resting
    point, the recorded acceleration is zero, and position only increases by the
    distance travelled before stopping (§3.6).
    """

    v1 = speed + acceleration * dt
    if v1 >= 0.0:
        # Normal advance: average-velocity integration.
        new_position = position + (speed + v1) * 0.5 * dt
        return new_position, v1, acceleration
    # Braking would reverse the train within this step.
    if speed <= 0.0:
        # Already at rest; braking cannot create reverse movement.
        return position, 0.0, 0.0
    # Stop exactly at the resting point. t_stop = -v0 / a (a < 0, so t_stop > 0).
    t_stop = -speed / acceleration
    distance = (speed * 0.5) * t_stop
    return position + distance, 0.0, 0.0
