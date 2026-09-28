"""Phase 3.2 acceptance tests — end-to-end BTM delivery to an ATP client (atp-api.md §3.1).

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
    return [AtpEndpoint("127.0.0.1", port)]


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


async def _await_ready(c, ready_count: int = 1) -> None:
    # No handshake: the channel is READY as soon as TCP opens.
    await _wait_until(lambda: _manager(c).ready_count == ready_count)


def _btm_state(message: dict, cab_id: int) -> dict:
    return next(
        item["state"]
        for item in message.get("equipment", [])
        if item.get("type") == "btm" and item.get("cab_id") == cab_id
    )


async def _next_train_state_with_btm(
    server, cab_id: int, payload_b64: str, timeout: float = 10.0
) -> dict:
    async def _poll() -> dict:
        while True:
            message = await server.wait_for_message(timeout=timeout)
            if message.get("type") != "train_state":
                continue
            entries = [
                item
                for item in message.get("equipment", [])
                if item.get("type") == "btm" and item.get("cab_id") == cab_id
            ]
            if entries and entries[0]["state"].get("payload_b64") == payload_b64:
                return message

    return await asyncio.wait_for(_poll(), timeout)


async def test_btm_delivery_is_embedded_in_train_state() -> None:
    server = TestAtpServer()
    port = await server.start(peer_count=2)
    try:
        async with running_app([T1], _cabs(port)) as c:
            await _await_ready(c, ready_count=2)

            data = base64.b64encode(PAYLOAD).decode("ascii")
            r = await c.post("/api/trains/TRAIN001/equipment/btm_1", json={"data": data})
            assert r.status_code == 200

            message = await _next_train_state_with_btm(server, 1, data)
            btm = _btm_state(message, 1)
            assert btm["payload_b64"] == data
            assert btm["received_count"] == 1
            assert "cab_id" not in btm and "key" not in message["equipment"][0]
            assert base64.b64decode(btm["payload_b64"], validate=True) == PAYLOAD

            await _no_more(server, "btm_rx")

            other = base64.b64encode(b"\xde\xad\xbe\xef").decode("ascii")
            r = await c.post("/api/trains/TRAIN001/equipment/btm_2", json={"data": other})
            assert r.status_code == 200
            message = await _next_train_state_with_btm(server, 2, other)
            btm = _btm_state(message, 2)
            assert btm["payload_b64"] == other
            assert base64.b64decode(btm["payload_b64"]) == b"\xde\xad\xbe\xef"
    finally:
        await server.stop()


async def test_reset_resyncs_without_replaying_old_payload() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], [AtpEndpoint("127.0.0.1", port)]) as c:
            await _await_ready(c)

            data = base64.b64encode(b"\x42").decode("ascii")
            await c.post("/api/trains/TRAIN001/equipment/btm_1", json={"data": data})
            await _next_train_state_with_btm(server, 1, data)

            await c.post("/api/simulation/reset")
            r = await c.post("/api/trains/TRAIN001/equipment/btm_1", json={"data": data})
            assert r.status_code == 200
            message = await _next_train_state_with_btm(server, 1, data)
            btm = _btm_state(message, 1)
            assert btm["payload_b64"] == data
            await _no_more(server, "btm_rx")
    finally:
        await server.stop()


TELEGRAM_41 = {
    "packets": [
        {
            "packet": 41,
            "q_dir": 1,
            "q_scale": 0,
            "d_leveltr": 1234,
            "m_leveltr": 2,
            "l_ackleveltr": 5,
        }
    ]
}
# Golden encoded 830-bit CTCS frame (tests/test_telegram_codec.py pins it).
TELEGRAM_41_HEX = "83007f8000000a501401349000507f80" + "00" * 88


async def test_telegram_json_is_encoded_and_delivered_to_atp() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _cabs(port)) as c:
            await _await_ready(server, c, ready_count=2)

            expected_b64 = base64.b64encode(bytes.fromhex(TELEGRAM_41_HEX)).decode("ascii")
            r = await c.post("/api/trains/TRAIN001/equipment/btm_1", json={"telegram": TELEGRAM_41})
            assert r.status_code == 200
            btm = next(item["state"] for item in r.json()["equipment"] if item["key"] == "btm_1")
            assert btm["payload_b64"] == expected_b64
            assert btm["received_count"] == 1

            message = await _next_train_state_with_btm(server, 1, expected_b64)
            btm = next(item["state"] for item in message["equipment"] if item["key"] == "btm_1")
            assert base64.b64decode(btm["payload_b64"]).hex() == TELEGRAM_41_HEX

            other = base64.b64encode(b"\x01").decode("ascii")
            r = await c.post("/api/trains/TRAIN001/equipment/btm_2", json={"data": other})
            assert r.status_code == 200
            message = await _next_train_state_with_btm(server, 2, other)
            btm = next(item["state"] for item in message["equipment"] if item["key"] == "btm_2")
            assert btm["payload_b64"] == other
    finally:
        await server.stop()


async def test_telegram_validation_errors_leave_state_unchanged() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _cabs(port)) as c:
            await _await_ready(server, c, ready_count=2)

            r = await c.post(
                "/api/trains/TRAIN001/equipment/btm_1",
                json={"telegram": {"packets": [{"packet": 5}]}},
            )
            assert r.status_code == 400
            assert "unknown packet number 5" in r.json()["detail"]

            r = await c.post(
                "/api/trains/TRAIN001/equipment/btm_1",
                json={"telegram": {"packets": [{"packet": 41, "q_dir": 2}]}},
            )
            assert r.status_code == 400
            assert "q_scale: missing required field" in r.json()["detail"]

            r = await c.post(
                "/api/trains/TRAIN001/equipment/btm_1",
                json={"telegram": TELEGRAM_41, "data": "AA=="},
            )
            assert r.status_code == 400
            assert "mutually exclusive" in r.json()["detail"]

            r = await c.post(
                "/api/trains/TRAIN001/equipment/left_door",
                json={"telegram": TELEGRAM_41},
            )
            assert r.status_code == 400
            assert "only accepted for btm" in r.json()["detail"]

            train = (await c.get("/api/trains/TRAIN001")).json()
            btm = next(item["state"] for item in train["equipment"] if item["key"] == "btm_1")
            assert btm["payload_b64"] is None
            assert btm["received_count"] == 0
    finally:
        await server.stop()
