"""Application lifecycle and REST/WebSocket test client helpers (§6.1).

``running_app()`` starts the *real* production application through its FastAPI
lifespan -- the ``SimulationCore`` ``run_loop()`` task, the ATP manager, and
the API router all start and stop -- and yields an ``httpx.AsyncClient`` backed
by the ASGI transport. No production module is mocked.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from asgi_lifespan import LifespanManager

from a_train.bootstrap import create_app


@asynccontextmanager
async def running_app() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
