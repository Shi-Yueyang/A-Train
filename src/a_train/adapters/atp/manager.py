"""Creates clients and bridges snapshots and ATP commands (§4, §2.6).

The manager owns one ``AtpClient`` per configured train cab. It receives the
core's snapshot and dispatches ``TRAIN_STATE`` / ``BTM_RX`` messages, and
converts accepted ``ATP_STATE`` messages into queued ``AtpStateCommand``
objects on the core's command queue.

Phase 0: no cab endpoints are configured, so ``start()`` and ``stop()`` are
no-ops and the manager stays ready for Phase 4 to populate clients.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...simulation.commands import Command


class AtpManager:
    """Owns the TCP clients for all configured external ATP processes."""

    def __init__(self, command_queue: asyncio.Queue[Command] | None = None) -> None:
        self._command_queue = command_queue
        self._clients: list[object] = []

    async def start(self) -> None:
        # No external ATP endpoints are configured in Phase 0.
        return

    async def stop(self) -> None:
        for client in self._clients:
            stop = getattr(client, "stop", None)
            if stop is not None:
                await stop()
        self._clients.clear()
