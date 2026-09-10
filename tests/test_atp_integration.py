"""Phase 3.2 acceptance tests — ATP protocol and content publishing.

Channels are READY the moment TCP opens -- no handshake. Manual steps
publish ``TRAIN_STATE`` per cab; inbound ``ATP_COMMAND`` messages drive the
train through the core; malformed and unexpected input is answered with
``ERROR`` without stopping the simulation or another cab's connection. All
observed through the real application and the production-protocol test TCP
server (§6.1); no production module is mocked.
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
                assert "equipment" in state
                assert "door" not in state
                assert "stcs_atp" not in state
    finally:
        await server.stop()


# -- Criterion: inbound ATP_COMMAND drives the train through the core (atp-api.md §4.1) --


async def test_atp_atp_command_is_applied_by_the_core() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _one_cab(port)) as c:
            await _await_ready(server, c)

            await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
            await server.send({"type": "atp_command", "cab_id": 1, "drive_demand": 0.5})

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


async def test_invalid_atp_command_answers_error() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _one_cab(port)) as c:
            await _await_ready(server, c)

            # Out-of-range demand.
            await server.send({"type": "atp_command", "cab_id": 1, "drive_demand": 5.0})
            err = await _next(server, "error", code="invalid_atp_command")
            assert "[-1.0, 1.0]" in err["detail"]

            # Identity mismatch: cab 2 on the cab 1 channel.
            await server.send({"type": "atp_command", "cab_id": 2, "drive_demand": 0.5})
            err = await _next(server, "error", code="invalid_atp_command")
            assert "cab_id" in err["detail"]

            # Empty command.
            await server.send({"type": "atp_command"})
            await _next(server, "error", code="invalid_atp_command")

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


# -- Work item: atp_signal drives the core stcs_atp (atp-api.md §4.2) --------


async def _wait_stcs_atp(c, **expected: object) -> dict:
    """Poll REST until the train's recorded ATP command matches ``expected``."""

    async def _poll() -> dict:
        while True:
            equipment = (await c.get("/api/trains/TRAIN001")).json()["equipment"]
            state = next(item["state"] for item in equipment if item["key"] == "stcs_atp_1")
            if all(state.get(key) == value for key, value in expected.items()):
                return state
            await asyncio.sleep(0.02)

    return await asyncio.wait_for(_poll(), timeout=5.0)


async def _next_train_state_with_stcs(server, expected: dict[str, object]) -> dict:
    async def _poll() -> dict:
        while True:
            message = await server.wait_for_message(timeout=10.0)
            if message.get("type") != "train_state":
                continue
            state = next(
                item["state"]
                for item in message.get("equipment", [])
                if item.get("key") == "stcs_atp_1"
            )
            if all(state.get(key) == value for key, value in expected.items()):
                return message

    return await asyncio.wait_for(_poll(), timeout=10.0)


async def test_atp_signal_asserts_state_and_shows_in_train_state() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _one_cab(port)) as c:
            await _await_ready(server, c)
            await _next(server, "train_state")  # drain the catch-up publish

            # The raw signal is recorded unchanged by STCS ATP.
            await server.send({"type": "atp_command", "cab_id": 1, "atp_signal": "0111"})
            await _wait_stcs_atp(c, last_command="0111")

            # The protection line rides every subsequent TRAIN_STATE.
            await _next_train_state_with_stcs(server, {"last_command": "0111"})

            # A shorter signal replaces the previous raw signal.
            await server.send({"type": "atp_command", "cab_id": 1, "atp_signal": "010"})
            state = await _wait_stcs_atp(c, last_command="010")
            r = await c.post("/api/simulation/step", json={"delta": 0.1})
            assert r.status_code == 200
            equipment = (await c.get("/api/trains/TRAIN001")).json()["equipment"]
            assert next(item["state"] for item in equipment if item["key"] == "stcs_atp_1") == state
    finally:
        await server.stop()


async def test_invalid_atp_signal_answers_error() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _one_cab(port)) as c:
            await _await_ready(server, c)

            await server.send({"type": "atp_command", "cab_id": 1, "atp_signal": "00120"})
            err = await _next(server, "error", code="invalid_atp_command")
            assert "atp_signal" in err["detail"]

            await server.send({"type": "atp_command", "cab_id": 1, "atp_signal": ""})
            err = await _next(server, "error", code="invalid_atp_command")
            assert "atp_signal" in err["detail"]

            assert _manager(c).ready_endpoints == frozenset({("TRAIN001", 1)})
    finally:
        await server.stop()
