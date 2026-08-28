"""Phase 1 acceptance tests (TODO.md §Phase 1 passing criteria).

Covers run/pause/reset idempotency, MANUAL stepping, paused-time-freeze, and the
SCALED multiplier, all driven through the public REST API against the real
application.
"""

from __future__ import annotations

import asyncio

import pytest

FIXED_STEP = 0.05


async def _post(client, path, json=None):
    return await client.post(path, json=json)


async def test_manual_step_advances_time_and_observed_through_api(app_client) -> None:
    r = await _post(app_client, "/api/simulation/time-mode", json={"mode": "MANUAL"})
    assert r.status_code == 200
    assert r.json()["time_mode"] == "MANUAL"

    r = await _post(app_client, "/api/simulation/start")
    assert r.status_code == 200
    assert r.json()["simulation_state"] == "RUNNING"

    # 0.10 == two whole fixed steps (0.05 each) -> simulation time 0.10.
    r = await _post(app_client, "/api/simulation/step", json={"delta": 0.10})
    assert r.status_code == 200
    body = r.json()
    assert body["simulation_state"] == "RUNNING"
    assert body["simulation_time"] == pytest.approx(0.10, abs=1e-9)

    status = await app_client.get("/api/status")
    assert status.json()["simulation_time"] == pytest.approx(0.10, abs=1e-9)


async def test_run_pause_reset_are_idempotent(app_client) -> None:
    # run is idempotent
    assert (await _post(app_client, "/api/simulation/start")).status_code == 200
    s = (await _post(app_client, "/api/simulation/start")).json()
    assert s["simulation_state"] == "RUNNING"

    # pause is idempotent (also idempotent while stopped)
    assert (await _post(app_client, "/api/simulation/pause")).status_code == 200
    s = (await _post(app_client, "/api/simulation/pause")).json()
    assert s["simulation_state"] == "PAUSED"

    # reset is idempotent
    assert (await _post(app_client, "/api/simulation/reset")).status_code == 200
    s = (await _post(app_client, "/api/simulation/reset")).json()
    assert s["simulation_state"] == "STOPPED"
    assert s["simulation_time"] == 0.0


async def test_paused_simulation_does_not_advance(app_client) -> None:
    await _post(app_client, "/api/simulation/time-mode", json={"mode": "REALTIME"})
    await _post(app_client, "/api/simulation/start")
    await asyncio.sleep(0.15)
    paused = (await _post(app_client, "/api/simulation/pause")).json()
    assert paused["simulation_state"] == "PAUSED"
    elapsed = paused["simulation_time"]
    assert elapsed > 0.0

    await asyncio.sleep(0.15)
    status = (await app_client.get("/api/status")).json()
    assert status["simulation_state"] == "PAUSED"
    assert status["simulation_time"] == pytest.approx(elapsed, abs=1e-9)


async def test_scaled_multiplier_changes_elapsed_time(app_client) -> None:
    await _post(
        app_client,
        "/api/simulation/time-mode",
        json={"mode": "SCALED", "time_multiplier": 4.0},
    )
    await _post(app_client, "/api/simulation/start")
    await asyncio.sleep(0.25)
    paused = (await _post(app_client, "/api/simulation/pause")).json()
    # 0.25s wall * 4 == ~1.0s simulated, snapped to the 0.05 fixed-step grid.
    assert paused["simulation_time"] == pytest.approx(1.0, abs=0.3)


async def test_reset_restores_initial_state(app_client) -> None:
    await _post(app_client, "/api/simulation/time-mode", json={"mode": "MANUAL"})
    await _post(app_client, "/api/simulation/start")
    await _post(app_client, "/api/simulation/step", json={"delta": 0.20})

    s = (await _post(app_client, "/api/simulation/reset")).json()
    assert s["simulation_state"] == "STOPPED"
    assert s["simulation_time"] == 0.0


async def test_invalid_input_is_rejected_without_state_change(app_client) -> None:
    # step requires MANUAL mode
    await _post(app_client, "/api/simulation/time-mode", json={"mode": "REALTIME"})
    r = await _post(app_client, "/api/simulation/step", json={"delta": 0.05})
    assert r.status_code == 400

    # SCALED requires a positive multiplier
    r = await _post(
        app_client,
        "/api/simulation/time-mode",
        json={"mode": "SCALED", "time_multiplier": 0},
    )
    assert r.status_code == 400

    # unknown mode
    r = await _post(app_client, "/api/simulation/time-mode", json={"mode": "WARP"})
    assert r.status_code == 400

    # state unchanged: still REALTIME, stopped, zero time
    s = (await app_client.get("/api/status")).json()
    assert s["time_mode"] == "REALTIME"
    assert s["simulation_state"] == "STOPPED"
    assert s["simulation_time"] == 0.0
