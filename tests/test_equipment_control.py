"""Equipment state through the generic REST endpoint.

Covers immediate application (no fixed step needed), per-type validation,
cab flags as native train state, and error isolation, all observed through
the public API against the real application (§6.1).
"""

from __future__ import annotations

import base64
import time

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

        status, snap = await _equipment(c, "btm_1", data=base64.b64encode(payload).decode("ascii"))
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
        atp = next(e for e in snap["equipment"] if e["key"] == "stcs_atp_duo_1")
        assert atp["state"]["train_out_signal"][20] == "1"  # door_state_1
        assert atp["state"]["train_out_signal"][21] == "0"  # door_state_2
        out = {s["name"]: s["value"] for s in atp["state"]["train_out_states"]}
        assert out["door_state_1"] is True and out["door_state_2"] is False

        await _equipment(c, "right_door", command="open")
        await _equipment(c, "left_door", command="close")
        snap = await _train(c)
        atp = next(e for e in snap["equipment"] if e["key"] == "stcs_atp_duo_1")
        assert atp["state"]["train_out_signal"][20] == "0"
        assert atp["state"]["train_out_signal"][21] == "1"

        await c.post("/api/simulation/reset")
        snap = await _train(c)
        atp = next(e for e in snap["equipment"] if e["key"] == "stcs_atp_duo_1")
        assert atp["state"]["train_out_signal"][20:22] == "00"


# -- STCS ATP records the wall-clock time each command was applied ---------------


def _stcs_atp(snap: dict) -> dict:
    return next(e["state"] for e in snap["equipment"] if e["key"] == "stcs_atp_duo_1")


async def test_stcs_atp_command_records_arrival_wall_clock_time() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        before = time.time()
        status, snap = await _equipment(c, "stcs_atp_duo_1", command="100")
        after = time.time()
        assert status == 200
        state = _stcs_atp(snap)
        assert state["last_command"] == "100"
        assert before <= state["last_command_time"] <= after

        # The stamp holds until the next command; it does not drift with
        # simulation time, which is frozen here in MANUAL mode.
        await _step(c, 1.0)
        assert _stcs_atp(await _train(c))["last_command_time"] == pytest.approx(
            state["last_command_time"]
        )

        before = time.time()
        await _equipment(c, "stcs_atp_duo_1", command="010")
        after = time.time()
        restamped = _stcs_atp(await _train(c))["last_command_time"]
        assert before <= restamped <= after

        await c.post("/api/simulation/reset")
        assert _stcs_atp(await _train(c))["last_command_time"] is None


# -- STCS ATP train->ATP (out) signals are assertable through the API ----------


async def test_stcs_atp_train_out_signal_is_settable_through_the_api() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        # Bit 11 = turnback_button: a train-originated signal the simulator
        # never derives, so the assertion sticks.
        status, snap = await _equipment(c, "stcs_atp_duo_1", train_out_signal="0" * 11 + "1")
        assert status == 200
        state = _stcs_atp(snap)
        assert state["train_out_states"][11]["name"] == "turnback_button"
        assert state["train_out_states"][11]["value"] is True
        assert state["train_out_signal"][11] == "1"

        # Bits the simulator derives from real train state reject the
        # override: door_state_1 (bit 20) is re-asserted from the open door.
        await _equipment(c, "left_door", command="open")
        status, snap = await _equipment(c, "stcs_atp_duo_1", train_out_signal="0" * 21)
        assert status == 200
        signal = _stcs_atp(snap)["train_out_signal"]
        assert signal[11] == "0"  # the button was explicitly released
        assert signal[20] == "1"  # authoritative door state wins

        # Shorter strings keep positions beyond the last bit unchanged.
        await _equipment(c, "stcs_atp_duo_1", train_out_signal="0")
        assert _stcs_atp(snap)["train_out_signal"][20] == "1"

        # Validation mirrors the inbound command: non-binary or empty rejected,
        # and one of command / train_out_signal is required.
        for bad in ("10x", ""):
            status, _ = await _equipment(c, "stcs_atp_duo_1", train_out_signal=bad)
            assert status == 400
        empty_status, error = await _equipment(c, "stcs_atp_duo_1")
        assert empty_status == 400
        assert "command" in error["detail"]


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


async def test_key_state_is_independent_per_cab_and_resets() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        r = await c.post(
            "/api/trains/TRAIN001/commands",
            json={"cab_id": 2, "key": True},
        )
        assert r.status_code == 200
        keys = {entry["cab_id"]: entry["key"] for entry in r.json()["cabs"]}
        assert keys == {1: False, 2: True}

        r = await c.post(
            "/api/trains/TRAIN001/commands",
            json={"cab_id": 2, "key": False},
        )
        assert r.status_code == 200
        assert all(entry["key"] is False for entry in r.json()["cabs"])

        r = await c.post("/api/simulation/reset")
        assert r.status_code == 200
        reset_state = await _train(c)
        assert all(entry["key"] is False for entry in reset_state["cabs"])


@pytest.mark.parametrize("command", ["open", "close"])
async def test_equipment_changes_do_not_advance_time(command: str) -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        before = (await c.get("/api/status")).json()["simulation_time"]
        await _equipment(c, "right_door", command=command)
        after = (await c.get("/api/status")).json()["simulation_time"]
        assert after == before


# -- STCS ATP internal derivations can be blocked through the API -------------


def _atp_state(snap: dict, key: str = "stcs_atp_duo_1") -> dict:
    return next(e["state"] for e in snap["equipment"] if e["key"] == key)


def _out_bits(snap: dict, key: str = "stcs_atp_duo_1") -> dict[str, dict]:
    return {s["name"]: s for s in _atp_state(snap, key)["train_out_states"]}


async def test_block_freezes_feedback_while_the_protection_brake_still_bites() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        # Roll the train, and confirm the clear-brake feedback is derived.
        await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "drive_demand": 1.0})
        await _step(c, 1.0)
        moving = (await _train(c))["speed"]
        assert moving > 0.0
        feedback = _out_bits(await _train(c))["service_brake_7_feedback"]
        assert feedback == {
            "name": "service_brake_7_feedback",
            "value": True,
            "blockable": True,
            "blocked": False,
        }
        assert _out_bits(await _train(c))["door_state_1"]["blockable"] is False

        # Block, then assert maximum_service_brake_7 (bit 2): the feedback
        # freezes claiming a clear brake while the input bit lands.
        status, _ = await _equipment(c, "stcs_atp_duo_1", block=["service_brake_7_feedback"])
        assert status == 200
        status, snap = await _equipment(c, "stcs_atp_duo_1", command="001")
        assert status == 200
        frozen = _out_bits(snap)["service_brake_7_feedback"]
        assert frozen["value"] is True and frozen["blocked"] is True

        # The blocked feedback does not gate the train-target reaction.
        await _step(c, 1.0)
        assert (await _train(c))["speed"] == 0.0

        # Manual assertions stick on the frozen bit; unblocked derived bits
        # are still re-established against the operator's override.
        status, snap = await _equipment(c, "stcs_atp_duo_1", train_out_signal="0000")
        assert status == 200
        assert _atp_state(snap)["train_out_signal"][:4] == "1110"

        # Unblocking self-heals immediately (brake is still commanded).
        status, snap = await _equipment(c, "stcs_atp_duo_1", unblock=["service_brake_7_feedback"])
        assert status == 200
        healed = _out_bits(snap)["service_brake_7_feedback"]
        assert healed["value"] is False and healed["blocked"] is False

        await c.post("/api/simulation/reset")
        after_reset = _out_bits(await _train(c))["service_brake_7_feedback"]
        assert after_reset["value"] is True and after_reset["blocked"] is False


async def test_block_validation_is_all_or_nothing_and_scoped_to_derived_signals() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, body = await _equipment(c, "stcs_atp_duo_1", block=["nope"])
        assert status == 400
        assert "unknown stcs_atp signal" in body["detail"]

        status, body = await _equipment(c, "stcs_atp_duo_1", block=["door_state_1"])
        assert status == 400
        assert "not blockable" in body["detail"]

        # Train-in bits have no simulator-side derivation to freeze.
        status, _ = await _equipment(c, "stcs_atp_duo_1", block=["emergency_brake_1"])
        assert status == 400

        # One invalid name poisons the whole request in either list.
        status, _ = await _equipment(
            c, "stcs_atp_duo_1", block=["sleep_signal", "cab_activation"], unblock=["nope"]
        )
        assert status == 400
        assert not any(s["blocked"] for s in _out_bits(await _train(c)).values())

        # A lone block is a valid equipment request.
        status, _ = await _equipment(c, "stcs_atp_duo_1", block=["sleep_signal"])
        assert status == 200


async def test_block_freezes_handle_mirror_bits_per_instance() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, snap = await _equipment(
            c, "driving_system_1", mode="traction", direction="forward", acceleration=1.0
        )
        assert status == 200
        bits = _out_bits(snap)
        assert bits["traction_handle_traction"]["value"] is True
        assert bits["direction_handle_forward_1"]["value"] is True

        status, _ = await _equipment(c, "stcs_atp_duo_1", block=["traction_handle_traction"])
        assert status == 200

        # Releasing the handles re-derives the unblocked mirror; the frozen
        # bit keeps claiming traction, and the other cab's instance is
        # untouched.
        status, snap = await _equipment(c, "driving_system_1", mode="off", direction="off")
        assert status == 200
        bits = _out_bits(snap)
        assert bits["traction_handle_traction"]["value"] is True
        assert bits["traction_handle_traction"]["blocked"] is True
        assert bits["direction_handle_forward_1"]["value"] is False
        other = _out_bits(snap, "stcs_atp_duo_2")
        assert other["traction_handle_traction"]["value"] is False

        status, snap = await _equipment(c, "stcs_atp_duo_1", unblock=["traction_handle_traction"])
        assert status == 200
        healed = _out_bits(snap)["traction_handle_traction"]
        assert healed["value"] is False and healed["blocked"] is False


async def test_blocking_settles_the_derived_value_over_stale_assertions() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        # An assertion onto a still-unblocked derived bit is shadowed by the
        # rule and must not resurface when the bit later freezes: blocking
        # settles the live derived value, not the stale write.
        status, snap = await _equipment(c, "stcs_atp_duo_1", train_out_signal="0000")
        assert status == 200
        assert _atp_state(snap)["train_out_signal"][:4] == "1111"  # shadowed

        status, snap = await _equipment(c, "stcs_atp_duo_1", block=["service_brake_7_feedback"])
        assert status == 200
        assert _out_bits(snap)["service_brake_7_feedback"]["value"] is True

        # Assertions taken while blocked are the settled value's replacement.
        status, snap = await _equipment(
            c, "stcs_atp_duo_1", train_out_signal="000" + "1" + "0" * 26
        )
        assert _atp_state(snap)["train_out_signal"][3] == "1"

        # Re-blocking an already-blocked row does not disturb its manual value.
        await _equipment(c, "stcs_atp_duo_1", command="001")
        status, snap = await _equipment(c, "stcs_atp_duo_1", block=["service_brake_7_feedback"])
        assert _out_bits(snap)["service_brake_7_feedback"]["value"] is True


async def test_block_freezes_sleep_derivation_from_cab_activation() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        # Cab 1 starts active: sleep is derived false.
        assert _out_bits(await _train(c))["sleep_signal"]["value"] is False

        status, _ = await _equipment(c, "stcs_atp_duo_1", block=["sleep_signal"])
        assert status == 200
        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 1, "active": False})
        assert r.status_code == 200

        bits = _out_bits(await _train(c))
        assert bits["cab_activation"] == {
            "name": "cab_activation",
            "value": False,
            "blockable": False,
            "blocked": False,
        }
        assert bits["sleep_signal"]["value"] is False
        assert bits["sleep_signal"]["blocked"] is True

        status, snap = await _equipment(c, "stcs_atp_duo_1", unblock=["sleep_signal"])
        assert status == 200
        assert _out_bits(snap)["sleep_signal"]["value"] is True
