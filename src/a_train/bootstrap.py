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
from .config import TRAIN_CONFIG_ENV, ConfigError, decode_config
from .domain.train import TrainConfig
from .simulation.commands import Command
from .simulation.core import SimulationCore
from .simulation.snapshots import SimulationSnapshot

if TYPE_CHECKING:
    from typing import Any


@asynccontextmanager
async def lifespan(
    app: FastAPI,
    train_configs: Sequence[TrainConfig] | None = None,
    atp_endpoints: Sequence[AtpEndpoint] | None = None,
):
    # Startup: assemble production components and start background tasks.
    command_queue: asyncio.Queue[Command] = asyncio.Queue()
    snapshot_subscribers: list[asyncio.Queue[SimulationSnapshot]] = []

    env_config: tuple[TrainConfig | None, list[dict[str, Any]]] | None = None
    if train_configs is None or atp_endpoints is None:
        env_config = _config_from_environment()

    if train_configs is not None:
        configs = train_configs
    elif env_config is not None and env_config[0] is not None:
        configs = (env_config[0],)
    else:
        raise ConfigError("train configuration is required; start with --train-config FILE")
    core = SimulationCore(
        command_queue=command_queue,
        snapshot_subscribers=snapshot_subscribers,
        train_configs=configs,
    )
    core_task = asyncio.create_task(core.run_loop(), name="simulation-core")

    if atp_endpoints is None:
        env_endpoints = env_config[1] if env_config is not None else []
        endpoints = tuple(AtpEndpoint(entry["host"], entry["port"]) for entry in env_endpoints)
    else:
        endpoints = tuple(atp_endpoints)
    atp_manager = AtpManager(core, endpoints=endpoints)
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


def _config_from_environment() -> tuple[TrainConfig | None, list[dict[str, Any]]]:
    """Decode the startup configuration set by the ``run`` command.

    Returns ``(train_config | None, atp_endpoints)``; ``(None, [])`` when the
    environment variable is unset (atp-api.md §6, config.py).
    """

    value = os.environ.get(TRAIN_CONFIG_ENV, "")
    if not value.strip():
        return None, []
    return decode_config(value)


def create_app(
    train_configs: Sequence[TrainConfig] | None = None,
    atp_endpoints: Sequence[AtpEndpoint] | None = None,
) -> FastAPI:
    """Build the FastAPI application with the production lifespan wired in.

    ``train_configs=None``/``atp_endpoints=None`` (the uvicorn factory
    defaults) load the corresponding part from the ``A_TRAIN_CONFIG``
    environment variable set by the ``run`` command; pass a sequence
    (including the empty tuple for ``atp_endpoints``) to configure it
    explicitly.
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
        ):
            yield

    return build_app(_lifespan)
