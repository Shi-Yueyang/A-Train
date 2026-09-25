"""Hosts ATP TCP listeners and bridges snapshots and ATP commands (§2.6)."""

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
from .connection import AtpConnection
from .protocol import decode_line, make_error, parse_atp_command

if TYPE_CHECKING:
    from ...simulation.core import SimulationCore

logger = logging.getLogger("a_train.adapters.atp")


@dataclass(frozen=True)
class AtpEndpoint:
    """Local address on which A-Train accepts ATP connections."""

    host: str
    port: int


class AtpManager:
    """Owns ATP TCP listeners, accepted sessions, and protocol handling."""

    def __init__(
        self,
        core: SimulationCore,
        endpoints: Sequence[AtpEndpoint] = (),
    ) -> None:
        self._core = core
        self._endpoints = tuple(endpoints)
        self._servers: list[asyncio.AbstractServer] = []
        self._clients: list[AtpConnection] = []
        self._session_tasks: set[asyncio.Task[None]] = set()
        self._publisher_tasks: dict[AtpConnection, asyncio.Task[None]] = {}
        self._queues: dict[AtpConnection, asyncio.Queue[Any]] = {}
        self._active_peers_by_endpoint = {endpoint: 0 for endpoint in self._endpoints}
        self._listening = False

    @property
    def clients(self) -> tuple[AtpConnection, ...]:
        return tuple(self._clients)

    @property
    def endpoints(self) -> tuple[AtpEndpoint, ...]:
        return self._endpoints

    @property
    def listening(self) -> bool:
        return self._listening

    @property
    def ready_count(self) -> int:
        """Number of ATP peers currently connected."""

        return sum(1 for client in self._clients if client.ready)

    def active_peer_count(self, endpoint: AtpEndpoint) -> int:
        return self._active_peers_by_endpoint.get(endpoint, 0)

    def send_message(self, message: dict[str, Any]) -> bool:
        """Broadcast one framed message to every READY peer; True if any write."""

        sent = False
        for client in self._clients:
            if client.send_message(message):
                sent = True
        return sent

    async def start(self) -> None:
        try:
            for endpoint in self._endpoints:
                server = await asyncio.start_server(
                    partial(self._accept, endpoint), endpoint.host, endpoint.port
                )
                self._servers.append(server)
            self._listening = bool(self._servers)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        for server in self._servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in self._servers))
        self._servers.clear()
        for client in self._clients:
            client.close()
        tasks = [*self._session_tasks, *self._publisher_tasks.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for queue in self._queues.values():
            self._core.unsubscribe(queue)
        self._publisher_tasks.clear()
        self._queues.clear()
        self._clients.clear()
        self._session_tasks.clear()
        self._active_peers_by_endpoint = {endpoint: 0 for endpoint in self._endpoints}
        self._listening = False

    def _accept(
        self,
        endpoint: AtpEndpoint,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.create_task(
            self._serve_peer(endpoint, reader, writer), name="atp-peer-session"
        )
        self._session_tasks.add(task)
        task.add_done_callback(self._session_tasks.discard)

    async def _serve_peer(
        self,
        endpoint: AtpEndpoint,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        client = AtpConnection(writer)
        self._clients.append(client)
        self._active_peers_by_endpoint[endpoint] += 1
        queue = self._core.subscribe()
        self._queues[client] = queue
        publisher = asyncio.create_task(
            self._publish_loop(client, queue), name=f"atp-publisher {client.ident}"
        )
        self._publisher_tasks[client] = publisher
        logger.info("%s: channel established", client.ident)
        try:
            while True:
                try:
                    line = await reader.readline()
                except ValueError as exc:
                    logger.warning("%s: stream error: %s", client.ident, exc)
                    break
                if not line:
                    logger.info("%s: closed by peer", client.ident)
                    break
                try:
                    message = decode_line(line)
                except ValueError as exc:
                    logger.warning("%s: %s", client.ident, exc)
                    client.send_message(make_error("malformed_message", str(exc)))
                    continue
                await self._handle_inbound(client, message)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as exc:
            logger.info("%s: connection ended: %s", client.ident, exc)
        finally:
            publisher.cancel()
            with suppress(asyncio.CancelledError):
                await publisher
            self._publisher_tasks.pop(client, None)
            self._queues.pop(client, None)
            self._core.unsubscribe(queue)
            self._active_peers_by_endpoint[endpoint] -= 1
            if client in self._clients:
                self._clients.remove(client)
            client.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def _publish_loop(
        self, client: AtpConnection, queue: asyncio.Queue[Any]
    ) -> None:
        while True:
            snapshot = await queue.get()
            client.publish(snapshot)

    # -- Inbound ATP content (atp-api.md §4, §5) ---------------------------------

    async def _handle_inbound(
        self, client: AtpConnection, message: dict[str, Any]
    ) -> None:
        mtype = message.get("type")
        if mtype == "atp_command":
            await self._handle_atp_command(client, message)
        elif mtype == "error":
            logger.warning("%s: ATP reported error: %r", client.ident, message)
        else:
            client.send_message(
                make_error("unknown_message_type", f"unexpected message type: {mtype!r}")
            )

    async def _handle_atp_command(
        self, client: AtpConnection, message: dict[str, Any]
    ) -> None:
        train_id = self._core.train_id
        try:
            cab_id, drive_demand, door, atp_signal = parse_atp_command(message)
        except ValueError as exc:
            client.send_message(make_error("invalid_atp_command", str(exc)))
            return

        commands: list[Any] = []
        if drive_demand is not None:
            commands.append(
                TrainControlCommand(
                    train_id=train_id,
                    payload=TrainControl(cab_id=cab_id, drive_demand=drive_demand),
                )
            )
        if door is not None:
            for door_key in ("left_door", "right_door"):
                commands.append(
                    EquipmentCommand(
                        train_id=train_id,
                        payload=EquipmentControlRequest(key=door_key, command=door),
                    )
                )
        if atp_signal is not None:
            commands.append(
                EquipmentCommand(
                    train_id=train_id,
                    payload=EquipmentControlRequest(
                        equipment_type="stcs_atp",
                        cab_id=cab_id,
                        command=atp_signal,
                    ),
                )
            )

        for command in commands:
            result = await self._core.submit_command(command)
            if not result.ok:
                client.send_message(make_error("command_rejected", result.error or "rejected"))
