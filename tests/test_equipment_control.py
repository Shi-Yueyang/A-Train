"""Equipment state through the generic REST endpoint.

Covers immediate application (no fixed step needed), per-type validation,
cab flags as native train state, and error isolation, all observed through
the public API against the real application (§6.1).
"""

from __future__ import annotations

import base64

import pytest

from a_train.domain.train import TrainConfig
from tests.support.app import running_app

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


async def _train(c, tid: str = "TRAIN001") -> dict:
    return (await c.get(f"/api/trains/{tid}")).json()


async def _equipment(c, key: str, **body) -> tuple[int, dict]:
    r = await c.post(f"/api/trains/TRAIN001/equipment/{key}", json=body)
    return r.status_code, r.json()


async def _step(c, delta: float) -> None:
    await c.post("/api/simulation/step", json={"delta": delta})


# -- Doors: immediate state change, no effect on dynamics ----------------------


async def test_door_state_applies_immediately_without_affecting_drive() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, snap = await _equipment(c, "left_door", command="open")
        assert status == 200
        door = next(e for e in snap["equipment"] if e["key"] == "left_door")
        assert door["state"]["state"] == "open"  # no step required

        await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "drive_demand": 1.0})
        await _step(c, 0.50)
        moving = await _train(c)
        assert moving["position"] > 0.0  # door open: dynamics unaffected


# -- Invalid input: 400 and state unchanged ------------------------------------


async def test_invalid_equipment_commands_are_rejected_without_state_change() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, body = await _equipment(c, "left_door", command="explode")
        assert status == 400
        door = next(e for e in (await _train(c))["equipment"] if e["key"] == "left_door")
        assert door["state"]["state"] == "closed"

        status, _ = await _equipment(c, "unknown_device", command="open")
        assert status == 400
        assert all(e["key"] != "unknown_device" for e in (await _train(c))["equipment"])

        r = await c.post("/api/trains/NOPE/equipment/left_door", json={"command": "open"})
        assert r.status_code == 400


# -- BTM: opaque delivery through the equipment endpoint ------------------------


async def test_btm_delivery_through_equipment_endpoint() -> None:
    payload = bytes([0x01, 0x23, 0xA4, 0xFF, 0x00, 0x81, 0x72])
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, snap = await _equipment(
            c, "btm_1", data=base64.b64encode(payload).decode("ascii")
        )
        assert status == 200
        cab1 = next(e["state"] for e in snap["equipment"] if e["key"] == "btm_1")
        assert cab1["pending"] is True
        assert cab1["received_count"] == 1
        assert base64.b64decode(cab1["payload_b64"]) == payload

        status, _ = await _equipment(c, "btm_9", data="AA==")
        assert status == 400  # cab not configured

        r = await c.post("/api/trains/TRAIN001/equipment/btm_1", json={"data": "!!!"})
        assert r.status_code == 400  # invalid base64


# -- Door state feeds the stcs_atp train-out feedback through the public API ---


async def test_door_state_reflects_in_stcs_atp_train_out_signal() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, snap = await _equipment(c, "left_door", command="open")
        assert status == 200
        atp = next(e for e in snap["equipment"] if e["key"] == "stcs_atp")
        assert atp["state"]["train_out_signal"][20] == "1"  # door_state_1
        assert atp["state"]["train_out_signal"][21] == "0"  # door_state_2
        out = {s["name"]: s["value"] for s in atp["state"]["train_out_states"]}
        assert out["door_state_1"] is True and out["door_state_2"] is False

        await _equipment(c, "right_door", command="open")
        await _equipment(c, "left_door", command="close")
        snap = await _train(c)
        atp = next(e for e in snap["equipment"] if e["key"] == "stcs_atp")
        assert atp["state"]["train_out_signal"][20] == "0"
        assert atp["state"]["train_out_signal"][21] == "1"

        await c.post("/api/simulation/reset")
        snap = await _train(c)
        atp = next(e for e in snap["equipment"] if e["key"] == "stcs_atp")
        assert atp["state"]["train_out_signal"][20:22] == "00"


# -- Cab activation is native train state, no authority ------------------------


async def test_cab_activate_is_native_flag_with_no_control_effect() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        # Cab 1 drives.
        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "drive_demand": 0.5})
        assert r.status_code == 200

        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 2, "active": True})
        assert r.status_code == 200
        flags = {entry["cab_id"]: entry["active"] for entry in r.json()["cabs"]}
        assert flags == {1: True, 2: True}  # flags independent, no transfer

        # The former cab is still accepted; cabs carry no authority.
        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "drive_demand": 0.0})
        assert r.status_code == 200

        # Both cabs can be deactivated in any order.
        for cab_id in (1, 2):
            r = await c.post(
                "/api/trains/TRAIN001/commands", json={"cab_id": cab_id, "active": False}
            )
            assert r.status_code == 200
        flags = {entry["cab_id"]: entry["active"] for entry in (await _train(c))["cabs"]}
        assert flags == {1: False, 2: False}

        # An unconfigured cab is rejected and its activation flag is unchanged.
        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 9, "active": True})
        assert r.status_code == 400
        flags = {entry["cab_id"]: entry["active"] for entry in (await _train(c))["cabs"]}
        assert flags == {1: False, 2: False}

        # Cabs are no longer equipment entries.
        snap = await _train(c)
        assert all(e["type"] != "cab" for e in snap["equipment"])
        status, _ = await _equipment(c, "cab_1", command="activate")
        assert status == 400

        # Reset restores the configured activation flags.
        await c.post("/api/simulation/reset")
        reset_flags = {entry["cab_id"]: entry["active"] for entry in (await _train(c))["cabs"]}
        assert reset_flags == {1: True, 2: False}


async def test_drive_accepted_from_any_configured_cab() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 2, "drive_demand": 1.0})
        assert r.status_code == 200
        await _step(c, 0.50)
        assert (await _train(c))["speed"] > 0.0

        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "drive_demand": 0.0})
        assert r.status_code == 200

        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 9, "drive_demand": 0.0})
        assert r.status_code == 400  # cab not configured


@pytest.mark.parametrize("command", ["open", "close"])
async def test_equipment_changes_do_not_advance_time(command: str) -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        before = (await c.get("/api/status")).json()["simulation_time"]
        await _equipment(c, "right_door", command=command)
        after = (await c.get("/api/status")).json()["simulation_time"]
        assert after == before
