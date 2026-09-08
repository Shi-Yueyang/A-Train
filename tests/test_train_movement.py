"""Phase 2 acceptance tests — train world and public state (TODO.md §Phase 2).

Covers the train aggregate API, signed drive demand and stop-within-step clamping,
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
    max_decel=2.0,
    initial_position=0.0,
)
T2 = TrainConfig(
    train_id="TRAIN002",
    cab_ids=(1,),
    initial_active_cab=1,
    max_traction_accel=2.0,
    max_decel=3.0,
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
            t["drive_demand"],
        )
        for t in snap["trains"]
    }


async def test_multiple_trains_produce_repeatable_snapshots() -> None:
    async def run_once() -> dict:
        async with running_app([T1, T2]) as c:
            await _manual_start(c)
            await _control(c, "TRAIN001", cab_id=1, drive_demand=1.0)
            await _control(c, "TRAIN002", cab_id=1, drive_demand=0.5)
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
        await _control(c, "TRAIN001", cab_id=1, drive_demand=1.0)
        await _step(c, 0.25)
        before = _signature((await c.get("/api/trains")).json())

        await c.post("/api/simulation/reset")
        await _manual_start(c)
        await _control(c, "TRAIN001", cab_id=1, drive_demand=1.0)
        await _step(c, 0.25)
        after = _signature((await c.get("/api/trains")).json())

    assert before == after


# -- Criterion: valid control changes state at the boundary; invalid is rejected


async def test_valid_control_changes_state_and_invalid_is_rejected() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        initial = await _train(c, "TRAIN001")
        assert initial["drive_demand"] == 0.0
        assert initial["speed"] == 0.0

        # Valid request: control state changes through the core, physics not yet.
        after = await _control(c, "TRAIN001", cab_id=1, drive_demand=1.0)
        assert after["drive_demand"] == 1.0
        assert after["speed"] == 0.0  # physics advances only at a fixed step
        assert after["position"] == 0.0

        # Invalid demand -> 400, state unchanged.
        r = await c.post(
            "/api/trains/TRAIN001/commands",
            json={"cab_id": 1, "drive_demand": 1.5},
        )
        assert r.status_code == 400
        assert (await _train(c, "TRAIN001"))["drive_demand"] == 1.0

        # Invalid cab (not configured) -> 400, state unchanged.
        r = await c.post(
            "/api/trains/TRAIN001/commands",
            json={"cab_id": 9, "drive_demand": 0.4},
        )
        assert r.status_code == 400
        assert (await _train(c, "TRAIN001"))["drive_demand"] == 1.0

        # Unknown train -> 400, state unchanged.
        r = await c.post(
            "/api/trains/NOPE/commands",
            json={"cab_id": 1, "drive_demand": 0.4},
        )
        assert r.status_code == 400


# -- Criterion: signed demand resolves acceleration at each fixed step ---------


async def test_drive_demand_sign_applied_at_each_fixed_step() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        # Get moving first so deceleration does not stop within a single step.
        await _control(c, "TRAIN001", cab_id=1, drive_demand=1.0)
        await _step(c, 1.0)  # speed == 1.0, accel == 1.0

        # Half negative demand scales max_decel (2.0 * -0.5 = -1.0).
        await _control(c, "TRAIN001", cab_id=1, drive_demand=-0.5)
        await _step(c, FIXED_STEP)
        snap = await _train(c, "TRAIN001")
        assert snap["acceleration"] == pytest.approx(-1.0, abs=1e-9)
        assert snap["speed"] < 1.0  # decelerating

        # Full negative demand scales max_decel.
        await _control(c, "TRAIN001", cab_id=1, drive_demand=-1.0)
        await _step(c, FIXED_STEP)
        snap = await _train(c, "TRAIN001")
        assert snap["acceleration"] == pytest.approx(-2.0, abs=1e-9)

        # Zero demand coasts with zero acceleration.
        await _control(c, "TRAIN001", cab_id=1, drive_demand=0.0)
        await _step(c, FIXED_STEP)
        snap = await _train(c, "TRAIN001")
        assert snap["acceleration"] == pytest.approx(0.0, abs=1e-9)


# -- Criterion: stop-within-step clamps speed/accel to zero, position not decreased


async def test_deceleration_that_stops_within_a_step() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        # Reach a small speed of 0.05 m/s (one fixed step of demand 1.0).
        await _control(c, "TRAIN001", cab_id=1, drive_demand=1.0)
        await _step(c, FIXED_STEP)
        moving = await _train(c, "TRAIN001")
        assert moving["speed"] == pytest.approx(0.05, abs=1e-12)
        position_before = moving["position"]

        # Demand -1.0 (decel 2.0) would reverse within this step: stop at rest.
        await _control(c, "TRAIN001", cab_id=1, drive_demand=-1.0)
        await _step(c, FIXED_STEP)
        stopped = await _train(c, "TRAIN001")

        assert stopped["speed"] == 0.0
        assert stopped["acceleration"] == 0.0
        assert stopped["position"] >= position_before  # never decreases
        # Distance travelled before stopping: v0/2 * t_stop = 0.05/2 * 0.025.
        assert stopped["position"] == pytest.approx(position_before + 0.000625, abs=1e-12)

        # Further decelerating demand at rest keeps it at rest with zero acceleration.
        await _step(c, FIXED_STEP)
        still = await _train(c, "TRAIN001")
        assert still["speed"] == 0.0
        assert still["acceleration"] == 0.0
        assert still["position"] == stopped["position"]


# -- Criterion: reset restores physical state and clears control/equipment ------


async def test_train_reset_restores_state_and_clears_equipment() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _control(c, "TRAIN001", cab_id=1, drive_demand=1.0)
        await _step(c, 0.50)  # moves
        moved = await _train(c, "TRAIN001")
        assert moved["position"] > 0.0
        assert moved["speed"] > 0.0
        assert moved["drive_demand"] == 1.0

        await c.post("/api/simulation/reset")
        reset_state = await _train(c, "TRAIN001")
        assert reset_state["position"] == 0.0  # configured initial
        assert reset_state["speed"] == 0.0
        assert reset_state["acceleration"] == 0.0
        assert reset_state["drive_demand"] == 0.0
        doors = [e for e in reset_state["equipment"] if e["type"] == "door"]
        assert {e["key"] for e in doors} == {"left_door", "right_door"}
        assert all(e["state"]["state"] == "closed" for e in doors)
        cab_flags = {c["cab_id"]: c["active"] for c in reset_state["cabs"]}
        assert cab_flags == {1: True, 2: False}  # configured cabs restored
        btm = [e["state"] for e in reset_state["equipment"] if e["type"] == "btm"]
        assert all(b["pending"] is False and b["received_count"] == 0 for b in btm)


# -- Criterion: equipment adds optional nested snapshots, physical fields intact


async def test_equipment_nested_snapshots_do_not_change_physical_fields() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _control(c, "TRAIN001", cab_id=1, drive_demand=1.0)
        await _step(c, FIXED_STEP)
        snap = await _train(c, "TRAIN001")

        # Physical fields keep their names and meanings (Phase 1 contract).
        for field in ("speed", "acceleration", "position", "direction"):
            assert field in snap
        assert snap["direction"] == "forward"

        # Cabs are native train state, one entry per configured cab.
        cabs = snap["cabs"]
        assert len(cabs) == 2
        assert cabs[0]["cab_id"] == 1 and cabs[0]["active"] is True
        assert cabs[1]["active"] is False
        assert all(e["type"] != "cab" for e in snap["equipment"])
        doors = [e for e in snap["equipment"] if e["type"] == "door"]
        assert {e["key"] for e in doors} == {"left_door", "right_door"}
        assert all(e["state"]["state"] == "closed" for e in doors)
        assert any(e["type"] == "btm" for e in snap["equipment"])


# -- Criterion: snapshots cannot be used to mutate subsequent simulator state --


async def test_snapshot_cannot_be_used_to_mutate_simulator_state() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _control(c, "TRAIN001", cab_id=1, drive_demand=1.0)
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
