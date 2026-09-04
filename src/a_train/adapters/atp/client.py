"""One reconnecting TCP client for one external ATP process (atp-api.md §1).

The simulator acts as the TCP client; ATP acts as the TCP server. Each client
owns exactly one cab's connection and runs a connection loop:

    CONNECTING --TCP open--> READY
        ^                       |
        +--- backoff wait (state DISCONNECTED) <--+
              peer closed / stream error

There is no application-level handshake: the channel is READY the moment TCP
opens, and snapshot publishing starts immediately. The cab identity lives in
the endpoint configuration (one ATP server per cab), not in a ``HELLO``
message. Any failure (connection refused, stream error, unexpected close)
returns to ``CONNECTING`` after an exponential backoff capped at
``max_retry_delay``; a session that reached READY resets the backoff so
recovery from a dropped link is fast (atp-api.md §1.4).

``publish`` converts core snapshots into cyclic ``TRAIN_STATE`` and
event-driven ``BTM_RX`` lines; inbound content is validated, answered with
``ERROR`` on malformed lines (atp-api.md §5), and dispatched to the manager
for ATP-command handling.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from enum import Enum
from typing import Any

from ...simulation.snapshots import SimulationSnapshot
from .protocol import (
    decode_line,
    encode_message,
    make_btm_rx,
    make_error,
    make_train_state,
)

logger = logging.getLogger("a_train.adapters.atp")

InboundHandler = Callable[[dict[str, Any]], Awaitable[None]]


class ClientState(Enum):
    """Connection-loop state of one ATP client (atp-api.md §1.3)."""

    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    READY = "READY"
    DISCONNECTED = "DISCONNECTED"
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
        on_message: InboundHandler | None = None,
    ) -> None:
        self._train_id = train_id
        self._cab_id = cab_id
        self._host = host
        self._port = port
        self._retry_delay = retry_delay
        self._max_retry_delay = max_retry_delay
        self._on_message = on_message

        self._state = ClientState.IDLE
        self._task: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._last_snapshot: SimulationSnapshot | None = None
        self._last_btm_count: int | None = None

    @property
    def train_id(self) -> str:
        return self._train_id

    @property
    def cab_id(self) -> int:
        return self._cab_id

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def state(self) -> ClientState:
        return self._state

    @property
    def ready(self) -> bool:
        return self._state is ClientState.READY

    @property
    def ident(self) -> str:
        return f"ATP {self._train_id} cab {self._cab_id} ({self._host}:{self._port})"

    def set_inbound_handler(self, handler: InboundHandler) -> None:
        """Attach the manager callback for inbound action messages (atp-api.md §4)."""

        self._on_message = handler

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

    # -- Outbound transport (atp-api.md §3) -------------------------------------

    def send_message(self, message: Mapping[str, Any]) -> bool:
        """Write one framed NDJSON message on the READY connection (atp-api.md §1.2).

        Returns True if the bytes were queued for the peer, False if the
        channel is not READY or the write failed (the connection loop then
        reconnects).
        """

        writer = self._writer
        if self._state is not ClientState.READY or writer is None:
            return False
        try:
            writer.write(encode_message(message))
        except (ConnectionError, OSError, RuntimeError):
            return False
        return True

    def publish(self, snapshot: SimulationSnapshot) -> None:
        """Publish one core snapshot as this cab's protocol content (atp-api.md §3.1, §3.2).

        Writes ``TRAIN_STATE`` for every
        READY snapshot and ``BTM_RX`` only when this cab's BTM delivery count
        increased. Snapshots arriving while the channel is down are
        remembered and re-published as soon as it reconnects.
        """

        self._last_snapshot = snapshot
        if self._state is ClientState.READY:
            self._publish_now(snapshot)

    def _publish_now(self, snapshot: SimulationSnapshot) -> None:
        train = next((t for t in snapshot.trains if t.train_id == self._train_id), None)
        if train is None:
            return
        self.send_message(make_train_state(self._train_id, self._cab_id, train))
        self._publish_btm(train.equipment.get("btm"))

    def _publish_btm(self, entries: Any) -> None:
        entry = next((e for e in entries or () if e.cab_id == self._cab_id), None)
        if entry is None:
            return
        count = entry.received_count
        if self._last_btm_count is not None and count > self._last_btm_count:
            if entry.payload_b64 is not None:
                self.send_message(make_btm_rx(entry.payload_b64))
        self._last_btm_count = count

    # -- Connection loop -------------------------------------------------------

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
                logger.warning("%s: connection failed: %s; retrying", self.ident, exc)
            self._state = ClientState.DISCONNECTED
            await asyncio.sleep(delay)
            delay = self._retry_delay if reached_ready else min(delay * 2, self._max_retry_delay)

    async def _session(self) -> bool:
        """Run one connect/hold session; True if the channel reached READY."""

        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            self._writer = writer
            self._state = ClientState.READY
            logger.info("%s: channel established", self.ident)
            if self._last_snapshot is not None:
                self._publish_now(self._last_snapshot)
            try:
                await self._hold(reader)
            finally:
                self._writer = None
            return True
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _hold(self, reader: asyncio.StreamReader) -> None:
        """Consume inbound messages while READY until the peer closes."""

        while True:
            line = await reader.readline()
            if not line:
                logger.info("%s: closed by peer", self.ident)
                return
            try:
                message = decode_line(line)
            except ValueError as exc:
                # A framing-valid but invalid line is reported, not fatal (atp-api.md §5.1).
                logger.warning("%s: %s", self.ident, exc)
                self.send_message(
                    make_error(
                        "malformed_message",
                        str(exc),
                        train_id=self._train_id,
                        cab_id=self._cab_id,
                    )
                )
                continue
            if self._on_message is not None:
                await self._on_message(message)
