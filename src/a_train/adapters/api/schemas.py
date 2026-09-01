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

    Each equipment type uses a subset of the fields; the train dispatcher
    validates what is required: door -> command, cab -> cab_id + command,
    btm -> cab_id + data (base64), io -> direction + (bits | values).
    """

    command: str | None = Field(
        default=None,
        description="Named command: 'open'/'close' (door), 'activate'/'deactivate' (cab).",
    )
    cab_id: int | None = Field(default=None, description="Target cab (cab, btm).")
    data: str | None = Field(default=None, description="Base64-encoded opaque payload (btm, §4.6).")
    direction: str | None = Field(
        default=None,
        description="I/O direction: 'train_to_atp' or 'atp_to_train'.",
    )
    bits: str | None = Field(default=None, description="Raw bit string, bit 0 leftmost (io).")
    values: dict[str, bool] | None = Field(default=None, description="Named on/off values (io).")


class TrainResponse(BaseModel):
    train_id: str
    cab_ids: list[int]
    speed: float
    acceleration: float
    position: float
    direction: str
    drive_demand: float
    equipment: dict[str, object] = {}


class TrainsResponse(BaseModel):
    trains: list[TrainResponse]


def _serialize_equipment(equipment: dict[str, object]) -> dict[str, object]:
    """Serialize frozen equipment snapshots to JSON-safe dicts."""

    def _serialize(value: object) -> object:
        if dataclasses.is_dataclass(value):
            return dataclasses.asdict(value)
        if isinstance(value, tuple):
            return [_serialize(item) for item in value]
        if isinstance(value, list):
            return [_serialize(item) for item in value]
        return value

    return {key: _serialize(snapshot) for key, snapshot in equipment.items()}


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
