"""Snapshot-to-browser WebSocket publisher (§5.3, §2.6).

A publisher task consumes the core's bounded snapshot queue and pushes state to
connected browsers. It never performs network I/O inside ``run_loop()`` so a
slow client cannot delay physics: the core publishes to each subscriber queue
with a non-blocking put that drops the oldest snapshot when full, and every
client has its own queue and publisher task.

On connect the client immediately receives the latest snapshot (seeded by
``SimulationCore.subscribe``), then one snapshot after every fixed step and
after every control-state transition.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...simulation.core import SimulationCore
from .schemas import snapshot_to_dict

ws_router = APIRouter()


@ws_router.websocket("/ws")
async def snapshot_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    core: SimulationCore = websocket.app.state.core
    queue = core.subscribe()
    try:
        while True:
            snapshot = await queue.get()
            await websocket.send_json(snapshot_to_dict(snapshot))
    except WebSocketDisconnect:
        pass
    finally:
        core.unsubscribe(queue)
