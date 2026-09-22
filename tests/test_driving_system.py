"""Driving-system equipment through the public API (§3.5, web-api.md, atp-api.md §3.1).

The driver-room equipment owns three handles (mode, direction, acceleration),
maps them through the cab's track facing into a train-target intent, and
overwrites the legacy drive-demand lever while engaged. All behavior is
observed through REST/WebSocket/ATP against the real application; no core or
domain object is touched directly (§6.1).
"""

from __future__ import annotations

import pytest

from a_train.domain.train import TrainConfig
from tests.support.app import running_app

FIXED_STEP = 0.05

# max_traction_accel=1.0, max_decel=2.0. Default facings: cab1 +1, cab2 -1.
T1 = TrainConfig(
    train_id="TRAIN001",
    cab_ids=(1, 2),
    initial_active_cab=1,
    max_traction_accel=1.0,
    max_decel=2.0,
    initial_position=0.0,
)


async def _manual_start(c) -> None:
    await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
    await c.post("/api/simulation/start")


async def _train(c) -> dict:
    return (await c.get("/api/trains/TRAIN001")).json()


async def _handles(c, cab: int, **body) -> tuple[int, dict]:
    r = await c.post(f"/api/trains/TRAIN001/equipment/driving_{cab}", json=body)
    return r.status_code, r.json()


async def _step(c, delta: float) -> None:
    await c.post("/api/simulation/step", json={"delta": delta})


def _equipment(snap: dict, key: str) -> dict:
    return next(e for e in snap["equipment"] if e["key"] == key)


def _out_bits(snap: dict, cab: int = 1) -> dict[str, bool]:
    atp = _equipment(snap, f"stcs_atp_duo_{cab}")
    return {s["name"]: s["value"] for s in atp["state"]["train_out_states"]}


# -- Standard consist carries one driving system per cab, released ------------


async def test_driving_system_is_standard_fit_and_initially_off() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        snap = await _train(c)
        for cab in (1, 2):
            d = _equipment(snap, f"driving_{cab}")["state"]
            assert d["mode"] == "off"
            assert d["direction"] == "off"
            assert d["acceleration"] == 0.0
        assert _equipment(snap, "driving_1")["state"]["facing"] == "forward"
        assert _equipment(snap, "driving_2")["state"]["facing"] == "backward"


# -- Traction mapped through the cab facing ------------------------------------


async def test_traction_forward_from_cab1_moves_increasing_position() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        status, _ = await _handles(c, 1, mode="traction", direction="forward", acceleration=1.0)
        assert status == 200
        await _step(c, FIXED_STEP)
        snap = await _train(c)
        assert snap["acceleration"] == pytest.approx(1.0)
        assert snap["speed"] == pytest.approx(0.05)
        assert snap["position"] > 0.0
        assert snap["direction"] == "forward"


async def test_traction_forward_from_cab2_moves_decreasing_position() -> None:
    # cab 2 faces backward, so its "forward" handle drives the train rearward.
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _handles(c, 2, mode="traction", direction="forward", acceleration=1.0)
        await _step(c, FIXED_STEP)
        snap = await _train(c)
        assert snap["acceleration"] == pytest.approx(-1.0)
        assert snap["speed"] < 0.0
        assert snap["position"] < 0.0
        assert snap["direction"] == "backward"


async def test_traction_handle_backward_reverses_the_travel_direction() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _handles(c, 1, mode="traction", direction="backward", acceleration=1.0)
        await _step(c, FIXED_STEP)
        snap = await _train(c)
        assert snap["acceleration"] == pytest.approx(-1.0)
        assert snap["speed"] < 0.0


# -- Brake opposes current motion in both directions --------------------------


async def test_brake_stops_a_moving_train_without_passing_through_zero() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _handles(c, 1, mode="traction", direction="forward", acceleration=1.0)
        await _step(c, FIXED_STEP)  # v = 0.05
        moving = await _train(c)
        assert moving["speed"] == pytest.approx(0.05)

        await _handles(c, 1, mode="brake", acceleration=1.0)
        await _step(c, FIXED_STEP)  # decel 2.0 would reverse; clamps to rest
        stopped = await _train(c)
        assert stopped["speed"] == 0.0
        assert stopped["acceleration"] == 0.0
        assert stopped["position"] > moving["position"]  # travelled before rest

        await _step(c, FIXED_STEP)  # brake at standstill: no force, no reversal
        at_rest = await _train(c)
        assert at_rest["speed"] == 0.0
        assert at_rest["acceleration"] == 0.0
        assert at_rest["position"] == stopped["position"]


# -- Driving system overwrites the legacy drive-demand lever -------------------


async def test_engaged_driving_system_overwrites_the_legacy_demand() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "drive_demand": 0.5})
        await _step(c, FIXED_STEP)
        assert (await _train(c))["acceleration"] == pytest.approx(0.5)  # legacy lever

        await _handles(c, 1, mode="traction", direction="forward", acceleration=1.0)
        await _step(c, FIXED_STEP)
        snap = await _train(c)
        assert snap["acceleration"] == pytest.approx(1.0)  # overwritten by the handles
        assert snap["drive_demand"] == 0.5  # the lever value is retained, not cleared

        await _handles(c, 1, mode="off")
        await _step(c, FIXED_STEP)
        assert (await _train(c))["acceleration"] == pytest.approx(0.5)  # legacy resumes


# -- Both cabs: driver intents add (no authority) ------------------------------


async def test_opposing_cab_tractions_cancel_additively() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _handles(c, 1, mode="traction", direction="forward", acceleration=1.0)
        await _handles(c, 2, mode="traction", direction="forward", acceleration=1.0)
        await _step(c, FIXED_STEP)
        snap = await _train(c)
        assert snap["acceleration"] == pytest.approx(0.0)  # +1.0 and -1.0 net
        assert snap["speed"] == pytest.approx(0.0)


# -- Handle state feeds the stcs_atp train-out feedback bits -------------------


async def test_driving_handles_feed_stcs_atp_feedback_bits() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _handles(c, 1, mode="traction", direction="forward", acceleration=1.0)
        bits = _out_bits(await _train(c))
        assert bits["direction_handle_forward_1"] is True
        assert bits["traction_handle_traction"] is True

        await _handles(c, 2, mode="brake", direction="backward", acceleration=0.5)
        bits = _out_bits(await _train(c), cab=2)
        assert bits["direction_handle_backward"] is True
        assert bits["traction_handle_brake"] is True

        await _handles(c, 1, mode="off", direction="off")
        bits = _out_bits(await _train(c), cab=1)
        assert bits["direction_handle_forward_1"] is False


# -- Reset restores released handles ------------------------------------------


async def test_reset_clears_driving_handles_and_feedback() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _handles(c, 1, mode="traction", direction="forward", acceleration=1.0)
        assert _out_bits(await _train(c))["traction_handle_traction"] is True

        await c.post("/api/simulation/reset")
        snap = await _train(c)
        d = _equipment(snap, "driving_1")["state"]
        assert (d["mode"], d["direction"], d["acceleration"]) == ("off", "off", 0.0)
        assert _out_bits(snap)["traction_handle_traction"] is False


# -- Validation: invalid input returns 400, state unchanged --------------------


async def test_invalid_driving_inputs_are_rejected() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, _ = await _handles(c, 1, mode="fly")
        assert status == 400
        assert _equipment(await _train(c), "driving_1")["state"]["mode"] == "off"

        status, _ = await _handles(c, 1, direction="sideways")
        assert status == 400

        status, _ = await _handles(c, 1, acceleration=1.5)
        assert status == 400

        status, _ = await _handles(c, 1, acceleration=-0.1)
        assert status == 400

        status, _ = await _handles(c, 9, mode="traction")  # cab not configured
        assert status == 400
