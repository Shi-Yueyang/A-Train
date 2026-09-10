"""Phase 3.2 acceptance tests — end-to-end BTM delivery to an ATP peer (atp-api.md §3.1).

A BTM payload injected through the equipment endpoint appears in the target
cab's ``TRAIN_STATE`` equipment snapshot; the payload stays opaque to the
simulator.
"""

from __future__ import annotations

import asyncio
import base64

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

PAYLOAD = bytes([0x01, 0x23, 0xA4, 0xFF, 0x00, 0x81, 0x72])


def _cabs(port: int) -> list[AtpEndpoint]:
    return [AtpEndpoint(cab_id, "127.0.0.1", port) for cab_id in (1, 2)]


def _manager(c):  # AtpManager
    return c.app.state.atp_manager


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(_poll(), timeout)


async def _next(server, mtype: str, timeout: float = 10.0, **match) -> dict:
    async def _poll() -> dict:
        while True:
            message = await server.wait_for_message(timeout=timeout)
            if message.get("type") == mtype and all(message.get(k) == v for k, v in match.items()):
                return message

    return await asyncio.wait_for(_poll(), timeout)


async def _no_more(server, mtype: str, within: float = 0.3) -> None:
    """Assert no message of ``mtype`` arrives within ``within`` seconds."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + within
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        try:
            message = await server.wait_for_message(timeout=remaining)
        except (TimeoutError, asyncio.TimeoutError):
            return
        assert message.get("type") != mtype, f"unexpected {mtype}: {message}"


async def _await_ready(server, c, ready_count: int = 1) -> None:
    # No handshake: the channel is READY as soon as TCP opens.
    await _wait_until(lambda: len(_manager(c).ready_endpoints) == ready_count)


async def _next_train_state_with_btm(server, cab_id: int, payload_b64: str, timeout: float = 10.0) -> dict:
    async def _poll() -> dict:
        while True:
            message = await server.wait_for_message(timeout=timeout)
            if message.get("type") != "train_state":
                continue
            btm = next(
                (
                    item["state"]
                    for item in message.get("equipment", [])
                    if item.get("key") == f"btm_{cab_id}"
                ),
                None,
            )
            if btm is not None and btm.get("payload_b64") == payload_b64:
                return message

    return await asyncio.wait_for(_poll(), timeout)


async def test_btm_delivery_is_embedded_in_train_state() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _cabs(port)) as c:
            await _await_ready(server, c, ready_count=2)

            data = base64.b64encode(PAYLOAD).decode("ascii")
            r = await c.post("/api/trains/TRAIN001/equipment/btm_1", json={"data": data})
            assert r.status_code == 200

            message = await _next_train_state_with_btm(server, 1, data)
            btm = next(item["state"] for item in message["equipment"] if item["key"] == "btm_1")
            assert btm["payload_b64"] == data
            assert btm["received_count"] == 1
            assert base64.b64decode(btm["payload_b64"], validate=True) == PAYLOAD

            await _no_more(server, "btm_rx")

            other = base64.b64encode(b"\xde\xad\xbe\xef").decode("ascii")
            r = await c.post(
                "/api/trains/TRAIN001/equipment/btm_2", json={"data": other}
            )
            assert r.status_code == 200
            message = await _next_train_state_with_btm(server, 2, other)
            btm = next(item["state"] for item in message["equipment"] if item["key"] == "btm_2")
            assert btm["payload_b64"] == other
            assert base64.b64decode(btm["payload_b64"]) == b"\xde\xad\xbe\xef"
    finally:
        await server.stop()


async def test_reset_resyncs_without_replaying_old_payload() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], [AtpEndpoint(1, "127.0.0.1", port)]) as c:
            await _await_ready(server, c)

            data = base64.b64encode(b"\x42").decode("ascii")
            await c.post("/api/trains/TRAIN001/equipment/btm_1", json={"data": data})
            await _next_train_state_with_btm(server, 1, data)

            await c.post("/api/simulation/reset")
            r = await c.post("/api/trains/TRAIN001/equipment/btm_1", json={"data": data})
            assert r.status_code == 200
            message = await _next_train_state_with_btm(server, 1, data)
            btm = next(item["state"] for item in message["equipment"] if item["key"] == "btm_1")
            assert btm["payload_b64"] == data
            await _no_more(server, "btm_rx")
    finally:
        await server.stop()
