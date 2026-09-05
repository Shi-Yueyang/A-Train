"""HTTP request and response validation models (§5.2).

Phase 1 defines the simulation-control request models (time mode, step) and the
``StatusResponse`` model. Phase 2 adds the train-facing request/response models
and the snapshot converters shared by the REST and WebSocket adapters. Numeric
range validation for train controls is performed by the train model at the
aggregate boundary, so the request models accept the raw values and report
errors as HTTP 400 via the core's ``CommandResult``.
"""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, Field

from ...domain.snapshots import TrainSnapshot
from ...simulation.snapshots import SimulationSnapshot


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


class TrainControlRequest(BaseModel):
    """A normalized train-control request from any configured cab (§3.3)."""

    cab_id: int = Field(description="The cab issuing the command; must be a configured cab.")
    drive_demand: float | None = Field(
        default=None,
        description="Signed drive lever in [-1.0, 1.0]: positive drives, negative decelerates.",
    )


class EquipmentSetRequest(BaseModel):
    """Body for the generic equipment-set endpoint (§3.5).

    The URL identifies one equipment instance by key. The train dispatcher
    validates what that instance requires.
    """

    command: str | None = Field(
        default=None,
        description="Named command: 'open'/'close' (door), 'activate'/'deactivate' (cab).",
    )
    cab_id: int | None = Field(default=None, description="Optional cab identity check.")
    data: str | None = Field(
        default=None, description="Base64-encoded opaque payload (btm, atp-api.md §3.2)."
    )


class TrainResponse(BaseModel):
    train_id: str
    cab_ids: list[int]
    speed: float
    acceleration: float
    position: float
    direction: str
    drive_demand: float
    equipment: list[object] = Field(default_factory=list)


class TrainsResponse(BaseModel):
    trains: list[TrainResponse]


class AtpConnectionResponse(BaseModel):
    """One cab's ATP channel state (Phase 3.1 observability, atp-api.md §6.2)."""

    train_id: str
    cab_id: int
    host: str
    port: int
    state: str = Field(description="IDLE / CONNECTING / READY / DISCONNECTED / STOPPED.")
    ready: bool = Field(description="True while the TCP channel is open.")


class AtpStatusResponse(BaseModel):
    connections: list[AtpConnectionResponse]


def _serialize_equipment(equipment: tuple[object, ...]) -> list[object]:
    """Serialize frozen equipment snapshots to JSON-safe dicts."""

    def _serialize(value: object) -> object:
        if dataclasses.is_dataclass(value):
            return dataclasses.asdict(value)
        if isinstance(value, tuple):
            return [_serialize(item) for item in value]
        if isinstance(value, list):
            return [_serialize(item) for item in value]
        return value

    return [_serialize(snapshot) for snapshot in equipment]


def train_snapshot_to_response(snap: TrainSnapshot) -> TrainResponse:
    return TrainResponse(
        train_id=snap.train_id,
        cab_ids=list(snap.cab_ids),
        speed=snap.speed,
        acceleration=snap.acceleration,
        position=snap.position,
        direction=snap.direction,
        drive_demand=snap.drive_demand,
        equipment=_serialize_equipment(snap.equipment),
    )


def _train_to_dict(snap: TrainSnapshot) -> dict:
    return {
        "train_id": snap.train_id,
        "cab_ids": list(snap.cab_ids),
        "speed": snap.speed,
        "acceleration": snap.acceleration,
        "position": snap.position,
        "direction": snap.direction,
        "drive_demand": snap.drive_demand,
        "equipment": _serialize_equipment(snap.equipment),
    }


def snapshot_to_dict(snap: SimulationSnapshot) -> dict:
    """Render a simulation snapshot as a JSON-serialisable dict (WebSocket)."""

    return {
        "simulation_state": snap.simulation_state.value,
        "simulation_time": snap.simulation_time,
        "time_mode": snap.time_mode.value,
        "time_multiplier": snap.time_multiplier,
        "trains": [_train_to_dict(t) for t in snap.trains],
    }
