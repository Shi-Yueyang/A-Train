"""One reconnecting TCP client for one external ATP process (§4.2).

The simulator acts as the TCP client; ATP acts as the TCP server. Each client
owns exactly one cab's connection and runs a connection loop:

    CONNECTING --TCP open--> HANDSHAKING --HELLO_ACK accepted--> READY
        ^                        |  |                            |
        +------- backoff wait <--+  +------ peer closed / error -+

Every attempt sends ``HELLO``, waits for an accepted ``HELLO_ACK``, and — once
READY — keeps reading so peer closure and protocol failures are detected. Any
failure (connection refused, timeout, rejected or malformed handshake, stream
error, unexpected close) returns to ``CONNECTING`` after an exponential backoff
capped at ``max_retry_delay``; a session that reached READY resets the backoff
so recovery from a dropped link is fast (§4.2 connection sequence).

Phase 3.1 establishes the channel and handshake only: no ``TRAIN_STATE`` /
``BTM_RX`` publishing and no inbound content handling — messages received
while READY are logged and ignored until Phase 3.2.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from enum import Enum

from .protocol import encode_message, read_message

logger = logging.getLogger("a_train.adapters.atp")


class ClientState(Enum):
    """Connection-loop state of one ATP client (§4.2)."""

    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    HANDSHAKING = "HANDSHAKING"
    READY = "READY"
    STOPPED = "STOPPED"


class AtpClient:
    """Owns one cab's reconnecting TCP connection to an external ATP process."""

    def __init__(
        self,
        train_id: str,
        cab_id: int,
        host: str,
        port: int,
        *,
        retry_delay: float = 1.0,
        max_retry_delay: float = 30.0,
        handshake_timeout: float = 10.0,
    ) -> None:
        self._train_id = train_id
        self._cab_id = cab_id
        self._host = host
        self._port = port
        self._retry_delay = retry_delay
        self._max_retry_delay = max_retry_delay
        self._handshake_timeout = handshake_timeout

        self._state = ClientState.IDLE
        self._task: asyncio.Task[None] | None = None

    @property
    def train_id(self) -> str:
        return self._train_id

    @property
    def cab_id(self) -> int:
        return self._cab_id

    @property
    def state(self) -> ClientState:
        return self._state

    @property
    def ready(self) -> bool:
        return self._state is ClientState.READY

    def _ident(self) -> str:
        return f"ATP {self._train_id} cab {self._cab_id} ({self._host}:{self._port})"

    async def start(self) -> None:
        """Launch the connection loop as a background task on the running loop."""

        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name=f"atp-client {self._train_id} cab {self._cab_id}"
            )

    async def stop(self) -> None:
        """Cancel the connection loop and leave the client stopped."""

        self._state = ClientState.STOPPED
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        delay = self._retry_delay
        while True:
            self._state = ClientState.CONNECTING
            reached_ready = False
            try:
                reached_ready = await self._session()
            except asyncio.CancelledError:
                raise
            except (OSError, ValueError, asyncio.TimeoutError) as exc:
                logger.warning("%s: connection failed: %s; retrying", self._ident(), exc)
            self._state = ClientState.CONNECTING
            await asyncio.sleep(delay)
            delay = self._retry_delay if reached_ready else min(delay * 2, self._max_retry_delay)

    async def _session(self) -> bool:
        """Run one connect/handshake/hold session; True if it reached READY."""

        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            self._state = ClientState.HANDSHAKING
            hello = {"type": "hello", "train_id": self._train_id, "cab_id": self._cab_id}
            writer.write(encode_message(hello))
            await writer.drain()
            ack = await asyncio.wait_for(read_message(reader), self._handshake_timeout)
            if ack is None or ack.get("type") != "hello_ack" or not ack.get("accepted", False):
                logger.warning("%s: handshake rejected or timed out (%r)", self._ident(), ack)
                return False
            self._state = ClientState.READY
            logger.info("%s: handshake complete", self._ident())
            await self._hold(reader)
            return True
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _hold(self, reader: asyncio.StreamReader) -> None:
        """Consume inbound messages while READY until the peer closes (§4.3).

        Phase 3.1 defines no inbound content: every valid message is logged at
        debug level; a malformed message raises so the caller reconnects.
        """

        while True:
            message = await read_message(reader)
            if message is None:
                logger.info("%s: closed by peer", self._ident())
                return
            logger.debug("%s: received %s (content handling is Phase 3.2)", self._ident(), message)
