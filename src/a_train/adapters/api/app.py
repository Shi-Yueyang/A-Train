"""FastAPI construction and dependency wiring (§5, §7.2).

``create_app(lifespan)`` builds the FastAPI application, includes the API
router and WebSocket publisher, and serves the static browser demo from the
repository ``web/`` directory at the site root. The lifespan -- not this module
-- assembles the production components and starts the background tasks (§2.6).
The API routes are registered before the static mount so ``/api/*`` and ``/ws``
are never shadowed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import router
from .websocket import ws_router

Lifespan = Callable[[FastAPI], AsyncIterator[None]]

# src/a_train/adapters/api/app.py -> repo root -> web
_WEB_DIR = Path(__file__).resolve().parents[4] / "web"


def create_app(lifespan: Lifespan) -> FastAPI:
    app = FastAPI(
        title="A-Train Simulator",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.include_router(ws_router)
    if _WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
    return app
