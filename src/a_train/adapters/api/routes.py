"""REST request handlers that submit core commands (§5.2).

Handlers never mutate simulation objects directly; they read the core from
``app.state`` and submit commands, then return the resulting read-only
snapshot. Invalid input is reported by the core as a ``CommandResult`` and
mapped to HTTP 400; adapter exceptions never enter ``run_loop()`` (§2.6).
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ...domain.train import EquipmentControlRequest, TrainControl
from ...simulation.commands import (
    Command,
    EquipmentCommand,
    LinkCutsCommand,
    TrainControlCommand,
)
from ...simulation.core import SimulationCore
from ...simulation.snapshots import SimulationSnapshot
from .schemas import (
    AtpConnectionResponse,
    AtpStatusResponse,
    EquipmentSetRequest,
    LinkCutInput,
    LinkCutsReplaceRequest,
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


async def _train_command_response(
    core: SimulationCore, train_id: str, command: Command
) -> TrainResponse:
    """Submit a train-scoped command and respond with the resulting snapshot."""

    result = await core.submit_command(command)
    _raise_on_error(result)
    train = _find_train(core.get_snapshot(), train_id)
    if train is None:
        raise HTTPException(status_code=404, detail=f"unknown train: {train_id}")
    return train_snapshot_to_response(train)


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
    """Report each configured ATP peer's channel state (atp-api.md §6.2)."""

    manager = request.app.state.atp_manager
    return AtpStatusResponse(
        connections=[
            AtpConnectionResponse(
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
        active=body.active,
        key=body.key,
    )
    return await _train_command_response(
        core, train_id, TrainControlCommand(train_id=train_id, payload=payload)
    )


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
    payload = EquipmentControlRequest(
        key=key,
        command=body.command,
        cab_id=body.cab_id,
        data=data,
        mode=body.mode,
        direction=body.direction,
        acceleration=body.acceleration,
        train_out_signal=body.train_out_signal,
        system_switch=body.system_switch,
        block=tuple(body.block) if body.block is not None else None,
        unblock=tuple(body.unblock) if body.unblock is not None else None,
    )
    return await _train_command_response(
        core, train_id, EquipmentCommand(train_id=train_id, payload=payload)
    )


# -- Physical link cuts (§3.7) --------------------------------------------------


def _cut_pairs(cuts: Iterable[LinkCutInput]) -> tuple[tuple[str, str], ...]:
    return tuple((cut.source, cut.target) for cut in cuts)


@router.put("/trains/{train_id}/links", response_model=TrainResponse)
async def replace_link_cuts(
    train_id: str,
    body: LinkCutsReplaceRequest,
    core: SimulationCore = Depends(get_core),
) -> TrainResponse:
    """Replace the whole cut set; an empty list restores every wire."""

    return await _train_command_response(
        core,
        train_id,
        LinkCutsCommand(train_id=train_id, action="replace", cuts=_cut_pairs(body.cuts)),
    )


@router.post("/trains/{train_id}/links/cut", response_model=TrainResponse)
async def cut_link(
    train_id: str,
    body: LinkCutInput,
    core: SimulationCore = Depends(get_core),
) -> TrainResponse:
    """Cut one physical wire; already-cut is a no-op (§3.7)."""

    return await _train_command_response(
        core,
        train_id,
        LinkCutsCommand(train_id=train_id, action="add", cuts=_cut_pairs([body])),
    )


@router.delete("/trains/{train_id}/links/cut", response_model=TrainResponse)
async def restore_link(
    train_id: str,
    source: str = Query(description="Cut source: equipment key or 'cab_<id>'."),
    target: str = Query(description="Cut target: equipment key or 'train'."),
    core: SimulationCore = Depends(get_core),
) -> TrainResponse:
    """Restore one physical wire; cutting no such pair is a no-op (§3.7)."""

    return await _train_command_response(
        core,
        train_id,
        LinkCutsCommand(train_id=train_id, action="remove", cuts=((source, target),)),
    )
