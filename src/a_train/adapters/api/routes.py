"""REST request handlers that submit core commands (§5.2).

Handlers never mutate simulation objects directly; they read the core from
``app.state`` and submit commands, then return the resulting read-only
snapshot. Invalid input is reported by the core as a ``CommandResult`` and
mapped to HTTP 400; adapter exceptions never enter ``run_loop()`` (§2.6).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ...simulation.core import SimulationCore
from ...simulation.snapshots import SimulationSnapshot
from .schemas import StatusResponse, StepRequest, TimeModeRequest

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


@router.get("/status", response_model=StatusResponse)
async def get_status(core: SimulationCore = Depends(get_core)) -> StatusResponse:
    return _to_status(core.get_snapshot())


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
