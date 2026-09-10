"""Unit tests for atp_signal validation and STCS ATP command recording.

Covers atp-api.md §4.2: bit validation (protocol) and the ``stcs_atp``
equipment (domain).
"""

from __future__ import annotations

import pytest

from a_train.adapters.atp.protocol import parse_atp_command
from a_train.domain.controls import CabStateControl, StcsAtpControl, TrainControl
from a_train.domain.equipment import StcsAtp
from a_train.domain.train import EquipmentControlRequest, Train, TrainConfig

TID, CAB = "TRAIN001", 1


# -- Validation ------------------------------------------------------------------


def test_atp_signal_parses_as_bit_string() -> None:
    drive, door, bits = parse_atp_command({"atp_signal": "0001000"}, TID, CAB)
    assert (drive, door, bits) == (None, None, "0001000")


def test_atp_signal_alone_satisfies_the_payload_requirement() -> None:
    # No drive_demand and no door: a bare atp_signal is a valid command.
    assert parse_atp_command({"atp_signal": "0"}, TID, CAB)[2] == "0"


def test_atp_signal_allows_underscore_separators() -> None:
    assert parse_atp_command({"atp_signal": "0_1_0"}, TID, CAB)[2] == "010"


@pytest.mark.parametrize("value", ["01x", "1 1", "true", 1, 0.1, ["01"]])
def test_invalid_atp_signal_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="atp_signal"):
        parse_atp_command({"atp_signal": value}, TID, CAB)


def test_empty_atp_signal_rejected() -> None:
    with pytest.raises(ValueError, match="atp_signal"):
        parse_atp_command({"atp_signal": ""}, TID, CAB)


def test_missing_payload_still_rejected() -> None:
    with pytest.raises(ValueError, match="atp_signal"):
        parse_atp_command({"cab_id": CAB}, TID, CAB)


# -- Core-side state machine (domain) ---------------------------------------------


def _train(*, initial_speed: float = 0.0) -> Train:
    return Train(
        TrainConfig(
            train_id=TID,
            cab_ids=(1, 2),
            initial_active_cab=1,
            max_traction_accel=1.5,
            max_decel=2.0,
            initial_speed=initial_speed,
        )
    )


def _atp_state(train: Train):
    return next(
        entry.state for entry in train.get_snapshot().equipment if entry.key == "stcs_atp_1"
    )


def test_stcs_atp_decodes_binary_command_into_train_in_states() -> None:
    equipment = StcsAtp("stcs_atp_1", cab_id=1)
    equipment.apply_control(StcsAtpControl("101100001"))
    state = equipment.read_state()
    assert state.last_command == "101100001"
    assert equipment.train_in_states["emergency_brake_1"] is True
    assert equipment.train_in_states["emergency_brake_2"] is False
    assert equipment.train_in_states["maximum_service_brake_7"] is True
    assert equipment.train_in_states["ato_enable"] is True
    assert equipment.train_in_states["service_brake_1"] is True
    assert equipment.train_out_states["emergency_brake_1_inner_feedback"] is False
    assert equipment.train_out_states["emergency_brake_2_inner_feedback"] is True
    assert equipment.train_out_states["emergency_brake_feedback"] is False
    assert equipment.train_out_states["service_brake_7_feedback"] is False
    assert equipment.read_state().train_out_signal == "010000001000000000000000000000"


def test_stcs_atp_short_command_preserves_unmentioned_states() -> None:
    equipment = StcsAtp("stcs_atp_1", cab_id=1)
    equipment.apply_control(StcsAtpControl("000000001"))
    equipment.apply_control(StcsAtpControl("0"))
    assert equipment.train_in_states["service_brake_1"] is True
    assert equipment.train_in_states["emergency_brake_1"] is False
    assert equipment.train_out_states["emergency_brake_feedback"] is True


def test_stcs_atp_maximum_service_brake_requests_train_deceleration() -> None:
    train = _train(initial_speed=1.0)

    assert train.set_equipment(EquipmentControlRequest(key="stcs_atp_1", command="001")).ok
    train.step(0.05)
    assert train.get_snapshot().acceleration == -2.0
    train.step(0.05)
    assert train.get_snapshot().acceleration == -2.0

    # The protection brake is a state assertion: releasing the bit removes
    # the braking force; the train coasts on its remaining speed.
    assert train.set_equipment(EquipmentControlRequest(key="stcs_atp_1", command="000")).ok
    train.step(0.05)
    assert train.get_snapshot().acceleration == 0.0
    assert train.get_snapshot().speed > 0.0


def test_stcs_atp_service_brake_does_not_drive_a_standing_train() -> None:
    train = _train()  # standing still

    assert train.set_equipment(EquipmentControlRequest(key="stcs_atp_1", command="001")).ok
    train.step(0.05)
    snap = train.get_snapshot()
    assert snap.speed == 0.0
    assert snap.acceleration == 0.0
    assert snap.drive_demand == 0.0  # protection no longer writes the legacy lever


@pytest.mark.parametrize("command", ["", "2", "010x", "true"])
def test_stcs_atp_rejects_non_binary_commands(command: str) -> None:
    equipment = StcsAtp("stcs_atp_1", cab_id=1)
    with pytest.raises(ValueError, match="0.*1"):
        equipment.apply_control(StcsAtpControl(command))


def test_stcs_atp_command_is_not_changed_by_train_step() -> None:
    train = _train()  # standing still: speed 0.0

    assert train.set_equipment(
        EquipmentControlRequest(key="stcs_atp_1", command="10000000000000000")
    ).ok

    train.step(0.05)  # no motion demanded, speed stays 0.0
    assert _atp_state(train).last_command == "10000000000000000"


def test_standard_consist_includes_stcs_atp() -> None:
    train = _train()
    equipment = {entry.key: entry.state for entry in train.get_snapshot().equipment}
    assert equipment["stcs_atp_1"].last_command is None
    assert equipment["stcs_atp_2"].last_command is None


def test_stcs_atp_state_is_bound_to_each_cab() -> None:
    train = _train()
    assert train.set_equipment(EquipmentControlRequest(key="stcs_atp_1", command="100")).ok
    equipment = {entry.key: entry.state for entry in train.get_snapshot().equipment}
    assert equipment["stcs_atp_1"].last_command == "100"
    assert equipment["stcs_atp_2"].last_command is None


def test_cab_key_state_is_reflected_in_matching_stcs_output() -> None:
    train = _train()
    equipment = {entry.key: entry.state for entry in train.get_snapshot().equipment}
    assert equipment["stcs_atp_1"].train_out_states[17].value is False
    assert equipment["stcs_atp_2"].train_out_states[17].value is False

    assert train.apply_control(TrainControl(cab_id=2, key=True)).ok
    equipment = {entry.key: entry.state for entry in train.get_snapshot().equipment}
    assert equipment["stcs_atp_1"].train_out_states[17].value is False
    assert equipment["stcs_atp_2"].train_out_states[17].value is True


def test_cab_activation_is_reflected_in_matching_stcs_output() -> None:
    train = _train()
    equipment = {entry.key: entry.state for entry in train.get_snapshot().equipment}
    assert equipment["stcs_atp_1"].train_out_states[4].value is True
    assert equipment["stcs_atp_2"].train_out_states[4].value is False
    assert equipment["stcs_atp_1"].train_out_states[8].value is False
    assert equipment["stcs_atp_2"].train_out_states[8].value is True

    assert train.apply_control(TrainControl(cab_id=2, active=True)).ok
    equipment = {entry.key: entry.state for entry in train.get_snapshot().equipment}
    assert equipment["stcs_atp_1"].train_out_states[4].value is True
    assert equipment["stcs_atp_2"].train_out_states[4].value is True
    assert equipment["stcs_atp_1"].train_out_states[8].value is False
    assert equipment["stcs_atp_2"].train_out_states[8].value is False

    assert train.apply_control(TrainControl(cab_id=1, active=False)).ok
    equipment = {entry.key: entry.state for entry in train.get_snapshot().equipment}
    assert equipment["stcs_atp_1"].train_out_states[4].value is False
    assert equipment["stcs_atp_2"].train_out_states[4].value is True
    assert equipment["stcs_atp_1"].train_out_states[8].value is True
    assert equipment["stcs_atp_2"].train_out_states[8].value is False


def test_stcs_observes_generic_cab_state() -> None:
    equipment = StcsAtp("stcs_atp_2", cab_id=2)
    equipment.observe_cab_state(CabStateControl(cab_id=2, active=True, key_inserted=True))
    assert equipment.train_out_states["cab_activation"] is True
    assert equipment.train_out_states["key_activation"] is True

    equipment.observe_cab_state(CabStateControl(cab_id=1, active=False, key_inserted=False))
    assert equipment.train_out_states["cab_activation"] is True
    assert equipment.train_out_states["key_activation"] is True


def test_door_state_feedback_tracks_left_and_right_doors() -> None:
    train = _train()
    assert _atp_state(train).train_out_signal[20] == "0"
    assert _atp_state(train).train_out_signal[21] == "0"

    assert train.set_equipment(EquipmentControlRequest(key="left_door", command="open")).ok
    signal = _atp_state(train).train_out_signal
    assert signal[20] == "1" and signal[21] == "0"

    assert train.set_equipment(EquipmentControlRequest(key="right_door", command="open")).ok
    assert _atp_state(train).train_out_signal == "11111" + "0" * 15 + "11" + "0" * 8

    assert train.set_equipment(EquipmentControlRequest(key="left_door", command="close")).ok
    assert _atp_state(train).train_out_signal == "11111" + "0" * 15 + "01" + "0" * 8


def test_door_state_feedback_follows_configured_and_reset_door_state() -> None:
    train = Train(
        TrainConfig(
            train_id=TID,
            cab_ids=(1,),
            initial_active_cab=1,
            max_traction_accel=1.0,
            max_decel=1.0,
            initial_door_state="open",
        )
    )
    assert _atp_state(train).train_out_signal == "11111" + "0" * 15 + "11" + "0" * 8

    assert train.set_equipment(EquipmentControlRequest(key="left_door", command="close")).ok
    assert _atp_state(train).train_out_signal[20] == "0"
    train.reset()
    assert _atp_state(train).train_out_signal == "11111" + "0" * 15 + "11" + "0" * 8


def test_stcs_atp_snapshot_lists_named_states_in_bit_order() -> None:
    equipment = StcsAtp("stcs_atp_1", cab_id=1)
    equipment.apply_control(StcsAtpControl("100"))
    state = equipment.read_state()

    assert [s.name for s in state.train_in_states] == list(
        StcsAtp.ATP_TO_TRAIN_SIGNAL_BY_BIT.values()
    )
    assert state.train_in_states[0].value is True
    assert state.train_in_states[1].value is False
    assert [s.name for s in state.train_out_states] == list(
        StcsAtp.TRAIN_TO_ATP_SIGNAL_BY_BIT.values()
    )
    assert state.train_out_states[0].name == "emergency_brake_1_inner_feedback"
    assert state.train_out_states[0].value is False


def test_stcs_atp_has_train_out_state_shape() -> None:
    equipment = StcsAtp("stcs_atp_1", cab_id=1)
    states = equipment.train_out_states

    assert len(states) == 30
    assert all(
        value
        is (
            name
            in {
                "emergency_brake_1_inner_feedback",
                "emergency_brake_2_inner_feedback",
                "emergency_brake_feedback",
                "service_brake_7_feedback",
                "sleep_signal",
            }
        )
        for name, value in states.items()
    )
    assert states["emergency_brake_1_inner_feedback"] is True
    assert states["cab_activation"] is False
    assert states["c2_control_state_2_2"] is False

    states["cab_activation"] = True
    assert equipment.train_out_states["cab_activation"] is False
    assert equipment.read_state().train_out_signal == "1111" + "0" * 4 + "1" + "0" * 21
