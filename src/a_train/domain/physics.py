"""Traction, braking, acceleration, speed, and position calculations (§3).

Phase 3 implements the physics integration driven by the fixed-step clock.
Physics owns physical state: ATP requests actions through the train brake
controller, the train model determines the physical result (§4.1).
"""

from __future__ import annotations
