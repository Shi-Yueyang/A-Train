"""Application lifecycle and REST/WebSocket test client helpers (§6.1).

``running_app()`` starts the *real* production application through its FastAPI
lifespan -- the ``SimulationCore`` ``run_loop()`` task, the ATP manager, and
the API router all start and stop -- and yields an ``httpx.AsyncClient`` backed
by the ASGI transport. No production module is mocked. The FastAPI ``app`` is
exposed on the client as ``client.app`` so WebSocket tests can drive the real
ASGI ``websocket`` interface in-process.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx
from asgi_lifespan import LifespanManager

from a_train.adapters.atp.manager import AtpEndpoint
from a_train.bootstrap import create_app
from a_train.domain.train import TrainConfig


@asynccontextmanager
async def running_app(
    train_configs: Sequence[TrainConfig] | None = None,
    atp_endpoints: Sequence[AtpEndpoint] | None = None,
    *,
    use_environment_train_config: bool = False,
) -> AsyncIterator[httpx.AsyncClient]:
    """``atp_endpoints=None`` uses the production env-var path (config.py)."""

    app = create_app(
        None if use_environment_train_config and train_configs is None else train_configs,
        atp_endpoints,
    )
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            object.__setattr__(client, "app", app)
            yield client
