"""Phase 3.2 acceptance tests — ATP protocol and content publishing.

Channels are READY the moment TCP opens -- no handshake. Manual steps
publish ``TRAIN_STATE`` per cab; inbound ``TRAIN_COMMAND`` messages drive the
train through the core; malformed and unexpected input is answered with
``ERROR`` without stopping the simulation or another cab's connection;
``HEARTBEAT`` keepalive runs in both directions. All observed through the
real application and the production-protocol test TCP server (§6.1); no
production module is mocked.
"""

from __future__ import annotations

import asyncio

import pytest

from a_train.adapters.atp.manager import AtpEndpoint
from a_train.domain.train import TrainConfig
from tests.support.app import running_app
from tests.support.atp_server import TestAtpServer

T1 = TrainConfig(
    train_id="TRAIN001",
    cab_ids=(1, 2),
    initial_active_cab=1,
    max_traction_accel=1.5,
    max_decel=2.0,
    initial_position=0.0,
)


def _cabs(port: int) -> list[AtpEndpoint]:
    return [AtpEndpoint("TRAIN001", cab_id, "127.0.0.1", port) for cab_id in (1, 2)]


def _one_cab(port: int) -> list[AtpEndpoint]:
    return [AtpEndpoint("TRAIN001", 1, "127.0.0.1", port)]


def _manager(c):  # AtpManager
    return c.app.state.atp_manager


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(_poll(), timeout)


async def _next(server, mtype: str, timeout: float = 10.0, **match) -> dict:
    """Wait for the next received message of ``mtype`` matching extra fields."""

    async def _poll() -> dict:
        while True:
            message = await server.wait_for_message(timeout=timeout)
            if message.get("type") == mtype and all(message.get(k) == v for k, v in match.items()):
                return message

    return await asyncio.wait_for(_poll(), timeout)


async def _await_ready(server, c, ready_count: int = 1) -> None:
    # No handshake: the channel is READY as soon as TCP opens.
    await _wait_until(lambda: len(_manager(c).ready_endpoints) == ready_count)


# -- Criterion: a manual step produces correctly framed TRAIN_STATE per cab -----


async def test_manual_step_publishes_train_state_for_each_cab() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _cabs(port)) as c:
            await _await_ready(server, c, ready_count=2)

            await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
            await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "drive_demand": 1.0})
            r = await c.post("/api/simulation/step", json={"delta": 0.5})
            assert r.status_code == 200

            # v = 1.5 * 0.5 = 0.75 m/s; x = 0.5 * 1.5 * 0.5^2 = 0.1875 m
            for cab_id in (1, 2):
                state = await _next(
                    server,
                    "train_state",
                    train_id="TRAIN001",
                    cab_id=cab_id,
                    position=pytest.approx(0.1875),
                )
                assert state["speed"] == pytest.approx(0.75)
                assert state["acceleration"] == pytest.approx(1.5)
                assert state["direction"] == "forward"
    finally:
        await server.stop()


# -- Criterion: inbound TRAIN_COMMAND drives the train through the core (§4.1) ---


async def test_atp_train_command_is_applied_by_the_core() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _one_cab(port)) as c:
            await _await_ready(server, c)

            await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
            await server.send({"type": "train_command", "cab_id": 1, "drive_demand": 0.5})

            # The demand must reach the train through the core, not REST.
            async def _demand_applied() -> bool:
                train = (await c.get("/api/trains/TRAIN001")).json()
                return train["drive_demand"] == pytest.approx(0.5)

            applied = False
            for _ in range(250):
                if await _demand_applied():
                    applied = True
                    break
                await asyncio.sleep(0.02)
            assert applied

            await c.post("/api/simulation/step", json={"delta": 1.0})
            train = (await c.get("/api/trains/TRAIN001")).json()
            assert train["position"] > 0.0
    finally:
        await server.stop()


# -- Criterion: invalid inbound commands are answered with ERROR ----------------


async def test_invalid_train_command_answers_error() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _one_cab(port)) as c:
            await _await_ready(server, c)

            # Out-of-range demand.
            await server.send({"type": "train_command", "cab_id": 1, "drive_demand": 5.0})
            err = await _next(server, "error", code="invalid_train_command")
            assert "[-1.0, 1.0]" in err["detail"]

            # Identity mismatch: cab 2 on the cab 1 channel.
            await server.send({"type": "train_command", "cab_id": 2, "drive_demand": 0.5})
            err = await _next(server, "error", code="invalid_train_command")
            assert "cab_id" in err["detail"]

            # Empty command.
            await server.send({"type": "train_command"})
            await _next(server, "error", code="invalid_train_command")

            # Nothing was applied and the simulation still runs.
            train = (await c.get("/api/trains/TRAIN001")).json()
            assert train["drive_demand"] == 0.0
            r = await c.post("/api/simulation/step", json={"delta": 0.05})
            assert r.status_code == 200
    finally:
        await server.stop()


# -- Criterion: malformed data is reported without stopping sim or other cabs ---


async def test_malformed_line_reported_without_stopping_anything() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _cabs(port)) as c:
            await _await_ready(server, c, ready_count=2)

            await server.send_raw("this is not json\n")
            errors = [await _next(server, "error"), await _next(server, "error")]
            assert {e["code"] for e in errors} == {"malformed_message"}

            # Both channels stay READY and the simulation is unaffected.
            assert len(_manager(c).ready_endpoints) == 2
            await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
            r = await c.post("/api/simulation/step", json={"delta": 0.1})
            assert r.status_code == 200
            assert (await c.get("/api/status")).json()["simulation_time"] == pytest.approx(0.1)
    finally:
        await server.stop()


async def test_unknown_message_type_answered_with_error() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _one_cab(port)) as c:
            await _await_ready(server, c)

            await server.send({"type": "totally_unknown", "cab_id": 1})
            await _next(server, "error", code="unknown_message_type")
            assert _manager(c).ready_endpoints == frozenset({("TRAIN001", 1)})
    finally:
        await server.stop()


# -- Work item: HEARTBEAT / HEARTBEAT_ACK keepalive ------------------------------


async def test_heartbeat_keepalive_both_directions() -> None:
    server = TestAtpServer(ack_heartbeats=True)
    port = await server.start()
    try:
        async with running_app([T1], _one_cab(port), atp_heartbeat_interval=0.1) as c:
            await _await_ready(server, c)

            beat = await _next(server, "heartbeat")
            assert beat["train_id"] == "TRAIN001" and beat["cab_id"] == 1

            await asyncio.sleep(0.5)  # several keepalive beats, all acked
            assert _manager(c).ready_endpoints == frozenset({("TRAIN001", 1)})

            await server.send({"type": "heartbeat", "train_id": "TRAIN001", "cab_id": 1})
            await _next(server, "heartbeat_ack")
    finally:
        await server.stop()


async def test_heartbeat_without_ack_reconnects() -> None:
    server = TestAtpServer()  # never acks heartbeats
    port = await server.start()
    try:
        async with running_app([T1], _one_cab(port), atp_heartbeat_interval=0.05) as c:
            await _await_ready(server, c)

            # The overdue ack drops the session, then the link comes back up.
            manager = _manager(c)

            async def _wait_dropped_then_ready() -> None:
                await _wait_until(lambda: manager.ready_endpoints == frozenset())
                await _wait_until(lambda: manager.ready_endpoints == frozenset({("TRAIN001", 1)}))

            await asyncio.wait_for(_wait_dropped_then_ready(), 5.0)
            assert (await c.get("/api/status")).status_code == 200
    finally:
        await server.stop()
