"""Phase 3.1 acceptance tests — ATP communication channel (TODO.md §Phase 3.1).

The application opens one reconnecting TCP connection per configured cab,
completes the HELLO / HELLO_ACK handshake on each, retries while a server is
down, and isolates a dropped connection from the simulation and other cabs.
All observed against the real application and a production-protocol test TCP
server (§6.1); no production module is mocked.
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


def _cabs(endpoints_port: int) -> list[AtpEndpoint]:
    return [AtpEndpoint("TRAIN001", cab_id, "127.0.0.1", endpoints_port) for cab_id in (1, 2)]


def _manager(c):  # AtpManager
    return c.app.state.atp_manager


async def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(_poll(), timeout)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# -- Criterion: connection + handshake per configured cab -----------------------


async def test_hello_handshake_completes_for_each_configured_cab() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _cabs(port)) as c:
            hellos = [await server.wait_for_message() for _ in range(2)]
            assert all(m["type"] == "hello" for m in hellos)
            assert {m["cab_id"] for m in hellos} == {1, 2}
            assert all(m["train_id"] == "TRAIN001" for m in hellos)

            await server.send({"type": "hello_ack", "accepted": True})
            await _wait_until(lambda: len(_manager(c).ready_endpoints) == 2)
            assert _manager(c).ready_endpoints == frozenset({("TRAIN001", 1), ("TRAIN001", 2)})
    finally:
        await server.stop()


# -- Criterion: server down at startup is retried until it appears --------------


async def test_handshake_completes_when_server_appears_later() -> None:
    port = _free_port()
    async with running_app([T1], [AtpEndpoint("TRAIN001", 1, "127.0.0.1", port)]) as c:
        await asyncio.sleep(0.15)  # several refused retries already elapsed
        assert _manager(c).ready_endpoints == frozenset()

        server = TestAtpServer()
        await server.start(port=port)
        try:
            hello = await server.wait_for_message()
            assert hello["type"] == "hello" and hello["cab_id"] == 1
            await server.send({"type": "hello_ack", "accepted": True})
            await _wait_until(lambda: _manager(c).ready_endpoints == frozenset({("TRAIN001", 1)}))
        finally:
            await server.stop()


# -- Criterion: a dropped connection is reported, simulation and other ----------
# -- cabs keep running; the dropped cab reconnects and re-handshakes -------------


async def test_dropped_connection_is_reported_and_isolated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        with caplog.at_level(logging.INFO, logger="a_train.adapters.atp"):
            async with running_app([T1], _cabs(port)) as c:
                for _ in range(2):
                    await server.wait_for_message()
                await server.send({"type": "hello_ack", "accepted": True})
                await _wait_until(lambda: len(_manager(c).ready_endpoints) == 2)

                await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
                await c.post("/api/simulation/start")

                server.drop_client(0)
                await _wait_until(lambda: server.connection_count == 1)

                # The dropped cab reconnects and completes a fresh handshake.
                hello = await server.wait_for_message()
                assert hello["type"] == "hello"
                await server.send({"type": "hello_ack", "accepted": True})
                await _wait_until(lambda: len(_manager(c).ready_endpoints) == 2)
                assert server.connection_count == 2

                # The simulation never stopped: stepping still works.
                r = await c.post("/api/simulation/step", json={"delta": 0.5})
                assert r.status_code == 200
                status = (await c.get("/api/status")).json()
                assert status["simulation_time"] == pytest.approx(0.5)
                assert status["simulation_state"] == "RUNNING"

                # The drop and the recovery are reported through logs.
                assert any("closed by peer" in rec.getMessage() for rec in caplog.records)
                assert any("handshake complete" in rec.getMessage() for rec in caplog.records)
    finally:
        await server.stop()
