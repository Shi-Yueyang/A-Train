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

from pydantic import BaseModel, Field, StrictBool

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

    cab_id: int | None = Field(
        default=None,
        description=(
            "Configured cab for cab activation/key controls; optional for train-wide drive demand."
        ),
    )
    drive_demand: float | None = Field(
        default=None,
        description="Signed drive lever in [-1.0, 1.0]: positive drives, negative decelerates.",
    )
    active: bool | None = Field(
        default=None,
        description="Sets the cab's native activation flag; omitted leaves it unchanged.",
    )
    key: bool | None = Field(
        default=None,
        description="Sets whether the key is inserted in this cab; omitted leaves it unchanged.",
    )


class EquipmentSetRequest(BaseModel):
    """Body for the generic equipment-set endpoint (§3.5).

    The URL identifies one equipment instance by key. The train dispatcher
    validates what that instance requires.
    """

    command: str | None = Field(
        default=None,
        description="Named command: 'open'/'close' (door).",
    )
    cab_id: int | None = Field(default=None, description="Optional cab identity check.")
    data: str | None = Field(
        default=None, description="Base64-encoded opaque payload (btm, atp-api.md §3.2)."
    )
    telegram: dict | None = Field(
        default=None,
        description=(
            "Complete JSON CTCS balise telegram encoded exactly as provided by the "
            "caller (btm keys only; docs/btm-telegram.md). Mutually exclusive with 'data'."
        ),
    )
    mode: str | None = Field(
        default=None,
        description="Driving-system mode handle: 'traction', 'off', or 'brake'.",
    )
    direction: str | None = Field(
        default=None,
        description="Driving-system direction handle: 'forward', 'off', or 'backward'.",
    )
    acceleration: float | None = Field(
        default=None,
        description="Driving-system acceleration handle, continuous effort in [0.0, 1.0].",
    )
    control_mode: str | None = Field(
        default=None,
        description="Driving-system control: 'manual' or a registered control law "
        "name (see the published control_options).",
    )
    target_speed: float | None = Field(default=None, description="Signed speed target in m/s.")
    target_position: float | None = Field(default=None, description="Position target in metres.")
    max_speed: float | None = Field(default=None, description="Positive speed cap in m/s.")
    speed_gain: float | None = Field(
        default=None,
        description="Positive finite proportional speed-tracking gain in 1/s.",
    )
    train_out_signal: str | None = Field(
        default=None,
        description="STCS ATP train-to-ATP bit assertion ('0'/'1' string); unblocked "
        "derived feedback bits are re-established from real train state.",
    )
    train_in_signals: dict[str, StrictBool] | None = Field(
        default=None,
        description="Sparse STCS ATP-to-train signal updates by signal name.",
    )
    train_out_signals: dict[str, StrictBool] | None = Field(
        default=None,
        description="Sparse STCS train-to-ATP signal updates by signal name.",
    )
    system_switch: str | None = Field(
        default=None,
        description="Switch box position: 'c2', 'auto', or 'cbtc'.",
    )
    c2_authorized: StrictBool | None = Field(
        default=None, description="Switch box C2 authorization state."
    )
    cbtc_authorized: StrictBool | None = Field(
        default=None, description="Switch box CBTC authorization state."
    )
    block: list[str] | None = Field(
        default=None,
        description="STCS ATP: freeze the simulator's derivation of these derived "
        "train-out signals; the bits stay stale and become manually assertable.",
    )
    unblock: list[str] | None = Field(
        default=None,
        description="STCS ATP: resume derivation of these signals (self-heals on the "
        "next recompute).",
    )


class CabResponse(BaseModel):
    cab_id: int
    active: bool
    key: bool
    facing: str = Field(
        description="Cab track facing: 'forward' drives toward increasing position."
    )


class LinkCutInput(BaseModel):
    """One physical wire to cut: source equipment/cab to target instance."""

    source: str = Field(description="Equipment key or 'cab_<id>' broadcast source.")
    target: str = Field(description="Equipment key or 'train'.")


class LinkCutsReplaceRequest(BaseModel):
    """Whole cut set; an empty list restores every wire (§3.7)."""

    cuts: list[LinkCutInput] = Field(default_factory=list)


class TrainResponse(BaseModel):
    train_id: str
    cabs: list[CabResponse]
    speed: float
    acceleration: float
    position: float
    direction: str
    drive_demand: float
    equipment: list[object] = Field(default_factory=list)
    link_cuts: list[object] = Field(default_factory=list)


class EncodedPayloadResponse(BaseModel):
    hex: str
    b64: str


class EquipmentSetResponse(TrainResponse):
    encoded: EncodedPayloadResponse | None = None


class TrainsResponse(BaseModel):
    trains: list[TrainResponse]


class AtpConnectionResponse(BaseModel):
    """One A-Train ATP listener state (atp-api.md §6.2)."""

    host: str
    port: int
    state: str = Field(
        description="READY while the listener accepts connections; otherwise STOPPED."
    )
    ready: bool = Field(description="True while the TCP listener is active.")
    active_peers: int = Field(description="Number of currently connected ATP clients.")


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


def _cabs_to_response(snap: TrainSnapshot) -> list[CabResponse]:
    return [
        CabResponse(
            cab_id=cab.cab_id,
            active=cab.active,
            key=cab.key,
            facing=cab.facing,
        )
        for cab in snap.cabs
    ]


def _link_cuts_to_list(snap: TrainSnapshot) -> list[object]:
    return [dataclasses.asdict(cut) for cut in snap.link_cuts]


def train_snapshot_to_response(snap: TrainSnapshot) -> TrainResponse:
    return TrainResponse(
        train_id=snap.train_id,
        cabs=_cabs_to_response(snap),
        speed=snap.speed,
        acceleration=snap.acceleration,
        position=snap.position,
        direction=snap.direction,
        drive_demand=snap.drive_demand,
        equipment=_serialize_equipment(snap.equipment),
        link_cuts=_link_cuts_to_list(snap),
    )


def _train_to_dict(snap: TrainSnapshot) -> dict:
    return {
        "train_id": snap.train_id,
        "cabs": [dataclasses.asdict(cab) for cab in snap.cabs],
        "speed": snap.speed,
        "acceleration": snap.acceleration,
        "position": snap.position,
        "direction": snap.direction,
        "drive_demand": snap.drive_demand,
        "equipment": _serialize_equipment(snap.equipment),
        "link_cuts": _link_cuts_to_list(snap),
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
