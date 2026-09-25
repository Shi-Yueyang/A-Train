"""ATP listener acceptance tests (TODO.md §Phase 3.1).

A-Train accepts ATP TCP clients without a handshake and isolates dropped
connections from the simulation and other peers. Tests use the real application
and production-protocol test ATP clients; no production module is mocked.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable

import pytest

from a_train.adapters.atp.manager import AtpEndpoint
from a_train.domain.train import TrainConfig
from tests.support.app import running_app
from tests.support.atp_server import TestAtpServer

T1 = TrainConfig(
    train_id="TRAIN001",
    cab_ids=(1, 2),
    initial_active_cab=1,
    max_traction_accel=1.0,
    max_decel=2.0,
    initial_position=0.0,
)


def _two_peers(port: int) -> list[AtpEndpoint]:
    return [AtpEndpoint("127.0.0.1", port)]


def _one_peer(port: int) -> list[AtpEndpoint]:
    return [AtpEndpoint("127.0.0.1", port)]


def _manager(c):  # AtpManager
    return c.app.state.atp_manager


async def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(_poll(), timeout)


async def _next_message(server, mtype: str, timeout: float = 10.0) -> dict:
    """Pop from the shared server deque until a message of the given type."""

    async def _poll() -> dict:
        while True:
            message = await server.wait_for_message(timeout=timeout)
            if message.get("type") == mtype:
                return message

    return await asyncio.wait_for(_poll(), timeout)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def test_simulator_listens_and_serves_atp_peer() -> None:
    port = _free_port()
    endpoint = AtpEndpoint("127.0.0.1", port)
    async with running_app([T1], [endpoint]) as c:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            line = await asyncio.wait_for(reader.readline(), 2.0)
            assert b'"type":"train_state"' in line

            writer.write(b'{"type":"hello"}\n')
            await writer.drain()
            error = await asyncio.wait_for(reader.readline(), 2.0)
            assert b'"code":"unknown_message_type"' in error

            status = (await c.get("/api/atp/status")).json()
            assert status["connections"] == [{
                "host": "127.0.0.1",
                "port": port,
                "state": "READY",
                "ready": True,
                "active_peers": 1,
            }]
        finally:
            writer.close()
            await writer.wait_closed()


# -- Criterion: the listener serves clients immediately without a handshake ---


async def test_channels_become_ready_without_handshake() -> None:
    server = TestAtpServer()
    port = await server.start(peer_count=2)
    try:
        async with running_app([T1], _one_peer(port)) as c:
            await _wait_until(lambda: _manager(c).ready_count == 2)
            assert server.connection_count == 2

            # Content flows on the fresh connections with no handshake first.
            state = await _next_message(server, "train_state")
            assert state["type"] == "train_state"
            assert "cab_id" not in state and "train_id" not in state
    finally:
        await server.stop()


# -- Criterion: an ATP client can connect after listener startup ---------------


async def test_atp_client_connects_after_listener_starts() -> None:
    port = _free_port()
    async with running_app([T1], _one_peer(port)) as c:
        await asyncio.sleep(0.15)  # listener is active while no ATP client is connected
        assert _manager(c).ready_count == 0

        server = TestAtpServer()
        await server.start(port=port)
        try:
            await _wait_until(lambda: _manager(c).ready_count == 1)
            assert server.connection_count == 1
        finally:
            await server.stop()


# -- Criterion: a dropped peer is isolated; ATP reconnects to the live listener --


async def test_dropped_connection_is_reported_and_isolated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = TestAtpServer()
    port = await server.start(peer_count=2)
    try:
        with caplog.at_level(logging.INFO, logger="a_train.adapters.atp"):
            async with running_app([T1], _one_peer(port)) as c:
                await _wait_until(lambda: _manager(c).ready_count == 2)

                await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
                await c.post("/api/simulation/start")

                server.drop_client(0)
                await _wait_until(lambda: _manager(c).ready_count < 2)

                # The dropped peer re-establishes without a restart.
                await _wait_until(lambda: _manager(c).ready_count == 2)
                assert server.connection_count == 2

                # The simulation never stopped: stepping still works.
                r = await c.post("/api/simulation/step", json={"delta": 0.5})
                assert r.status_code == 200
                status = (await c.get("/api/status")).json()
                assert status["simulation_time"] == pytest.approx(0.5)
                assert status["simulation_state"] == "RUNNING"

                # The drop and the recovery are reported through logs. A
                # server-side close may surface as clean EOF or as a reset.
                assert any(
                    "closed by peer" in rec.getMessage() or "connection failed" in rec.getMessage()
                    for rec in caplog.records
                )
                assert any("channel established" in rec.getMessage() for rec in caplog.records)
    finally:
        await server.stop()


# -- Criterion: inbound NDJSON lines are consumed intact; unknown types get -----
# -- an ERROR answer and the session survives ------------------------------------


async def test_ready_connection_survives_unknown_inbound_messages() -> None:
    server = TestAtpServer()
    port = await server.start(peer_count=2)
    try:
        async with running_app([T1], _one_peer(port)) as c:
            await _wait_until(lambda: _manager(c).ready_count == 2)

            await server.send({"type": "hello", "payload": {"n": 42}})
            await server.send({"type": "future_phase_3_message", "payload": [1, 2, 3]})
            await _next_message(server, "error")
            await asyncio.sleep(0.1)

            assert _manager(c).ready_count == 2
            assert server.connection_count == 2
    finally:
        await server.stop()


# -- Criterion: the write path accepts framed NDJSON bytes on a READY channel ---


async def test_send_message_writes_framed_ndjson_to_peers() -> None:
    port = _free_port()
    async with running_app([T1], _one_peer(port)) as c:
        manager = _manager(c)

        # Channel down: the write path refuses.
        assert manager.send_message({"type": "phase31_probe", "n": 0}) is False

        server = TestAtpServer()
        await server.start(port=port)
        try:
            await _wait_until(lambda: manager.ready_count == 1)

            message = {"type": "phase31_probe", "n": 1}
            assert manager.send_message(message) is True
            assert await _next_message(server, "phase31_probe") == message
        finally:
            await server.stop()


# -- Criterion: each peer connection's state is observable through the REST API --


async def test_rest_reports_connection_states_through_the_lifecycle() -> None:
    port = _free_port()
    async with running_app([T1], _one_peer(port)) as c:
        async def _status_until(active_peers: int, timeout: float = 5.0) -> dict:
            async def _poll() -> dict:
                while True:
                    body = (await c.get("/api/atp/status")).json()
                    conn = body["connections"][0]
                    if conn["active_peers"] == active_peers:
                        return conn
                    await asyncio.sleep(0.02)

            return await asyncio.wait_for(_poll(), timeout)

        conn = await _status_until(0)
        assert conn == {
            "host": "127.0.0.1",
            "port": port,
            "state": "READY",
            "ready": True,
            "active_peers": 0,
        }

        server = TestAtpServer()
        await server.start(port=port)
        try:
            ready = await _status_until(1)
            assert ready["ready"] is True

            server.drop_client(0)
            await _status_until(0)
            await _status_until(1)
        finally:
            await server.stop()
