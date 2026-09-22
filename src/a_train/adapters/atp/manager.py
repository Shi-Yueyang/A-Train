"""Creates clients and bridges snapshots and ATP commands (atp-api.md, §2.6).

The manager holds one ``AtpClient`` per configured train cab (atp-api.md §1.1:
each ATP process serves exactly one cab and accepts one connection at a time,
so no in-band handshake identifies the stream). ``start()`` launches every
client's connection loop, subscribes one bounded snapshot queue per cab, and
runs a publisher task per client that turns core snapshots into
``TRAIN_STATE`` lines (atp-api.md §3.1). Inbound ATP content is validated and
converted into commands submitted to the core (architectural.md §4.1);
rejected input is answered with an ``ERROR`` message (atp-api.md §5).
Publisher and inbound tasks perform all protocol I/O outside ``run_loop()``,
so a slow or chatty ATP peer cannot delay physics (§2.6).

With no endpoints configured (the default when the environment variable is
unset), ``start()`` and ``stop()`` are no-ops and the application behaves
exactly as before Phase 3.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from ...domain.train import EquipmentControlRequest, TrainControl
from ...simulation.commands import EquipmentCommand, TrainControlCommand
from .client import AtpClient
from .protocol import make_error, parse_atp_command

if TYPE_CHECKING:
    from ...simulation.core import SimulationCore

logger = logging.getLogger("a_train.adapters.atp")


@dataclass(frozen=True)
class AtpEndpoint:
    """Where to reach the external ATP process serving one cab."""

    cab_id: int
    host: str
    port: int


class AtpManager:
    """Owns the TCP clients, publishers, and inbound handling for all cabs."""

    def __init__(
        self,
        core: SimulationCore,
        endpoints: Sequence[AtpEndpoint] = (),
        *,
        retry_delay: float = 1.0,
        max_retry_delay: float = 30.0,
    ) -> None:
        self._core = core
        self._endpoints = tuple(endpoints)
        self._retry_delay = retry_delay
        self._max_retry_delay = max_retry_delay
        self._clients: list[AtpClient] = []
        self._publisher_tasks: dict[AtpClient, asyncio.Task[None]] = {}
        self._queues: dict[AtpClient, asyncio.Queue[Any]] = {}

    @property
    def clients(self) -> tuple[AtpClient, ...]:
        return tuple(self._clients)

    @property
    def ready_endpoints(self) -> frozenset[tuple[str, int]]:
        """(train_id, cab_id) pairs whose channel is currently open (READY)."""

        return frozenset((c.train_id, c.cab_id) for c in self._clients if c.ready)

    def send_message(self, train_id: str, cab_id: int, message: dict[str, Any]) -> bool:
        """Write one framed message to one cab's ATP process; False if absent/not READY."""

        for client in self._clients:
            if client.train_id == train_id and client.cab_id == cab_id:
                return client.send_message(message)
        return False

    async def start(self) -> None:
        for endpoint in self._endpoints:
            client = AtpClient(
                self._core.train_id,
                endpoint.cab_id,
                endpoint.host,
                endpoint.port,
                retry_delay=self._retry_delay,
                max_retry_delay=self._max_retry_delay,
            )
            client.set_inbound_handler(partial(self._handle_inbound, client))
            queue = self._core.subscribe()
            self._queues[client] = queue
            self._publisher_tasks[client] = asyncio.create_task(
                self._publish_loop(client, queue),
                name=f"atp-publisher {self._core.train_id} cab {endpoint.cab_id}",
            )
            await client.start()
            self._clients.append(client)

    async def stop(self) -> None:
        for client in self._clients:
            await client.stop()
            task = self._publisher_tasks.pop(client, None)
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            queue = self._queues.pop(client, None)
            if queue is not None:
                self._core.unsubscribe(queue)
        self._clients.clear()

    async def _publish_loop(self, client: AtpClient, queue: asyncio.Queue[Any]) -> None:
        while True:
            snapshot = await queue.get()
            client.publish(snapshot)

    # -- Inbound ATP content (atp-api.md §4, §5) ---------------------------------

    async def _handle_inbound(self, client: AtpClient, message: dict[str, Any]) -> None:
        mtype = message.get("type")
        if mtype == "atp_command":
            await self._handle_atp_command(client, message)
        elif mtype == "error":
            logger.warning("%s: ATP reported error: %r", client.ident, message)
        else:
            client.send_message(
                make_error(
                    "unknown_message_type",
                    f"unexpected message type: {mtype!r}",
                    train_id=client.train_id,
                    cab_id=client.cab_id,
                )
            )

    async def _handle_atp_command(self, client: AtpClient, message: dict[str, Any]) -> None:
        try:
            drive_demand, door, atp_signal = parse_atp_command(
                message, client.train_id, client.cab_id
            )
        except ValueError as exc:
            client.send_message(
                make_error(
                    "invalid_atp_command",
                    str(exc),
                    train_id=client.train_id,
                    cab_id=client.cab_id,
                )
            )
            return

        commands: list[Any] = []
        if drive_demand is not None:
            commands.append(
                TrainControlCommand(
                    train_id=client.train_id,
                    payload=TrainControl(cab_id=client.cab_id, drive_demand=drive_demand),
                )
            )
        if door is not None:
            for door_key in ("left_door", "right_door"):
                commands.append(
                    EquipmentCommand(
                        train_id=client.train_id,
                        payload=EquipmentControlRequest(key=door_key, command=door),
                    )
                )
        if atp_signal is not None:
            commands.append(
                EquipmentCommand(
                    train_id=client.train_id,
                    payload=EquipmentControlRequest(
                        key=f"stcs_atp_cab_{client.cab_id}",
                        command=atp_signal,
                    ),
                )
            )

        for command in commands:
            result = await self._core.submit_command(command)
            if not result.ok:
                client.send_message(
                    make_error(
                        "command_rejected",
                        result.error or "command rejected",
                        train_id=client.train_id,
                        cab_id=client.cab_id,
                    )
                )
