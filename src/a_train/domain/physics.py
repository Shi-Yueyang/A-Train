"""Drive-demand, acceleration, speed, and position calculations (§3.4, §3.6).

Physics is a set of pure functions: they take a drive demand, the prior
physical state, the configured acceleration limits, and a step duration, and
return the resulting physical state without side effects. Every train type
applies the same rules, so the stop-within-a-step clamping lives here. The
model knows force and speed only; a negative demand is a decelerating force,
not a brake concept. No train-facing equipment affects the physical
integration in this version.

Forward-only model: position never decreases and speed is never negative. When
deceleration would reverse the train within a step, the train stops at the
resting point and the acceleration actually applied during that step is zero
(§3.6).
"""

from __future__ import annotations

import math
from typing import Protocol


def is_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def is_positive_finite(value: float) -> bool:
    return is_finite(value) and value > 0.0


def is_normalized(value: float) -> bool:
    """Return True when ``value`` is a finite number in the inclusive -1.0..1.0 range."""

    return is_finite(value) and -1.0 <= value <= 1.0


class Limits(Protocol):
    """Structural shape of the per-train acceleration limits."""

    max_traction_accel: float
    max_decel: float


def resolve_acceleration(
    drive_demand: float,
    limits: Limits,
) -> float:
    """Resolve the single acceleration value for a step from the signed demand.

    Positive demand scales ``max_traction_accel``; negative demand scales
    ``max_decel`` as a decelerating force. Zero demand means zero acceleration.
    The returned value is signed: positive for drive, negative for
    deceleration, zero at rest with no effective command.
    """

    if drive_demand > 0.0:
        return limits.max_traction_accel * drive_demand
    if drive_demand < 0.0:
        return limits.max_decel * drive_demand
    return 0.0


def integrate_forward(
    *,
    position: float,
    speed: float,
    acceleration: float,
    dt: float,
) -> tuple[float, float, float]:
    """Advance forward-only motion over ``dt`` and return the new state.

    Returns ``(new_position, new_speed, applied_acceleration)``. When
    deceleration would bring the train to rest within the step, the train stops
    at the resting point, the recorded acceleration is zero, and position only
    increases by the distance travelled before stopping (§3.6).
    """

    v1 = speed + acceleration * dt
    if v1 >= 0.0:
        # Normal advance: average-velocity integration.
        new_position = position + (speed + v1) * 0.5 * dt
        return new_position, v1, acceleration
    # Deceleration would reverse the train within this step.
    if speed <= 0.0:
        # Already at rest; a decelerating force cannot create reverse movement.
        return position, 0.0, 0.0
    # Stop exactly at the resting point. t_stop = -v0 / a (a < 0, so t_stop > 0).
    t_stop = -speed / acceleration
    distance = (speed * 0.5) * t_stop
    return position + distance, 0.0, 0.0
