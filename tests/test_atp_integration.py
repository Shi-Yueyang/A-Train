"""Phase 3.2 acceptance tests — ATP protocol and content publishing.

Channels are READY the moment TCP opens -- no handshake. Every READY peer
receives identical whole-train ``TRAIN_STATE`` broadcasts; inbound
``ATP_COMMAND`` messages state their target ``cab_id`` in the message and
drive the train through the core; malformed and unexpected input is answered
with ``ERROR`` without stopping the simulation or another connection. All
observed through the real application and the production-protocol test TCP
server (§6.1); no production module is mocked.
"""

from __future__ import annotations

import asyncio
import time

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


def _two_peers(port: int) -> list[AtpEndpoint]:
    return [AtpEndpoint("127.0.0.1", port), AtpEndpoint("127.0.0.1", port)]


def _one_peer(port: int) -> list[AtpEndpoint]:
    return [AtpEndpoint("127.0.0.1", port)]


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


async def _await_ready(c, ready_count: int = 1) -> None:
    # No handshake: the channel is READY as soon as TCP opens.
    await _wait_until(lambda: _manager(c).ready_count == ready_count)


def _wire_entry(message: dict, eq_type: str, cab_id: int) -> dict:
    entry = next(
        item
        for item in message.get("equipment", [])
        if item.get("type") == eq_type and item.get("cab_id") == cab_id
    )
    return entry["state"]


# -- Criterion: each READY peer receives the identical whole-train broadcast -----


async def test_manual_step_publishes_identical_train_state_to_both_peers() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _two_peers(port)) as c:
            await _await_ready(c, ready_count=2)

            await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
            await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "drive_demand": 1.0})
            r = await c.post("/api/simulation/step", json={"delta": 0.5})
            assert r.status_code == 200

            # v = 1.5 * 0.5 = 0.75 m/s; x = 0.5 * 1.5 * 0.5^2 = 0.1875 m
            # One per-cab copy each: byte-identical broadcast on every peer.
            first = await _next(server, "train_state", position=pytest.approx(0.1875))
            second = await _next(server, "train_state", position=pytest.approx(0.1875))
            assert first == second

            state = first
            assert state["speed"] == pytest.approx(0.75)
            assert state["acceleration"] == pytest.approx(1.5)
            assert state["direction"] == "forward"
            assert set(state) == {
                "type",
                "speed",
                "acceleration",
                "position",
                "direction",
                "equipment",
            }
            equipment = state["equipment"]
            assert {(item["type"], item["cab_id"]) for item in equipment} == {
                ("btm", 1),
                ("btm", 2),
                ("stcs_atp_duo", 1),
                ("stcs_atp_duo", 2),
            }
            assert all(set(item) == {"type", "cab_id", "state"} for item in equipment)
            assert all("cab_id" not in item["state"] for item in equipment)
            assert all(
                set(item["state"]) == {"train_out_signal"}
                for item in equipment
                if item["type"].startswith("stcs_atp")
            )
            assert "door" not in state
    finally:
        await server.stop()


# -- Criterion: inbound ATP_COMMAND drives the train through the core (atp-api.md §4.1) --


async def test_atp_atp_command_is_applied_by_the_core() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _one_peer(port)) as c:
            await _await_ready(c)

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
        async with running_app([T1], _one_peer(port)) as c:
            await _await_ready(c)

            # Out-of-range demand.
            await server.send({"type": "atp_command", "cab_id": 1, "drive_demand": 5.0})
            err = await _next(server, "error", code="invalid_atp_command")
            assert "[-1.0, 1.0]" in err["detail"]

            # cab_id is required: the channel carries no cab binding.
            await server.send({"type": "atp_command", "drive_demand": 0.5})
            err = await _next(server, "error", code="invalid_atp_command")
            assert "cab_id is required" in err["detail"]

            # train_id is no longer part of the protocol.
            await server.send({"type": "atp_command", "train_id": "TRAIN001", "cab_id": 1})
            err = await _next(server, "error", code="invalid_atp_command")
            assert "train_id" in err["detail"]

            # An unconfigured cab is rejected by the core, not the session.
            await server.send({"type": "atp_command", "cab_id": 99, "drive_demand": 0.5})
            await _next(server, "error", code="command_rejected")

            # No action at all.
            await server.send({"type": "atp_command", "cab_id": 1})
            await _next(server, "error", code="invalid_atp_command")

            # Nothing was applied and the simulation still runs.
            train = (await c.get("/api/trains/TRAIN001")).json()
            assert train["drive_demand"] == 0.0
            r = await c.post("/api/simulation/step", json={"delta": 0.05})
            assert r.status_code == 200
    finally:
        await server.stop()


# -- Criterion: malformed data is reported without stopping sim or peers ---------


async def test_malformed_line_reported_without_stopping_anything() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _two_peers(port)) as c:
            await _await_ready(c, ready_count=2)

            await server.send_raw("this is not json\n")
            errors = [await _next(server, "error"), await _next(server, "error")]
            assert {e["code"] for e in errors} == {"malformed_message"}

            # Both channels stay READY and the simulation is unaffected.
            assert _manager(c).ready_count == 2
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
        async with running_app([T1], _one_peer(port)) as c:
            await _await_ready(c)

            await server.send({"type": "totally_unknown", "cab_id": 1})
            await _next(server, "error", code="unknown_message_type")
            assert _manager(c).ready_count == 1
    finally:
        await server.stop()


# -- Work item: atp_signal drives the core stcs_atp (atp-api.md §4.2) --------


async def _wait_stcs_atp(c, **expected: object) -> dict:
    """Poll REST until the train's recorded ATP command matches ``expected``."""

    async def _poll() -> dict:
        while True:
            equipment = (await c.get("/api/trains/TRAIN001")).json()["equipment"]
            state = next(item["state"] for item in equipment if item["key"] == "stcs_atp_duo_1")
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
            state = _wire_entry(message, "stcs_atp_duo", 1)
            if all(state.get(key) == value for key, value in expected.items()):
                return message

    return await asyncio.wait_for(_poll(), timeout=10.0)


async def test_atp_signal_asserts_state_and_shows_in_train_state() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _one_peer(port)) as c:
            await _await_ready(c)
            await _next(server, "train_state")  # drain the catch-up publish

            # The raw signal is recorded unchanged by STCS ATP, together with
            # the wall-clock time at which the core applied it.
            before = time.time()
            await server.send({"type": "atp_command", "cab_id": 1, "atp_signal": "0111"})
            core_state = await _wait_stcs_atp(c, last_command="0111")
            assert before <= core_state["last_command_time"] <= time.time()

            # Only the train-out feedback line rides subsequent TRAIN_STATEs;
            # the raw command and named state maps stay off the wire.
            message = await _next_train_state_with_stcs(
                server, {"train_out_signal": core_state["train_out_signal"]}
            )
            state = _wire_entry(message, "stcs_atp_duo", 1)
            assert state == {"train_out_signal": core_state["train_out_signal"]}

            # A shorter signal replaces the previous raw signal in the core
            # and restamps its arrival time with the current wall-clock time.
            await server.send({"type": "atp_command", "cab_id": 1, "atp_signal": "010"})
            before = time.time()
            second_state = await _wait_stcs_atp(c, last_command="010")
            assert before <= second_state["last_command_time"] <= time.time()
            r = await c.post("/api/simulation/step", json={"delta": 0.1})
            assert r.status_code == 200
    finally:
        await server.stop()


async def test_invalid_atp_signal_answers_error() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _one_peer(port)) as c:
            await _await_ready(c)

            await server.send({"type": "atp_command", "cab_id": 1, "atp_signal": "00120"})
            err = await _next(server, "error", code="invalid_atp_command")
            assert "atp_signal" in err["detail"]

            await server.send({"type": "atp_command", "cab_id": 1, "atp_signal": ""})
            err = await _next(server, "error", code="invalid_atp_command")
            assert "atp_signal" in err["detail"]

            assert _manager(c).ready_count == 1
    finally:
        await server.stop()


# -- Criterion: any peer may act for any cab; the train enforces cabs ------------


async def test_peer_can_command_the_other_cab() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _one_peer(port)) as c:
            await _await_ready(c)
            await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})

            await server.send({"type": "atp_command", "cab_id": 2, "atp_signal": "1"})
            r = await c.post("/api/simulation/step", json={"delta": 0.1})
            assert r.status_code == 200
            equipment = (await c.get("/api/trains/TRAIN001")).json()["equipment"]
            states = {item["key"]: item["state"] for item in equipment}
            assert states["stcs_atp_duo_2"]["last_command"] == "1"
            assert states["stcs_atp_duo_1"]["last_command"] is None
    finally:
        await server.stop()
