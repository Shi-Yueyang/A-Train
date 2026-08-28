"""HTTP request and response validation models (§5.2).

Phase 1 defines the simulation-control request models (time mode, step) and the
``StatusResponse`` model. Phase 5 adds scenario loading, signals, train
controls, and BTM inject.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class StatusResponse(BaseModel):
    simulation_state: str
    simulation_time: float
    time_mode: str
    time_multiplier: float | None = None


class TimeModeRequest(BaseModel):
    mode: str = Field(description="One of REALTIME, SCALED, or MANUAL.")
    time_multiplier: float | None = Field(
        default=None,
        description="Positive finite multiplier; required for SCALED mode.",
    )


class StepRequest(BaseModel):
    delta: float = Field(description="Non-negative seconds to advance (MANUAL mode only).")
