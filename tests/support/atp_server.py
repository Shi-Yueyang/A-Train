"""Controllable production-protocol TCP server for test ATP peers (§6).

The simulator connects to an external ATP process over TCP/NDJSON. In tests
that ATP process is replaced by this server, which speaks the same NDJSON
protocol over a real TCP socket -- no production module is mocked.

Each connected client's received NDJSON lines are collected into a shared
deque; a test can ``send`` a message (broadcast to all connected clients) and
``wait_for_message`` to read the next received message.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Mapping
from typing import Any


class TestAtpServer:
    """A controllable asyncio TCP server speaking NDJSON."""

    __test__ = False  # support helper; not a pytest test class

    def __init__(self) -> None:
        self._server: asyncio.Server | None = None
        self._port: int = 0
        self._received: deque[dict[str, Any]] = deque()
        self._clients: list[asyncio.StreamWriter] = []

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self._server = await asyncio.start_server(self._handle_connection, host, port)
        sock = self._server.sockets[0]
        self._port = sock.getsockname()[1]
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

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._clients.append(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                self._received.append(message)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if writer in self._clients:
                self._clients.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def send(self, message: Mapping[str, Any]) -> None:
        data = (json.dumps(dict(message)) + "\n").encode("utf-8")
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
        for writer in list(self._clients):
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass
        self._clients.clear()
