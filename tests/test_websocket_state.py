"""Phase 2 acceptance tests — live state over WebSocket (TODO.md §Phase 2).

A slow WebSocket test client must not prevent another client from receiving a
later state snapshot or delay manual ``step(delta)`` completion. The core
publishes to each subscriber's bounded queue with a non-blocking put that drops
the oldest snapshot when full, and every client has its own queue and publisher
task, so a stalled client can never block physics.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from a_train.domain.train import TrainConfig
from tests.support.app import running_app
from tests.support.ws import ws_connect

FIXED_STEP = 0.05

T1 = TrainConfig(
    train_id="TRAIN001",
    cab_ids=(1,),
    initial_active_cab=1,
    max_traction_accel=1.0,
    max_decel=2.0,
)


async def _manual_start(c) -> None:
    await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
    await c.post("/api/simulation/start")


async def test_websocket_receives_initial_snapshot() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        async with ws_connect(c.app, "/ws") as ws:
            first = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
            assert first["simulation_state"] == "RUNNING"
            assert len(first["trains"]) == 1
            assert first["trains"][0]["train_id"] == "TRAIN001"


async def test_websocket_receives_snapshot_after_step() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        async with ws_connect(c.app, "/ws") as ws:
            await ws.receive_json()  # initial

            await c.post("/api/simulation/step", json={"delta": FIXED_STEP})
            msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
            assert msg["simulation_time"] == pytest.approx(FIXED_STEP, abs=1e-9)


async def test_slow_websocket_client_cannot_block_others_or_step() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        # The "slow" client connects but never reads after the initial snapshot,
        # so its publisher task blocks on send backpressure.
        async with ws_connect(c.app, "/ws", send_maxsize=1) as slow:
            await slow.receive_json()  # consume the seeded initial snapshot

            async with ws_connect(c.app, "/ws", send_maxsize=1) as fast:
                await fast.receive_json()  # consume the seeded initial snapshot

                # Step must return promptly even while the slow client is stuck.
                start = time.monotonic()
                await c.post("/api/simulation/step", json={"delta": FIXED_STEP})
                elapsed = time.monotonic() - start
                assert elapsed < 1.0, f"step was delayed by {elapsed:.3f}s"

                # The fast client still receives the post-step snapshot.
                msg = await asyncio.wait_for(fast.receive_json(), timeout=2.0)
                assert msg["simulation_time"] == pytest.approx(FIXED_STEP, abs=1e-9)

            # A second step after the fast client leaves also completes promptly.
            start = time.monotonic()
            await c.post("/api/simulation/step", json={"delta": FIXED_STEP})
            elapsed = time.monotonic() - start
            assert elapsed < 1.0
