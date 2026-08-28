"""Application factory and process lifecycle ownership (§1.2, §2.6).

``create_app()`` returns the FastAPI application. Its lifespan assembles the
real production components -- the command queue, the ``SimulationCore``, the
``AtpManager``, and the snapshot subscribers -- starts the single
``run_loop()`` task, and tears them down in reverse order on shutdown.

Per §2.6, ``bootstrap.py`` is the only production module allowed to assemble
these components and start background tasks. Adapters submit commands to the
core; they never mutate world state directly.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from .adapters.api.app import create_app as build_app
from .adapters.atp.manager import AtpManager
from .simulation.commands import Command
from .simulation.core import SimulationCore
from .simulation.snapshots import SimulationSnapshot

if TYPE_CHECKING:
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: assemble production components and start background tasks.
    command_queue: asyncio.Queue[Command] = asyncio.Queue()
    snapshot_subscribers: list[asyncio.Queue[SimulationSnapshot]] = []

    core = SimulationCore(
        command_queue=command_queue,
        snapshot_subscribers=snapshot_subscribers,
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


def create_app() -> FastAPI:
    """Build the FastAPI application with the production lifespan wired in."""
    return build_app(lifespan)
