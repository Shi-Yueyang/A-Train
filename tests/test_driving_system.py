"""Driving-system equipment through the public API (§3.5, web-api.md, atp-api.md §3.1).

The driver-room equipment owns three handles (mode, direction, acceleration),
maps them through the cab's track facing into a train-target intent, and
overwrites the legacy drive-demand lever while engaged. All behavior is
observed through REST/WebSocket/ATP against the real application; no core or
domain object is touched directly (§6.1).
"""

from __future__ import annotations

from dataclasses import replace

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
# Same consist rolling rearward/forward at 1.0 m/s at reset.
T_ROLL_BACKWARD = replace(T1, initial_speed=-1.0)
T_ROLL_FORWARD = replace(T1, initial_speed=1.0)


async def _manual_start(c) -> None:
    await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
    await c.post("/api/simulation/start")


async def _train(c) -> dict:
    return (await c.get("/api/trains/TRAIN001")).json()


async def _handles(c, cab: int, **body) -> tuple[int, dict]:
    r = await c.post(f"/api/trains/TRAIN001/equipment/driving_system_{cab}", json=body)
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
            d = _equipment(snap, f"driving_system_{cab}")["state"]
            assert d["mode"] == "off"
            assert d["direction"] == "off"
            assert d["acceleration"] == 0.0
        assert _equipment(snap, "driving_system_1")["state"]["facing"] == "forward"
        assert _equipment(snap, "driving_system_2")["state"]["facing"] == "backward"


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
        assert bits["direction_handle_forward_2"] is True  # duplicate of the same signal
        assert bits["traction_handle_traction"] is True

        await _handles(c, 2, mode="brake", direction="backward", acceleration=0.5)
        bits = _out_bits(await _train(c), cab=2)
        assert bits["direction_handle_backward"] is True
        assert bits["traction_handle_brake"] is True
        assert bits["direction_handle_forward_1"] is False
        assert bits["direction_handle_forward_2"] is False

        await _handles(c, 1, mode="off", direction="off")
        bits = _out_bits(await _train(c), cab=1)
        assert bits["direction_handle_forward_1"] is False
        assert bits["direction_handle_forward_2"] is False


# -- Reset restores released handles ------------------------------------------


async def test_reset_clears_driving_handles_and_feedback() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _handles(c, 1, mode="traction", direction="forward", acceleration=1.0)
        assert _out_bits(await _train(c))["traction_handle_traction"] is True

        await c.post("/api/simulation/reset")
        snap = await _train(c)
        d = _equipment(snap, "driving_system_1")["state"]
        assert (d["mode"], d["direction"], d["acceleration"]) == ("off", "off", 0.0)
        assert _out_bits(snap)["traction_handle_traction"] is False


# -- Validation: invalid input returns 400, state unchanged --------------------


async def test_invalid_driving_inputs_are_rejected() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, _ = await _handles(c, 1, mode="fly")
        assert status == 400
        assert _equipment(await _train(c), "driving_system_1")["state"]["mode"] == "off"

        status, _ = await _handles(c, 1, direction="sideways")
        assert status == 400

        status, _ = await _handles(c, 1, acceleration=1.5)
        assert status == 400

        status, _ = await _handles(c, 1, acceleration=-0.1)
        assert status == 400

        status, _ = await _handles(c, 9, mode="traction")  # cab not configured
        assert status == 400


async def test_stopping_envelope_cruises_at_cap_and_reports_effective_handles() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        status, snap = await _handles(
            c, 1, control_mode="stopping_envelope", target_position=5.0, max_speed=1.0
        )
        assert status == 200
        state = _equipment(snap, "driving_system_1")["state"]
        assert state["control_mode"] == "stopping_envelope"
        assert state["law"] == "stopping_envelope"
        assert state["target_position"] == 5.0
        assert state["speed_gain"] == 4.0

        await _step(c, 2.0)
        train = await _train(c)
        state = _equipment(train, "driving_system_1")["state"]
        assert train["speed"] == pytest.approx(1.0, abs=0.05)
        assert state["acceleration"] < 0.05
        assert state["direction"] == "forward"
        assert _out_bits(train)["direction_handle_forward_1"] is True
        assert _out_bits(train)["direction_handle_forward_2"] is True
        assert state["manual_mode"] == "off"


async def test_stopping_envelope_speed_gain_can_be_set_and_updated() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        status, snap = await _handles(
            c,
            1,
            control_mode="stopping_envelope",
            target_position=100.0,
            max_speed=0.5,
            speed_gain=1.0,
        )
        assert status == 200
        state = _equipment(snap, "driving_system_1")["state"]
        assert state["speed_gain"] == 1.0

        await _step(c, FIXED_STEP)
        state = _equipment(await _train(c), "driving_system_1")["state"]
        assert state["acceleration"] == pytest.approx(0.5)

        status, _ = await _handles(c, 1, speed_gain=4.0)
        assert status == 200
        await _step(c, FIXED_STEP)
        state = _equipment(await _train(c), "driving_system_1")["state"]
        assert state["speed_gain"] == 4.0
        assert state["acceleration"] == 1.0


async def test_hold_speed_law_tracks_signed_setpoint() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        status, snap = await _handles(c, 1, control_mode="hold_speed", target_speed=1.0)
        assert status == 200
        state = _equipment(snap, "driving_system_1")["state"]
        assert state["law"] == "hold_speed"
        assert state["target_speed"] == 1.0

        await _step(c, 2.0)
        train = await _train(c)
        assert train["speed"] == pytest.approx(1.0, abs=0.05)
        state = _equipment(train, "driving_system_1")["state"]
        assert state["direction"] == "forward"

        # A rearward setpoint is an explicit operator reversal: the hold law
        # brakes through zero and tractores cab-relative backward to hold it.
        status, _ = await _handles(c, 1, target_speed=-0.5)
        assert status == 200
        for _ in range(8):
            await _step(c, 0.25)
        train = await _train(c)
        assert train["speed"] == pytest.approx(-0.5, abs=0.05)
        state = _equipment(train, "driving_system_1")["state"]
        assert state["target_speed"] == -0.5
        # At setpoint the tracking request is under the deadband: handles rest
        # off while the direction handle stays on the rearward travel side.
        assert state["mode"] == "off"
        assert state["direction"] == "backward"
        assert state["acceleration"] == 0.0


async def test_automatic_braking_keeps_direction_handle_aligned_with_travel() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _handles(c, 1, control_mode="stopping_envelope", target_position=10.0, max_speed=1.0)
        await _step(c, 2.0)
        assert (await _train(c))["speed"] > 0.0

        # Re-targeting behind the moving train drops the demand to zero:
        # braking begins while the direction handle stays on the travel side.
        status, _ = await _handles(c, 1, target_position=0.0)
        assert status == 200
        await _step(c, FIXED_STEP)

        train = await _train(c)
        state = _equipment(train, "driving_system_1")["state"]
        bits = _out_bits(train)
        assert state["mode"] == "brake"
        assert state["direction"] == "forward"
        assert bits["direction_handle_forward_1"] is True
        assert bits["direction_handle_backward"] is False
        assert bits["traction_handle_brake"] is True


async def test_automatic_direction_handle_is_relative_to_cab_facing() -> None:
    async with running_app([T_ROLL_FORWARD]) as c:
        await _manual_start(c)
        status, _ = await _handles(
            c, 2, control_mode="stopping_envelope", target_position=-5.0, max_speed=1.0
        )
        assert status == 200
        await _step(c, FIXED_STEP)

        train = await _train(c)
        state = _equipment(train, "driving_system_2")["state"]
        bits = _out_bits(train, cab=2)
        assert train["speed"] > 0.0
        # The -1-facing cab sees the +1 track motion as rearward travel and
        # brakes against it; the forward-only law never tractores cab-rearward,
        # so a rearward traction handle stays a manual move.
        assert state["mode"] == "brake"
        assert state["direction"] == "backward"
        assert bits["direction_handle_backward"] is True


async def test_position_control_stops_at_target_with_speed_cap() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        status, snap = await _handles(
            c,
            1,
            control_mode="stopping_envelope",
            target_position=3.0,
            max_speed=0.8,
        )
        assert status == 200
        state = _equipment(snap, "driving_system_1")["state"]
        assert state["control_mode"] == "stopping_envelope"
        assert state["target_position"] == 3.0
        assert state["max_speed"] == 0.8

        await _step(c, 0.25)
        train = await _train(c)
        state = _equipment(train, "driving_system_1")["state"]
        assert state["mode"] == "traction"
        assert state["direction"] == "forward"
        assert _out_bits(train)["direction_handle_forward_1"] is True

        max_observed_speed = 0.0
        for _ in range(23):
            await _step(c, 0.25)
            train = await _train(c)
            max_observed_speed = max(max_observed_speed, abs(train["speed"]))

        # The train settles slightly past the target and stays: rearward
        # traction is never asserted, so an overshoot is not corrected.
        assert train["position"] == pytest.approx(3.0, abs=0.2)
        assert train["position"] >= 3.0 - 0.05
        assert abs(train["speed"]) < 0.05
        assert max_observed_speed <= 0.8 + 1e-9


async def test_automatic_control_uses_off_deadband_for_small_requests() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        # A target inside the arrival band demands zero speed: the resulting
        # acceleration request falls under the deadband and handles go off.
        status, _ = await _handles(
            c,
            1,
            control_mode="stopping_envelope",
            target_position=0.03,
            max_speed=0.8,
        )
        assert status == 200

        await _step(c, FIXED_STEP)
        state = _equipment(await _train(c), "driving_system_1")["state"]
        assert state["mode"] == "off"
        assert state["acceleration"] == 0.0


async def test_manual_handle_command_returns_control_to_manual_mode() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _handles(c, 1, control_mode="stopping_envelope", target_position=3.0, max_speed=0.5)
        status, snap = await _handles(c, 1, mode="traction", direction="forward", acceleration=0.5)
        assert status == 200
        state = _equipment(snap, "driving_system_1")["state"]
        assert state["control_mode"] == "manual"
        assert state["manual_mode"] == "traction"
        assert state["target_position"] is None
        assert state["law"] is None

        status, snap = await _handles(c, 1, control_mode="manual")
        assert status == 200
        state = _equipment(snap, "driving_system_1")["state"]
        assert state["manual_mode"] == "traction"
        await _step(c, FIXED_STEP)
        assert (await _train(c))["acceleration"] == pytest.approx(0.5)


async def test_automatic_control_requires_valid_targets() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        status, _ = await _handles(c, 1, control_mode="stopping_envelope")
        assert status == 400
        status, _ = await _handles(c, 1, control_mode="hold_speed")
        assert status == 400
        status, _ = await _handles(c, 1, control_mode="stopping_envelope", target_position=10.0)
        assert status == 400
        status, _ = await _handles(
            c,
            1,
            control_mode="stopping_envelope",
            target_position=10.0,
            max_speed=0.0,
        )
        assert status == 400
        status, _ = await _handles(
            c,
            1,
            control_mode="stopping_envelope",
            target_position=10.0,
            max_speed=0.5,
            speed_gain=0.0,
        )
        assert status == 400
        # A law only accepts its own declared targets.
        status, _ = await _handles(c, 1, control_mode="hold_speed", target_position=10.0)
        assert status == 400
        status, _ = await _handles(
            c,
            1,
            control_mode="stopping_envelope",
            target_position=10.0,
            max_speed=0.5,
            target_speed=1.0,
        )
        assert status == 400
        # Unknown control modes — including the retired "speed"/"position"
        # words — are rejected.
        status, _ = await _handles(c, 1, control_mode="pid_jerk")
        assert status == 400
        status, _ = await _handles(c, 1, control_mode="speed", target_speed=1.0)
        assert status == 400


async def test_position_control_holds_when_target_is_behind_the_cab() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        status, snap = await _handles(
            c,
            1,
            control_mode="stopping_envelope",
            target_position=-2.0,
            max_speed=0.7,
        )
        assert status == 200

        await _step(c, 0.25)
        await _step(c, 0.25)
        train = await _train(c)
        assert train["position"] == 0.0
        assert train["speed"] == 0.0
        state = _equipment(train, "driving_system_1")["state"]
        assert state["control_mode"] == "stopping_envelope"
        assert state["target_position"] == -2.0
        assert state["mode"] == "off"
        assert state["direction"] == "off"
        assert state["acceleration"] == 0.0


async def test_position_control_brakes_before_pursuing_a_forward_target() -> None:
    async with running_app([T_ROLL_BACKWARD]) as c:
        await _manual_start(c)
        status, _ = await _handles(
            c,
            1,
            control_mode="stopping_envelope",
            target_position=5.0,
            max_speed=0.8,
        )
        assert status == 200

        # Rolling rearward toward nothing: the controller brakes first, and
        # the direction handle follows current travel while braking.
        await _step(c, 0.25)
        train = await _train(c)
        assert -1.0 < train["speed"] < 0.0
        state = _equipment(train, "driving_system_1")["state"]
        assert state["mode"] == "brake"
        assert state["direction"] == "backward"

        for _ in range(49):
            await _step(c, 0.25)
            train = await _train(c)
            state = _equipment(train, "driving_system_1")["state"]
            # Rearward travel is only ever braked, never tractored toward.
            if state["mode"] == "traction":
                assert state["direction"] == "forward"

        assert train["position"] == pytest.approx(5.0, abs=0.12)
        assert abs(train["speed"]) < 0.05


async def test_position_control_stops_past_a_target_it_cannot_hold_back() -> None:
    async with running_app([T_ROLL_FORWARD]) as c:
        await _manual_start(c)
        status, _ = await _handles(
            c,
            1,
            control_mode="stopping_envelope",
            target_position=0.02,
            max_speed=0.8,
        )
        assert status == 200

        previous_position = (await _train(c))["position"]
        for _ in range(8):
            await _step(c, 0.25)
            train = await _train(c)
            assert train["position"] >= previous_position
            previous_position = train["position"]
            assert train["speed"] > -0.05

        state = _equipment(train, "driving_system_1")["state"]
        assert train["position"] == pytest.approx(0.3, abs=0.1)
        assert abs(train["speed"]) < 0.05
        assert state["mode"] in ("off", "brake")
        assert state["target_position"] == 0.02


async def test_position_control_drives_decreasing_positions_from_cab2() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        status, _ = await _handles(
            c,
            2,
            control_mode="stopping_envelope",
            target_position=-2.0,
            max_speed=0.7,
        )
        assert status == 200

        await _step(c, 0.25)
        train = await _train(c)
        assert train["position"] < 0.0
        state = _equipment(train, "driving_system_2")["state"]
        # Track-decreasing motion is cab-relative forward for the -1 cab.
        assert state["mode"] == "traction"
        assert state["direction"] == "forward"

        for _ in range(23):
            await _step(c, 0.25)
        train = await _train(c)
        assert train["position"] == pytest.approx(-2.0, abs=0.2)
        assert train["position"] <= -2.0 + 0.05
        assert abs(train["speed"]) < 0.05


# -- Control modes are manual plus every registered speed-planning law --------


async def test_driving_snapshot_publishes_control_options_and_law() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        state = _equipment(await _train(c), "driving_system_1")["state"]
        assert state["control_mode"] == "manual"
        assert state["law"] is None
        assert [
            (o["value"], o["label"], tuple(o["targets"])) for o in state["control_options"]
        ] == [
            ("manual", "Manual", ()),
            ("hold_speed", "Hold speed", ("target_speed",)),
            (
                "stopping_envelope",
                "Stopping envelope",
                ("target_position", "max_speed", "speed_gain"),
            ),
        ]

        status, snap = await _handles(
            c, 1, control_mode="stopping_envelope", target_position=3.0, max_speed=0.8
        )
        assert status == 200
        state = _equipment(snap, "driving_system_1")["state"]
        assert state["control_mode"] == "stopping_envelope"
        assert state["law"] == "stopping_envelope"


async def test_law_switches_are_runtime_controls() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        # Arming the law takes over from manual handles.
        await _handles(c, 1, mode="off", direction="forward", acceleration=0.0)
        status, _ = await _handles(
            c, 1, control_mode="stopping_envelope", target_position=2.0, max_speed=0.6
        )
        assert status == 200
        await _step(c, 0.25)
        assert (await _train(c))["position"] > 0.0

        # Back to manual drops the armed law.
        status, snap = await _handles(c, 1, control_mode="manual")
        assert status == 200
        state = _equipment(snap, "driving_system_1")["state"]
        assert state["law"] is None
        assert state["control_mode"] == "manual"

        # An unregistered law is a rejected command, not a startup failure.
        status, _ = await _handles(
            c, 1, control_mode="model_predictive", target_position=2.0, max_speed=0.6
        )
        assert status == 400
