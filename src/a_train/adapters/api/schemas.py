"""HTTP request and response validation models (§5.2).

Phase 1 defines the simulation-control request models (time mode, step) and the
``StatusResponse`` model. Phase 2 adds the train-facing request/response models
and the snapshot converters shared by the REST and WebSocket adapters. Numeric
range validation for train controls is performed by the train model at the
aggregate boundary, so the request models accept the raw values and report
errors as HTTP 400 via the core's ``CommandResult``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ...domain.snapshots import TrainSnapshot
from ...simulation.snapshots import SimulationSnapshot, TriggeredEventRecord


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
    """A normalized train-control request routed to the active cab (§3.3)."""

    cab_id: int = Field(description="The cab issuing the command; must be the active cab.")
    traction_demand: float | None = Field(
        default=None, description="Proportion of max traction in [0.0, 1.0]."
    )
    service_brake_demand: float | None = Field(
        default=None, description="Proportion of max service brake in [0.0, 1.0]."
    )
    emergency_brake: bool | None = Field(
        default=None,
        description="True applies the latch, False requests release, None is unchanged.",
    )
    door: str | None = Field(default=None, description="'open' or 'close'.")


class CabSnapshotModel(BaseModel):
    cab_id: int
    active: bool = False


class DoorSnapshotModel(BaseModel):
    state: str = "closed"


class BtmSnapshotModel(BaseModel):
    cab_id: int = 0
    pending: bool = False
    payload_b64: str | None = None
    received_count: int = 0


class IoSnapshotModel(BaseModel):
    train_to_atp: str = ""
    atp_to_train: str = ""
    values: dict[str, bool] = {}


class TrainResponse(BaseModel):
    train_id: str
    cab_ids: list[int]
    active_cab: int
    speed: float
    acceleration: float
    position: float
    direction: str
    traction_demand: float
    service_brake_demand: float
    emergency_brake: bool
    door_state: str
    cab: list[CabSnapshotModel]
    doors: DoorSnapshotModel | None = None
    btm: list[BtmSnapshotModel]
    io: IoSnapshotModel | None = None


class TrainsResponse(BaseModel):
    trains: list[TrainResponse]


def train_snapshot_to_response(snap: TrainSnapshot) -> TrainResponse:
    return TrainResponse(
        train_id=snap.train_id,
        cab_ids=list(snap.cab_ids),
        active_cab=snap.active_cab,
        speed=snap.speed,
        acceleration=snap.acceleration,
        position=snap.position,
        direction=snap.direction,
        traction_demand=snap.traction_demand,
        service_brake_demand=snap.service_brake_demand,
        emergency_brake=snap.emergency_brake,
        door_state=snap.door_state,
        cab=[CabSnapshotModel(cab_id=c.cab_id, active=c.active) for c in snap.cab],
        doors=DoorSnapshotModel(state=snap.doors.state) if snap.doors else None,
        btm=[
            BtmSnapshotModel(
                cab_id=b.cab_id,
                pending=b.pending,
                payload_b64=b.payload_b64,
                received_count=b.received_count,
            )
            for b in snap.btm
        ],
        io=(
            IoSnapshotModel(
                train_to_atp=snap.io.train_to_atp,
                atp_to_train=snap.io.atp_to_train,
                values={name: value for name, value in snap.io.values},
            )
            if snap.io
            else None
        ),
    )


def _train_to_dict(snap: TrainSnapshot) -> dict:
    return {
        "train_id": snap.train_id,
        "cab_ids": list(snap.cab_ids),
        "active_cab": snap.active_cab,
        "speed": snap.speed,
        "acceleration": snap.acceleration,
        "position": snap.position,
        "direction": snap.direction,
        "traction_demand": snap.traction_demand,
        "service_brake_demand": snap.service_brake_demand,
        "emergency_brake": snap.emergency_brake,
        "door_state": snap.door_state,
        "cab": [{"cab_id": c.cab_id, "active": c.active} for c in snap.cab],
        "doors": {"state": snap.doors.state} if snap.doors else None,
        "btm": [
            {
                "cab_id": b.cab_id,
                "pending": b.pending,
                "payload_b64": b.payload_b64,
                "received_count": b.received_count,
            }
            for b in snap.btm
        ],
        "io": (
            {
                "train_to_atp": snap.io.train_to_atp,
                "atp_to_train": snap.io.atp_to_train,
                "values": {name: value for name, value in snap.io.values},
            }
            if snap.io
            else None
        ),
    }


def _event_to_dict(event: TriggeredEventRecord) -> dict:
    return {"event_id": event.event_id, "at": event.at, "type": event.type}


def snapshot_to_dict(snap: SimulationSnapshot) -> dict:
    """Render a simulation snapshot as a JSON-serialisable dict (WebSocket)."""

    return {
        "simulation_state": snap.simulation_state.value,
        "simulation_time": snap.simulation_time,
        "time_mode": snap.time_mode.value,
        "time_multiplier": snap.time_multiplier,
        "trains": [_train_to_dict(t) for t in snap.trains],
        "recent_events": [_event_to_dict(e) for e in snap.recent_events],
    }
