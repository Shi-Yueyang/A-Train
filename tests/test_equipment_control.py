"""Equipment state through the generic REST endpoint.

Covers immediate application (no fixed step needed), per-type validation,
cab flags as plain equipment state, and error isolation, all observed through
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

        status, snap = await _equipment(c, "door", command="open")
        assert status == 200
        assert snap["equipment"]["door"]["state"] == "open"  # no step required

        await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "drive_demand": 1.0})
        await _step(c, 0.50)
        moving = await _train(c)
        assert moving["position"] > 0.0  # door open: dynamics unaffected


# -- Invalid input: 400 and state unchanged ------------------------------------


async def test_invalid_equipment_commands_are_rejected_without_state_change() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, body = await _equipment(c, "door", command="explode")
        assert status == 400
        assert (await _train(c))["equipment"]["door"]["state"] == "closed"

        status, _ = await _equipment(c, "unknown_device", command="open")
        assert status == 400
        assert "unknown_device" not in (await _train(c))["equipment"]

        r = await c.post("/api/trains/NOPE/equipment/door", json={"command": "open"})
        assert r.status_code == 400


# -- BTM: opaque delivery through the equipment endpoint ------------------------


async def test_btm_delivery_through_equipment_endpoint() -> None:
    payload = bytes([0x01, 0x23, 0xA4, 0xFF, 0x00, 0x81, 0x72])
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, snap = await _equipment(
            c, "btm", cab_id=1, data=base64.b64encode(payload).decode("ascii")
        )
        assert status == 200
        cab1 = next(b for b in snap["equipment"]["btm"] if b["cab_id"] == 1)
        assert cab1["pending"] is True
        assert cab1["received_count"] == 1
        assert base64.b64decode(cab1["payload_b64"]) == payload

        status, _ = await _equipment(c, "btm", cab_id=9, data="AA==")
        assert status == 400  # cab not configured

        r = await c.post("/api/trains/TRAIN001/equipment/btm", json={"cab_id": 1, "data": "!!!"})
        assert r.status_code == 400  # invalid base64


# -- Digital I/O ---------------------------------------------------------------


async def test_io_bits_and_named_values_update() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, snap = await _equipment(c, "io", direction="atp_to_train", bits="10")
        assert status == 200
        assert snap["equipment"]["io"]["atp_to_train"] == "10"

        status, snap = await _equipment(c, "io", direction="atp_to_train", values={"warning": True})
        assert status == 200
        assert snap["equipment"]["io"]["atp_to_train"] == "10"

        status, _ = await _equipment(c, "io", direction="sideways", bits="1")
        assert status == 400
        status, _ = await _equipment(c, "io", direction="atp_to_train")
        assert status == 400  # neither bits nor values


# -- Cab activation is a plain equipment flag, no authority --------------------


async def test_cab_activate_is_local_flag_with_no_control_effect() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        # Cab 1 drives.
        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "drive_demand": 0.5})
        assert r.status_code == 200

        status, snap = await _equipment(c, "cab", cab_id=2, command="activate")
        assert status == 200
        flags = {entry["cab_id"]: entry["active"] for entry in snap["equipment"]["cab"]}
        assert flags == {1: True, 2: True}  # flags independent, no transfer

        # The former cab is still accepted; cabs carry no authority.
        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "drive_demand": 0.0})
        assert r.status_code == 200

        # Both cabs can be deactivated in any order.
        for cab_id in (1, 2):
            status, _ = await _equipment(c, "cab", cab_id=cab_id, command="deactivate")
            assert status == 200
        flags = {
            entry["cab_id"]: entry["active"]
            for entry in (await _train(c))["equipment"]["cab"]
        }
        assert flags == {1: False, 2: False}

        # An unconfigured cab is rejected.
        status, _ = await _equipment(c, "cab", cab_id=9, command="activate")
        assert status == 400

        # Reset restores the configured activation flags.
        await c.post("/api/simulation/reset")
        reset_flags = {
            entry["cab_id"]: entry["active"] for entry in (await _train(c))["equipment"]["cab"]
        }
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
        await _equipment(c, "door", command=command)
        after = (await c.get("/api/status")).json()["simulation_time"]
        assert after == before
