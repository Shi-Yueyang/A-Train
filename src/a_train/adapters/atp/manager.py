"""Creates clients and owns one per configured ATP endpoint (§4, §2.6).

The manager holds one ``AtpClient`` per configured train cab (§4.2: each ATP
process has exactly one connection). ``start()`` launches every client's
connection loop; ``stop()`` cancels them. It also exposes handshake
observability (``clients``, ``ready_endpoints``) without touching world state.

Phase 3.1 establishes channels and the ``HELLO`` / ``HELLO_ACK`` handshake
only. The manager receives no core snapshots for publishing and converts no
inbound content into queued commands; ``TRAIN_STATE`` / ``BTM_RX`` publishing
and content handling arrive with Phase 3.2.

With no endpoints configured (the default), ``start()`` and ``stop()`` are
no-ops and the application behaves exactly as before Phase 3.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .client import AtpClient

if TYPE_CHECKING:
    from ...simulation.commands import Command


@dataclass(frozen=True)
class AtpEndpoint:
    """Where to reach the external ATP process serving one train cab (§4.2)."""

    train_id: str
    cab_id: int
    host: str
    port: int


class AtpManager:
    """Owns the TCP clients for all configured external ATP processes."""

    def __init__(
        self,
        command_queue: asyncio.Queue[Command] | None = None,
        endpoints: Sequence[AtpEndpoint] = (),
        *,
        retry_delay: float = 1.0,
        max_retry_delay: float = 30.0,
        handshake_timeout: float = 10.0,
    ) -> None:
        self._command_queue = command_queue
        self._endpoints = tuple(endpoints)
        self._retry_delay = retry_delay
        self._max_retry_delay = max_retry_delay
        self._handshake_timeout = handshake_timeout
        self._clients: list[AtpClient] = []

    @property
    def clients(self) -> tuple[AtpClient, ...]:
        return tuple(self._clients)

    @property
    def ready_endpoints(self) -> frozenset[tuple[str, int]]:
        """(train_id, cab_id) pairs whose channel has completed the handshake."""

        return frozenset((c.train_id, c.cab_id) for c in self._clients if c.ready)

    async def start(self) -> None:
        for endpoint in self._endpoints:
            client = AtpClient(
                endpoint.train_id,
                endpoint.cab_id,
                endpoint.host,
                endpoint.port,
                retry_delay=self._retry_delay,
                max_retry_delay=self._max_retry_delay,
                handshake_timeout=self._handshake_timeout,
            )
            await client.start()
            self._clients.append(client)

    async def stop(self) -> None:
        for client in self._clients:
            await client.stop()
        self._clients.clear()
