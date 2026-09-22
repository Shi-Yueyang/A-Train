"""Application factory and process lifecycle ownership (§1.2, §2.6).

``create_app()`` returns the FastAPI application. Its lifespan assembles the
real production components -- the command queue, the ``SimulationCore`` (with
the configured trains), the ``AtpManager``, and the snapshot subscribers --
starts the single ``run_loop()`` task, and tears them down in reverse order on
shutdown.

Per §2.6, ``bootstrap.py`` is the only production module allowed to assemble
these components and start background tasks. Adapters submit commands to the
core; they never mutate world state directly.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from .adapters.api.app import create_app as build_app
from .adapters.atp.manager import AtpEndpoint, AtpManager
from .config import (
    ATP_ENDPOINTS_ENV,
    TRAIN_CONFIG_ENV,
    decode_env,
    decode_train_config,
)
from .domain.train import TrainConfig
from .simulation.commands import Command
from .simulation.core import SimulationCore
from .simulation.snapshots import SimulationSnapshot

if TYPE_CHECKING:
    pass


# A small default consist so a freshly started server has a world to show. Real
# deployments pass their own configuration through ``create_app``.
DEFAULT_TRAIN_CONFIGS: tuple[TrainConfig, ...] = (
    TrainConfig(
        train_id="TRAIN001",
        cab_ids=(1, 2),
        initial_active_cab=1,
        max_traction_accel=1.5,
        max_decel=2.0,
        initial_position=0.0,
    ),
)


@asynccontextmanager
async def lifespan(
    app: FastAPI,
    train_configs: Sequence[TrainConfig] | None = None,
    atp_endpoints: Sequence[AtpEndpoint] | None = None,
    *,
    atp_retry_delay: float = 1.0,
):
    # Startup: assemble production components and start background tasks.
    command_queue: asyncio.Queue[Command] = asyncio.Queue()
    snapshot_subscribers: list[asyncio.Queue[SimulationSnapshot]] = []

    if train_configs is not None:
        configs = train_configs
    elif os.environ.get(TRAIN_CONFIG_ENV):
        configs = (decode_train_config(os.environ[TRAIN_CONFIG_ENV]),)
    else:
        configs = DEFAULT_TRAIN_CONFIGS
    core = SimulationCore(
        command_queue=command_queue,
        snapshot_subscribers=snapshot_subscribers,
        train_configs=configs,
    )
    core_task = asyncio.create_task(core.run_loop(), name="simulation-core")

    endpoints = _endpoints_from_environment() if atp_endpoints is None else tuple(atp_endpoints)
    atp_manager = AtpManager(
        core,
        endpoints=endpoints,
        retry_delay=atp_retry_delay,
    )
    await atp_manager.start()

    app.state.core = core
    app.state.command_queue = command_queue
    app.state.snapshot_subscribers = snapshot_subscribers
    app.state.atp_manager = atp_manager

    try:
        yield
    finally:
        # Shutdown: reverse-order teardown.
        await atp_manager.stop()
        core_task.cancel()
        try:
            await core_task
        except asyncio.CancelledError:
            pass


def _endpoints_from_environment() -> tuple[AtpEndpoint, ...]:
    """Decode ATP endpoints set by the ``run`` command (atp-api.md §6, config.py)."""

    entries = decode_env(os.environ.get(ATP_ENDPOINTS_ENV, ""))
    return tuple(AtpEndpoint(e["cab_id"], e["host"], e["port"]) for e in entries)


def create_app(
    train_configs: Sequence[TrainConfig] | None = None,
    atp_endpoints: Sequence[AtpEndpoint] | None = None,
    *,
    atp_retry_delay: float = 1.0,
) -> FastAPI:
    """Build the FastAPI application with the production lifespan wired in.

    ``atp_endpoints=None`` (the uvicorn factory default) loads the endpoints
    from the ``A_TRAIN_ATP_ENDPOINTS`` environment variable; pass a sequence
    (including the empty tuple) to configure them explicitly.
    """

    configs = train_configs
    train_configs_capture: Sequence[TrainConfig] | None = configs
    endpoints_capture: Sequence[AtpEndpoint] | None = (
        None if atp_endpoints is None else tuple(atp_endpoints)
    )

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        async with lifespan(
            app,
            train_configs=train_configs_capture,
            atp_endpoints=endpoints_capture,
            atp_retry_delay=atp_retry_delay,
        ):
            yield

    return build_app(_lifespan)
