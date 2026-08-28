"""FastAPI construction and dependency wiring (§5, §7.2).

``create_app(lifespan)`` builds the FastAPI application, includes the API
router, and attaches the lifespan supplied by ``bootstrap.py``. The lifespan
-- not this module -- assembles the production components and starts the
background tasks (§2.6). Phase 5 adds static-asset mounting and the full route
surface; Phase 0 wires only the status route used by the lifecycle tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from fastapi import FastAPI

from .routes import router

Lifespan = Callable[[FastAPI], AsyncIterator[None]]


def create_app(lifespan: Lifespan) -> FastAPI:
    app = FastAPI(
        title="A-Train Simulator",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app
