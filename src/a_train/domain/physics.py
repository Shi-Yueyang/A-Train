"""Drive-demand, acceleration, speed, and position calculations (§3.4, §3.6).

Physics is a set of pure functions: they take a resolved acceleration, the
prior physical state, and a step duration, and return the resulting physical
state without side effects. Every train type applies the same rules, so the
stop-within-a-step clamping lives here. The model knows force and speed only.
Driving systems and ATP protection act on the train through resolved driver
intents (traction and motion-opposing brake), while the legacy signed drive
demand remains a force lever whose negative values never move a standing
train backward.

Reversible model: speed and force are signed along the single linear track.
When the net force brings the train across zero speed within a step, the
train stops at the resting point and the acceleration actually applied
during that step is zero (§3.6).
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
    *,
    speed: float = 0.0,
) -> float:
    """Resolve the single acceleration value for a step from the signed demand.

    Positive demand scales ``max_traction_accel``; negative demand scales
    ``max_decel`` as a decelerating force. Zero demand means zero acceleration.
    The returned value is signed: positive for drive, negative for
    deceleration, zero at rest with no effective command. A negative demand
    at standstill produces no force: the legacy lever is a brake-style
    deceleration, not rearward traction.
    """

    if drive_demand > 0.0:
        return limits.max_traction_accel * drive_demand
    if drive_demand < 0.0:
        if speed == 0.0:
            return 0.0
        return limits.max_decel * drive_demand
    return 0.0


def resolve_driver_acceleration(
    traction: float,
    brake: float,
    *,
    speed: float,
    limits: Limits,
) -> float:
    """Resolve the acceleration of engaged driving-system/ATP driver intents.

    ``traction`` is a signed effort in ``[-1.0, 1.0]`` scaled by the traction
    limit and works from standstill in either direction. ``brake`` is an
    effort in ``[0.0, 1.0]`` scaled by the deceleration limit that always
    opposes the current motion and produces no force at standstill.
    """

    acceleration = limits.max_traction_accel * traction
    if brake > 0.0 and speed != 0.0:
        acceleration -= math.copysign(limits.max_decel * brake, speed)
    return acceleration


def integrate(
    *,
    position: float,
    speed: float,
    acceleration: float,
    dt: float,
) -> tuple[float, float, float]:
    """Advance signed motion over ``dt`` and return the new state.

    Returns ``(new_position, new_speed, applied_acceleration)``. When the net
    force brings the train across zero speed within the step, the train stops
    at the resting point, the recorded acceleration is zero, and position
    advances only by the distance travelled before stopping (§3.6). A force
    from standstill starts moving the train in its own direction.
    """

    v1 = speed + acceleration * dt
    if speed * v1 >= 0.0:
        # No zero crossing: normal average-velocity integration. This also
        # covers starting from rest, where speed * v1 is 0.
        new_position = position + (speed + v1) * 0.5 * dt
        return new_position, v1, acceleration
    # The force carries the train through zero: stop exactly at the resting
    # point. t_stop = -v0 / a (a opposes v0, so t_stop > 0).
    t_stop = -speed / acceleration
    distance = (speed * 0.5) * t_stop
    return position + distance, 0.0, 0.0
