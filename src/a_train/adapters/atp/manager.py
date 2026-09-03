"""Creates clients and owns one per configured ATP endpoint (§4, §2.6).

The manager holds one ``AtpClient`` per configured train cab (§4.2: each ATP
process has exactly one connection). ``start()`` launches every client's
connection loop; ``stop()`` cancels them. It also exposes handshake
observability (``clients``, ``ready_endpoints``) without touching world state.

Phase 3.1 establishes the full channel: endpoints come from the run-command
configuration, each client holds a persistent reconnecting connection, and
``send_message`` writes framed bytes to a READY cab. ``TRAIN_STATE`` /
``BTM_RX`` publishing and content handling arrive with Phase 3.2.

With no endpoints configured (the default when the environment variable is
unset), ``start()`` and ``stop()`` are no-ops and the application behaves
exactly as before Phase 3.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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

    def send_message(self, train_id: str, cab_id: int, message: Mapping[str, Any]) -> bool:
        """Write one framed message to one cab's ATP process; False if absent/not READY."""

        for client in self._clients:
            if client.train_id == train_id and client.cab_id == cab_id:
                return client.send_message(message)
        return False

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
