"""Phase 3.1 acceptance tests — ATP communication channel (TODO.md §Phase 3.1).

The application opens one reconnecting TCP connection per configured cab and
is READY the moment TCP opens (no handshake); it retries while a server is
down and isolates a dropped connection from the simulation and other cabs.
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


# -- Criterion: each configured cab reaches READY as soon as TCP opens ----------


async def test_channels_become_ready_without_handshake() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _cabs(port)) as c:
            await _wait_until(lambda: len(_manager(c).ready_endpoints) == 2)
            assert _manager(c).ready_endpoints == frozenset({("TRAIN001", 1), ("TRAIN001", 2)})
            assert server.connection_count == 2

            # Content flows on the fresh connections with no handshake first.
            seen_cabs: set[int] = set()
            while len(seen_cabs) < 2:
                state = await _next_message(server, "train_state")
                assert state["train_id"] == "TRAIN001"
                seen_cabs.add(state["cab_id"])
    finally:
        await server.stop()


# -- Criterion: server down at startup is retried until it appears --------------


async def test_channel_becomes_ready_when_server_appears_later() -> None:
    port = _free_port()
    async with running_app([T1], [AtpEndpoint("TRAIN001", 1, "127.0.0.1", port)]) as c:
        await asyncio.sleep(0.15)  # several refused retries already elapsed
        assert _manager(c).ready_endpoints == frozenset()

        server = TestAtpServer()
        await server.start(port=port)
        try:
            await _wait_until(lambda: _manager(c).ready_endpoints == frozenset({("TRAIN001", 1)}))
            assert server.connection_count == 1
        finally:
            await server.stop()


# -- Criterion: a dropped connection is reported, simulation and other ----------
# -- cabs keep running; the dropped cab reconnects on its own --------------------


async def test_dropped_connection_is_reported_and_isolated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        with caplog.at_level(logging.INFO, logger="a_train.adapters.atp"):
            async with running_app([T1], _cabs(port)) as c:
                await _wait_until(lambda: len(_manager(c).ready_endpoints) == 2)

                await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
                await c.post("/api/simulation/start")

                server.drop_client(0)
                await _wait_until(lambda: len(_manager(c).ready_endpoints) < 2)

                # The dropped cab re-establishes without a restart.
                await _wait_until(lambda: len(_manager(c).ready_endpoints) == 2)
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
    port = await server.start()
    try:
        async with running_app([T1], _cabs(port)) as c:
            await _wait_until(lambda: len(_manager(c).ready_endpoints) == 2)

            await server.send({"type": "heartbeat"})
            await server.send({"type": "future_phase_3_message", "payload": [1, 2, 3]})
            await _next_message(server, "error")
            await asyncio.sleep(0.1)

            assert len(_manager(c).ready_endpoints) == 2
            assert server.connection_count == 2
    finally:
        await server.stop()


# -- Criterion: the write path accepts framed NDJSON bytes on a READY channel ---


async def test_send_message_writes_framed_ndjson_to_peer() -> None:
    port = _free_port()
    async with running_app([T1], [AtpEndpoint("TRAIN001", 1, "127.0.0.1", port)]) as c:
        manager = _manager(c)

        # Channel down: the write path refuses.
        assert manager.send_message("TRAIN001", 1, {"type": "phase31_probe", "n": 0}) is False

        server = TestAtpServer()
        await server.start(port=port)
        try:
            await _wait_until(lambda: manager.ready_endpoints == frozenset({("TRAIN001", 1)}))

            message = {"type": "phase31_probe", "n": 1}
            assert manager.send_message("TRAIN001", 1, message) is True
            assert await _next_message(server, "phase31_probe") == message

            # Unconfigured cab: refused, session untouched.
            assert manager.send_message("TRAIN001", 2, message) is False
            assert manager.ready_endpoints == frozenset({("TRAIN001", 1)})
        finally:
            await server.stop()


# -- Criterion: each cab's connection state is observable through the REST API --


async def test_rest_reports_connection_states_through_the_lifecycle() -> None:
    port = _free_port()
    async with running_app([T1], [AtpEndpoint("TRAIN001", 1, "127.0.0.1", port)]) as c:
        await asyncio.sleep(0.15)  # refused attempts put the client in backoff

        async def _status_until(*states: str, timeout: float = 5.0) -> dict:
            async def _poll() -> dict:
                while True:
                    body = (await c.get("/api/atp/status")).json()
                    conn = body["connections"][0]
                    if conn["state"] in states:
                        return conn
                    await asyncio.sleep(0.02)

            return await asyncio.wait_for(_poll(), timeout)

        conn = await _status_until("DISCONNECTED", "CONNECTING")
        assert conn["train_id"] == "TRAIN001"
        assert conn["cab_id"] == 1
        assert conn["host"] == "127.0.0.1" and conn["port"] == port
        assert conn["ready"] is False

        server = TestAtpServer()
        await server.start(port=port)
        try:
            ready = await _status_until("READY")
            assert ready["ready"] is True

            server.drop_client(0)
            await _status_until("DISCONNECTED", "CONNECTING")
        finally:
            await server.stop()
