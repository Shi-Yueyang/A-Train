"""In-process async ASGI WebSocket client for integration tests.

The REST tests drive the real ASGI app through ``httpx.ASGITransport``. WebSockets
have no equivalent in httpx, so this helper drives the real FastAPI app's
``websocket`` ASGI interface directly with a constructed scope and a pair of
async queues. No production module is mocked; the real ``SimulationCore``,
router, and WebSocket publisher all run.

The app->client channel is bounded so that a test client which never reads
("slow client") applies real backpressure to the publisher task, exercising the
core's bounded subscriber-queue guarantee that physics can never be delayed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class AsgiWebSocket:
    def __init__(self, app: Any, path: str, *, send_maxsize: int = 1) -> None:
        self._app = app
        self._path = path
        self._client_to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._app_to_client: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=send_maxsize)
        self._accepted = asyncio.Event()
        self._closed = asyncio.Event()
        self._first_receive = True
        self._scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 80),
            "subprotocols": [],
            "state": {},
            "extensions": {},
        }
        self._task: asyncio.Task[None] | None = None

    async def _receive(self) -> dict[str, Any]:
        if self._first_receive:
            self._first_receive = False
            return {"type": "websocket.connect"}
        if self._closed.is_set():
            return {"type": "websocket.disconnect", "code": 1000}
        return await self._client_to_app.get()

    async def _send(self, message: dict[str, Any]) -> None:
        mtype = message.get("type")
        if mtype == "websocket.accept":
            self._accepted.set()
            return
        if mtype == "websocket.close":
            self._closed.set()
            return
        if mtype == "websocket.send":
            # Bounded: blocks when the client is not reading (slow client).
            await self._app_to_client.put(message)
            return

    async def _run(self) -> None:
        try:
            await self._app(self._scope, self._receive, self._send)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _start(self) -> None:
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self._accepted.wait(), timeout=2.0)

    async def receive_json(self) -> Any:
        message = await self._app_to_client.get()
        return json.loads(message["text"])

    async def receive_text(self) -> str:
        message = await self._app_to_client.get()
        return message["text"]

    async def send_json(self, obj: Any) -> None:
        text = json.dumps(obj)
        await self._client_to_app.put({"type": "websocket.receive", "text": text})

    async def close(self) -> None:
        if self._task is None:
            return
        self._closed.set()
        await self._client_to_app.put({"type": "websocket.disconnect", "code": 1000})
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass


@asynccontextmanager
async def ws_connect(
    app: Any, path: str = "/ws", *, send_maxsize: int = 1
) -> AsyncIterator[AsgiWebSocket]:
    session = AsgiWebSocket(app, path, send_maxsize=send_maxsize)
    await session._start()
    try:
        yield session
    finally:
        await session.close()
