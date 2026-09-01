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
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from .adapters.api.app import create_app as build_app
from .adapters.atp.manager import AtpManager
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
async def lifespan(app: FastAPI, train_configs: Sequence[TrainConfig] | None = None):
    # Startup: assemble production components and start background tasks.
    command_queue: asyncio.Queue[Command] = asyncio.Queue()
    snapshot_subscribers: list[asyncio.Queue[SimulationSnapshot]] = []

    configs = train_configs if train_configs is not None else DEFAULT_TRAIN_CONFIGS
    core = SimulationCore(
        command_queue=command_queue,
        snapshot_subscribers=snapshot_subscribers,
        train_configs=configs,
    )
    core_task = asyncio.create_task(core.run_loop(), name="simulation-core")

    atp_manager = AtpManager(command_queue=command_queue)
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


def create_app(train_configs: Sequence[TrainConfig] | None = None) -> FastAPI:
    """Build the FastAPI application with the production lifespan wired in."""

    configs = train_configs
    train_configs_capture: Sequence[TrainConfig] | None = configs

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        async with lifespan(app, train_configs=train_configs_capture):
            yield

    return build_app(_lifespan)
