"""REST request handlers that submit core commands (§5.2).

Handlers never mutate simulation objects directly; they read the core from
``app.state`` and submit commands, then return the resulting read-only
snapshot. Invalid input is reported by the core as a ``CommandResult`` and
mapped to HTTP 400; adapter exceptions never enter ``run_loop()`` (§2.6).
"""

from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Request

from ...domain.train import EquipmentSet, TrainControl
from ...simulation.commands import EquipmentCommand, TrainControlCommand
from ...simulation.core import SimulationCore
from ...simulation.snapshots import SimulationSnapshot
from .schemas import (
    AtpConnectionResponse,
    AtpStatusResponse,
    EquipmentSetRequest,
    StatusResponse,
    StepRequest,
    TimeModeRequest,
    TrainControlRequest,
    TrainResponse,
    TrainsResponse,
    train_snapshot_to_response,
)

router = APIRouter(prefix="/api")


def get_core(request: Request) -> SimulationCore:
    return request.app.state.core


def _to_status(snap: SimulationSnapshot) -> StatusResponse:
    return StatusResponse(
        simulation_state=snap.simulation_state.value,
        simulation_time=snap.simulation_time,
        time_mode=snap.time_mode.value,
        time_multiplier=snap.time_multiplier,
    )


def _raise_on_error(result) -> None:
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error)


def _find_train(snap: SimulationSnapshot, train_id: str):
    for train in snap.trains:
        if train.train_id == train_id:
            return train
    return None


@router.get("/status", response_model=StatusResponse)
async def get_status(core: SimulationCore = Depends(get_core)) -> StatusResponse:
    return _to_status(core.get_snapshot())


@router.get("/atp/status", response_model=AtpStatusResponse)
async def get_atp_status(request: Request) -> AtpStatusResponse:
    """Report each configured cab's ATP channel state (atp-api.md §6.2, Phase 3.1)."""

    manager = request.app.state.atp_manager
    return AtpStatusResponse(
        connections=[
            AtpConnectionResponse(
                train_id=client.train_id,
                cab_id=client.cab_id,
                host=client.host,
                port=client.port,
                state=client.state.value,
                ready=client.ready,
            )
            for client in manager.clients
        ]
    )


@router.post("/simulation/start", response_model=StatusResponse)
async def start_simulation(core: SimulationCore = Depends(get_core)) -> StatusResponse:
    _raise_on_error(await core.run())
    return _to_status(core.get_snapshot())


@router.post("/simulation/pause", response_model=StatusResponse)
async def pause_simulation(core: SimulationCore = Depends(get_core)) -> StatusResponse:
    _raise_on_error(await core.pause())
    return _to_status(core.get_snapshot())


@router.post("/simulation/reset", response_model=StatusResponse)
async def reset_simulation(core: SimulationCore = Depends(get_core)) -> StatusResponse:
    _raise_on_error(await core.reset())
    return _to_status(core.get_snapshot())


@router.post("/simulation/time-mode", response_model=StatusResponse)
async def set_time_mode(
    body: TimeModeRequest,
    core: SimulationCore = Depends(get_core),
) -> StatusResponse:
    _raise_on_error(await core.set_time_mode(body.mode, body.time_multiplier))
    return _to_status(core.get_snapshot())


@router.post("/simulation/step", response_model=StatusResponse)
async def step_simulation(
    body: StepRequest,
    core: SimulationCore = Depends(get_core),
) -> StatusResponse:
    _raise_on_error(await core.step(body.delta))
    return _to_status(core.get_snapshot())


@router.get("/trains", response_model=TrainsResponse)
async def list_trains(core: SimulationCore = Depends(get_core)) -> TrainsResponse:
    snap = core.get_snapshot()
    return TrainsResponse(trains=[train_snapshot_to_response(t) for t in snap.trains])


@router.get("/trains/{train_id}", response_model=TrainResponse)
async def get_train(
    train_id: str,
    core: SimulationCore = Depends(get_core),
) -> TrainResponse:
    train = _find_train(core.get_snapshot(), train_id)
    if train is None:
        raise HTTPException(status_code=404, detail=f"unknown train: {train_id}")
    return train_snapshot_to_response(train)


@router.post("/trains/{train_id}/commands", response_model=TrainResponse)
async def control_train(
    train_id: str,
    body: TrainControlRequest,
    core: SimulationCore = Depends(get_core),
) -> TrainResponse:
    payload = TrainControl(
        cab_id=body.cab_id,
        drive_demand=body.drive_demand,
    )
    result = await core.submit_command(TrainControlCommand(train_id=train_id, payload=payload))
    _raise_on_error(result)
    train = _find_train(core.get_snapshot(), train_id)
    if train is None:
        raise HTTPException(status_code=404, detail=f"unknown train: {train_id}")
    return train_snapshot_to_response(train)


@router.post("/trains/{train_id}/equipment/{key}", response_model=TrainResponse)
async def set_equipment(
    train_id: str,
    key: str,
    body: EquipmentSetRequest,
    core: SimulationCore = Depends(get_core),
) -> TrainResponse:
    """Set train-facing equipment state through the core, applied immediately."""

    data = None
    if body.data is not None:
        try:
            data = base64.b64decode(body.data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="data must be valid base64") from None
    payload = EquipmentSet(
        key=key,
        command=body.command,
        cab_id=body.cab_id,
        data=data,
    )
    result = await core.submit_command(EquipmentCommand(train_id=train_id, payload=payload))
    _raise_on_error(result)
    train = _find_train(core.get_snapshot(), train_id)
    if train is None:
        raise HTTPException(status_code=404, detail=f"unknown train: {train_id}")
    return train_snapshot_to_response(train)
