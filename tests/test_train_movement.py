"""Phase 2 acceptance tests — train world and public state (TODO.md §Phase 2).

Covers the train aggregate API, physics priority and stop-within-step clamping,
reset, equipment snapshots, REST train-control validation, and snapshot
immutability. All driven through the public REST API against the real
application; no core/domain object is touched directly (§6.1).
"""

from __future__ import annotations

import pytest

from a_train.domain.train import TrainConfig
from tests.support.app import running_app

FIXED_STEP = 0.05

T1 = TrainConfig(
    train_id="TRAIN001",
    cab_ids=(1, 2),
    initial_active_cab=1,
    max_traction_accel=1.0,
    max_service_brake_decel=1.0,
    max_emergency_brake_decel=2.0,
    initial_position=0.0,
)
T2 = TrainConfig(
    train_id="TRAIN002",
    cab_ids=(1,),
    initial_active_cab=1,
    max_traction_accel=2.0,
    max_service_brake_decel=1.5,
    max_emergency_brake_decel=3.0,
    initial_position=100.0,
)


async def _manual_start(c) -> None:
    await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
    await c.post("/api/simulation/start")


async def _train(c, tid: str) -> dict:
    return (await c.get(f"/api/trains/{tid}")).json()


async def _control(c, tid: str, **body) -> dict:
    body.setdefault("cab_id", 1)
    return (await c.post(f"/api/trains/{tid}/commands", json=body)).json()


async def _step(c, delta: float) -> dict:
    return (await c.post("/api/simulation/step", json={"delta": delta})).json()


# -- Criterion: repeatable snapshots for the same state + command sequence -----


def _signature(snap: dict) -> dict:
    return {
        t["train_id"]: (
            round(t["speed"], 12),
            round(t["position"], 12),
            round(t["acceleration"], 12),
            t["traction_demand"],
            t["service_brake_demand"],
            t["emergency_brake"],
        )
        for t in snap["trains"]
    }


async def test_multiple_trains_produce_repeatable_snapshots() -> None:
    async def run_once() -> dict:
        async with running_app([T1, T2]) as c:
            await _manual_start(c)
            await _control(c, "TRAIN001", cab_id=1, traction_demand=1.0)
            await _control(c, "TRAIN002", cab_id=1, traction_demand=0.5)
            await _step(c, 0.50)
            return (await c.get("/api/trains")).json()

    first = _signature(await run_once())
    second = _signature(await run_once())
    assert first == second
    assert first["TRAIN001"][0] == pytest.approx(0.5, abs=1e-9)  # speed
    assert first["TRAIN002"][1] == pytest.approx(100.125, abs=1e-9)  # position


async def test_reset_and_repeat_gives_the_same_snapshot() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _control(c, "TRAIN001", cab_id=1, traction_demand=1.0)
        await _step(c, 0.25)
        before = _signature((await c.get("/api/trains")).json())

        await c.post("/api/simulation/reset")
        await _manual_start(c)
        await _control(c, "TRAIN001", cab_id=1, traction_demand=1.0)
        await _step(c, 0.25)
        after = _signature((await c.get("/api/trains")).json())

    assert before == after


# -- Criterion: valid control changes state at the boundary; invalid is rejected


async def test_valid_control_changes_state_and_invalid_is_rejected() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        initial = await _train(c, "TRAIN001")
        assert initial["traction_demand"] == 0.0
        assert initial["speed"] == 0.0

        # Valid request: control state changes through the core, physics not yet.
        after = await _control(c, "TRAIN001", cab_id=1, traction_demand=1.0)
        assert after["traction_demand"] == 1.0
        assert after["speed"] == 0.0  # physics advances only at a fixed step
        assert after["position"] == 0.0

        # Invalid demand -> 400, state unchanged.
        r = await c.post(
            "/api/trains/TRAIN001/commands",
            json={"cab_id": 1, "traction_demand": 1.5},
        )
        assert r.status_code == 400
        assert (await _train(c, "TRAIN001"))["traction_demand"] == 1.0

        # Invalid cab (not active) -> 400, state unchanged.
        r = await c.post(
            "/api/trains/TRAIN001/commands",
            json={"cab_id": 2, "traction_demand": 0.4},
        )
        assert r.status_code == 400
        assert (await _train(c, "TRAIN001"))["traction_demand"] == 1.0

        # Unknown train -> 400, state unchanged.
        r = await c.post(
            "/api/trains/NOPE/commands",
            json={"cab_id": 1, "traction_demand": 0.4},
        )
        assert r.status_code == 400

        # Door open closes off traction even with a demand set.
        await _control(c, "TRAIN001", cab_id=1, traction_demand=1.0, door="open")
        await _step(c, FIXED_STEP)
        moving_open = await _train(c, "TRAIN001")
        assert moving_open["speed"] == 0.0  # doors open -> no traction


# -- Criterion: braking priority (emergency > service > traction) --------------


async def test_braking_priority_at_each_fixed_step() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        # Get moving first so braking does not stop within a single step.
        await _control(c, "TRAIN001", cab_id=1, traction_demand=1.0)
        await _step(c, 1.0)  # speed == 1.0, accel == 1.0

        # Service brake (1.0) wins over traction (1.0).
        await _control(c, "TRAIN001", cab_id=1, traction_demand=1.0, service_brake_demand=1.0)
        await _step(c, FIXED_STEP)
        snap = await _train(c, "TRAIN001")
        assert snap["acceleration"] == pytest.approx(-1.0, abs=1e-9)
        assert snap["speed"] < 1.0  # decelerating

        # Emergency brake wins over service + traction.
        await _control(
            c,
            "TRAIN001",
            cab_id=1,
            traction_demand=1.0,
            service_brake_demand=1.0,
            emergency_brake=True,
        )
        await _step(c, FIXED_STEP)
        snap = await _train(c, "TRAIN001")
        assert snap["acceleration"] == pytest.approx(-2.0, abs=1e-9)

        # Releasing emergency lets service take priority again.
        await _control(
            c,
            "TRAIN001",
            cab_id=1,
            traction_demand=1.0,
            service_brake_demand=1.0,
            emergency_brake=False,
        )
        await _step(c, FIXED_STEP)
        snap = await _train(c, "TRAIN001")
        assert snap["acceleration"] == pytest.approx(-1.0, abs=1e-9)


# -- Criterion: stop-within-step clamps speed/accel to zero, position not decreased


async def test_braking_that_stops_within_a_step() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        # Reach a small speed of 0.05 m/s (one fixed step of traction 1.0).
        await _control(c, "TRAIN001", cab_id=1, traction_demand=1.0)
        await _step(c, FIXED_STEP)
        moving = await _train(c, "TRAIN001")
        assert moving["speed"] == pytest.approx(0.05, abs=1e-12)
        position_before = moving["position"]

        # Emergency brake (-2.0) would reverse within this step: stop at rest.
        await _control(c, "TRAIN001", cab_id=1, emergency_brake=True)
        await _step(c, FIXED_STEP)
        stopped = await _train(c, "TRAIN001")

        assert stopped["speed"] == 0.0
        assert stopped["acceleration"] == 0.0
        assert stopped["position"] >= position_before  # never decreases
        # Distance travelled before stopping: v0/2 * t_stop = 0.05/2 * 0.025.
        assert stopped["position"] == pytest.approx(position_before + 0.000625, abs=1e-12)

        # Further braking at rest keeps it at rest with zero acceleration.
        await _step(c, FIXED_STEP)
        still = await _train(c, "TRAIN001")
        assert still["speed"] == 0.0
        assert still["acceleration"] == 0.0
        assert still["position"] == stopped["position"]


# -- Criterion: reset restores physical state and clears control/equipment ------


async def test_train_reset_restores_state_and_clears_equipment() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _control(c, "TRAIN001", cab_id=1, traction_demand=1.0)  # door closed
        await _step(c, 0.50)  # moves
        await _control(c, "TRAIN001", cab_id=1, door="open")  # open door (traction blocked)
        await _step(c, FIXED_STEP)  # coasts with the door open
        moved = await _train(c, "TRAIN001")
        assert moved["position"] > 0.0
        assert moved["speed"] > 0.0
        assert moved["door_state"] == "open"
        assert moved["traction_demand"] == 1.0
        # Equipment fed state during the step.
        assert moved["io"]["train_to_atp"][1] == "0"  # doors_closed == 0

        await c.post("/api/simulation/reset")
        reset_state = await _train(c, "TRAIN001")
        assert reset_state["position"] == 0.0  # configured initial
        assert reset_state["speed"] == 0.0
        assert reset_state["acceleration"] == 0.0
        assert reset_state["traction_demand"] == 0.0
        assert reset_state["service_brake_demand"] == 0.0
        assert reset_state["emergency_brake"] is False
        assert reset_state["door_state"] == "closed"
        assert reset_state["active_cab"] == 1
        # Equipment runtime state cleared back to defaults.
        assert reset_state["io"]["train_to_atp"] == "0000"
        assert all(b["pending"] is False and b["received_count"] == 0 for b in reset_state["btm"])


# -- Criterion: equipment adds optional nested snapshots, physical fields intact


async def test_equipment_nested_snapshots_do_not_change_physical_fields() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _control(c, "TRAIN001", cab_id=1, traction_demand=1.0)
        await _step(c, FIXED_STEP)
        snap = await _train(c, "TRAIN001")

        # Physical fields keep their names and meanings (Phase 1 contract).
        for field in ("speed", "acceleration", "position", "direction"):
            assert field in snap
        assert snap["direction"] == "forward"

        # Equipment is exposed as optional nested snapshots.
        assert isinstance(snap["cab"], list) and len(snap["cab"]) == 2
        assert snap["cab"][0]["cab_id"] == 1 and snap["cab"][0]["active"] is True
        assert snap["cab"][1]["active"] is False
        assert snap["doors"]["state"] == "closed"
        assert isinstance(snap["btm"], list) and snap["btm"]
        # IO equipment reflects aggregate-fed state after a step.
        assert snap["io"]["train_to_atp"][0] == "1"  # cab_active
        assert snap["io"]["train_to_atp"][1] == "1"  # doors_closed

        # Opening the door changes only the door equipment snapshot, not physical
        # field names; traction is blocked while a door is open.
        await _control(c, "TRAIN001", cab_id=1, door="open")
        await _step(c, FIXED_STEP)
        snap2 = await _train(c, "TRAIN001")
        assert snap2["doors"]["state"] == "open"
        assert snap2["io"]["train_to_atp"][1] == "0"  # doors_closed == 0


# -- Criterion: snapshots cannot be used to mutate subsequent simulator state --


async def test_snapshot_cannot_be_used_to_mutate_simulator_state() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _control(c, "TRAIN001", cab_id=1, traction_demand=1.0)
        await _step(c, FIXED_STEP)
        snap = await _train(c, "TRAIN001")
        speed_after_one_step = snap["speed"]

        # A client tries to write back physical fields via the command body.
        # Extra fields are ignored; only control state is accepted.
        r = await c.post(
            "/api/trains/TRAIN001/commands",
            json={
                "cab_id": 1,
                "speed": 999.0,
                "position": 999.0,
                "acceleration": 999.0,
            },
        )
        assert r.status_code == 200

        # Advancing again moves according to the held traction command, not any
        # client-supplied speed/position.
        await _step(c, FIXED_STEP)
        snap2 = await _train(c, "TRAIN001")
        assert snap2["speed"] == pytest.approx(speed_after_one_step + 0.05, abs=1e-9)
        assert snap2["position"] < 1.0
