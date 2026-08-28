"""FastAPI application factory export."""

from __future__ import annotations

from .app import create_app
from .routes import router
from .websocket import ws_router

__all__ = ["create_app", "router", "ws_router"]
