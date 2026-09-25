"""Controllable ATP TCP client for integration tests (§6).

The test peer connects to A-Train's listener and exchanges production NDJSON
over real TCP sockets. It retries until the application is listening.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections import deque
from collections.abc import Mapping
from typing import Any


class TestAtpServer:
    """A controllable ATP client speaking NDJSON to A-Train."""

    __test__ = False  # support helper; not a pytest test class

    def __init__(self) -> None:
        self._port: int = 0
        self._received: deque[dict[str, Any]] = deque()
        self._clients: dict[int, asyncio.StreamWriter] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = False

    async def start(
        self, host: str = "127.0.0.1", port: int = 0, *, peer_count: int = 1
    ) -> int:
        if port == 0:
            with socket.socket() as probe:
                probe.bind((host, 0))
                port = int(probe.getsockname()[1])
        self._port = port
        self._stopping = False
        self._tasks = [
            asyncio.create_task(self._connect_peer(host, port, index))
            for index in range(peer_count)
        ]
        return self._port

    @property
    def port(self) -> int:
        return self._port

    @property
    def connection_count(self) -> int:
        return len(self._clients)

    def drop_client(self, index: int = 0) -> None:
        """Close one accepted connection server-side, as a link reset would."""

        self._clients[index].close()

    async def _connect_peer(self, host: str, port: int, index: int) -> None:
        while not self._stopping:
            writer: asyncio.StreamWriter | None = None
            try:
                reader, writer = await asyncio.open_connection(host, port)
                self._clients[index] = writer
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    try:
                        message = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    self._received.append(message)
            except (ConnectionError, OSError):
                pass
            finally:
                self._clients.pop(index, None)
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except (ConnectionError, OSError):
                        pass
            if not self._stopping:
                await asyncio.sleep(0.05)

    async def send(self, message: Mapping[str, Any]) -> None:
        data = (json.dumps(dict(message)) + "\n").encode("utf-8")
        for writer in list(self._clients.values()):
            writer.write(data)
            await writer.drain()

    async def send_raw(self, text: str) -> None:
        """Broadcast raw bytes to every client (e.g. a malformed NDJSON line)."""

        data = text.encode("utf-8")
        for writer in list(self._clients):
            writer.write(data)
            await writer.drain()

    async def wait_for_message(self, timeout: float = 2.0) -> dict[str, Any]:
        async def _next() -> dict[str, Any]:
            while not self._received:
                await asyncio.sleep(0.01)
            return self._received.popleft()

        return await asyncio.wait_for(_next(), timeout=timeout)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for writer in list(self._clients.values()):
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass
        self._clients.clear()
