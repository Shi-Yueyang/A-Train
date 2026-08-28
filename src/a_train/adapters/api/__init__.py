"""FastAPI application factory export."""

from __future__ import annotations

from .app import create_app
from .routes import router

__all__ = ["create_app", "router"]
