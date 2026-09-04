"""Phase 3.2 acceptance tests — end-to-end BTM delivery to an ATP peer (§4.5).

A BTM payload injected through the equipment endpoint appears on the target
cab's connection as a ``BTM_RX`` line whose decoded data equals the delivered
bytes; other cabs receive nothing, and the payload stays opaque to the
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
    return [AtpEndpoint("TRAIN001", cab_id, "127.0.0.1", port) for cab_id in (1, 2)]


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


async def test_btm_delivery_publishes_btm_rx_to_the_target_cab() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], _cabs(port)) as c:
            await _await_ready(server, c, ready_count=2)

            data = base64.b64encode(PAYLOAD).decode("ascii")
            r = await c.post("/api/trains/TRAIN001/equipment/btm", json={"cab_id": 1, "data": data})
            assert r.status_code == 200

            message = await _next(server, "btm_rx")
            assert message["data"] == data
            assert base64.b64decode(message["data"], validate=True) == PAYLOAD

            # No second BTM was delivered: no further btm_rx lines appear.
            await _no_more(server, "btm_rx")

            # A delivery to cab 2 reaches the other connection.
            other = base64.b64encode(b"\xde\xad\xbe\xef").decode("ascii")
            r = await c.post(
                "/api/trains/TRAIN001/equipment/btm", json={"cab_id": 2, "data": other}
            )
            assert r.status_code == 200
            message = await _next(server, "btm_rx")
            assert base64.b64decode(message["data"]) == b"\xde\xad\xbe\xef"
    finally:
        await server.stop()


async def test_reset_resyncs_without_replaying_old_payload() -> None:
    server = TestAtpServer()
    port = await server.start()
    try:
        async with running_app([T1], [AtpEndpoint("TRAIN001", 1, "127.0.0.1", port)]) as c:
            await _await_ready(server, c)

            data = base64.b64encode(b"\x42").decode("ascii")
            await c.post("/api/trains/TRAIN001/equipment/btm", json={"cab_id": 1, "data": data})
            await _next(server, "btm_rx")

            # Reset clears the delivery count; the resync must not re-send.
            await c.post("/api/simulation/reset")
            r = await c.post("/api/trains/TRAIN001/equipment/btm", json={"cab_id": 1, "data": data})
            assert r.status_code == 200
            message = await _next(server, "btm_rx")  # exactly one new delivery
            assert message["data"] == data
            await _no_more(server, "btm_rx")
    finally:
        await server.stop()
