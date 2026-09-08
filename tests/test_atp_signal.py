"""Unit tests for atp_signal translation and STCS ATP command recording.

Covers atp-api.md §4.2: bit validation (protocol), pure bit-to-command
translation (signal.py) and the ``stcs_atp`` equipment (domain).
"""

from __future__ import annotations

import pytest

from a_train.adapters.atp.protocol import parse_atp_command
from a_train.adapters.atp.signal import decode_atp_signal
from a_train.domain.controls import StcsAtpControl
from a_train.domain.equipment import StcsAtp
from a_train.domain.train import EquipmentSet, Train, TrainConfig

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


# -- Translation: defined bits assert, undefined positions keep state ------------


def test_asserted_bits_produce_commands_in_ascending_order() -> None:
    commands = decode_atp_signal("0111", TID, CAB)
    assert [c.payload.command for c in commands] == ["0111"]
    assert all(c.train_id == TID and c.payload.key == "stcs_atp" for c in commands)


def test_explicit_zero_bits_produce_release_commands() -> None:
    commands = decode_atp_signal("010", TID, CAB)  # idx1 '1', idx2 '0'
    assert [c.payload.command for c in commands] == ["010"]


def test_reserved_and_unbound_positions_are_recorded_unchanged() -> None:
    assert [c.payload.command for c in decode_atp_signal("0", TID, CAB)] == ["0"]


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
    return next(entry.state for entry in train.get_snapshot().equipment if entry.key == "stcs_atp")


def test_stcs_atp_decodes_binary_command_into_train_in_states() -> None:
    equipment = StcsAtp("stcs_atp")
    equipment.apply_control(StcsAtpControl("101100001"))
    state = equipment.read_state()
    assert state.last_command == "101100001"
    assert equipment.train_in_states["emergency_brake_1"] is True
    assert equipment.train_in_states["emergency_brake_2"] is False
    assert equipment.train_in_states["maximum_service_brake_7"] is True
    assert equipment.train_in_states["ato_enable"] is True
    assert equipment.train_in_states["service_brake_1"] is True
    assert equipment.train_out_states["emergency_brake_1_inner_feedback"] is True
    assert equipment.train_out_states["emergency_brake_2_inner_feedback"] is False
    assert equipment.train_out_states["emergency_brake_feedback"] is True
    assert equipment.train_out_states["service_brake_7_feedback"] is True
    assert equipment.read_state().train_out_signal == "101100000000000000000000000000"


def test_stcs_atp_short_command_preserves_unmentioned_states() -> None:
    equipment = StcsAtp("stcs_atp")
    equipment.apply_control(StcsAtpControl("000000001"))
    equipment.apply_control(StcsAtpControl("0"))
    assert equipment.train_in_states["service_brake_1"] is True
    assert equipment.train_in_states["emergency_brake_1"] is False


def test_stcs_atp_maximum_service_brake_requests_train_deceleration() -> None:
    train = _train(initial_speed=1.0)

    assert train.set_equipment(
        EquipmentSet(key="stcs_atp", command="001")
    ).ok
    train.step(0.05)
    assert train.get_snapshot().acceleration == -2.0
    train.step(0.05)
    assert train.get_snapshot().acceleration == -2.0

    assert train.set_equipment(
        EquipmentSet(key="stcs_atp", command="000")
    ).ok
    train.step(0.05)
    assert train.get_snapshot().acceleration == -2.0


@pytest.mark.parametrize("command", ["", "2", "010x", "true"])
def test_stcs_atp_rejects_non_binary_commands(command: str) -> None:
    equipment = StcsAtp("stcs_atp")
    with pytest.raises(ValueError, match="0.*1"):
        equipment.apply_control(StcsAtpControl(command))


def test_stcs_atp_command_is_not_changed_by_train_step() -> None:
    train = _train()  # standing still: speed 0.0

    assert train.set_equipment(
        EquipmentSet(key="stcs_atp", command="10000000000000000")
    ).ok

    train.step(0.05)  # no motion demanded, speed stays 0.0
    assert _atp_state(train).last_command == "10000000000000000"


def test_standard_consist_includes_stcs_atp() -> None:
    state = _atp_state(_train())
    assert state.last_command is None


def test_door_state_feedback_tracks_left_and_right_doors() -> None:
    train = _train()
    assert _atp_state(train).train_out_signal[20] == "0"
    assert _atp_state(train).train_out_signal[21] == "0"

    assert train.set_equipment(EquipmentSet(key="left_door", command="open")).ok
    signal = _atp_state(train).train_out_signal
    assert signal[20] == "1" and signal[21] == "0"

    assert train.set_equipment(EquipmentSet(key="right_door", command="open")).ok
    assert _atp_state(train).train_out_signal == "0" * 20 + "11" + "0" * 8

    assert train.set_equipment(EquipmentSet(key="left_door", command="close")).ok
    assert _atp_state(train).train_out_signal == "0" * 20 + "01" + "0" * 8


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
    assert _atp_state(train).train_out_signal == "0" * 20 + "11" + "0" * 8

    assert train.set_equipment(EquipmentSet(key="left_door", command="close")).ok
    assert _atp_state(train).train_out_signal[20] == "0"
    train.reset()
    assert _atp_state(train).train_out_signal == "0" * 20 + "11" + "0" * 8


def test_stcs_atp_has_train_out_state_shape() -> None:
    equipment = StcsAtp("stcs_atp")
    states = equipment.train_out_states

    assert len(states) == 30
    assert all(value is False for value in states.values())
    assert states["emergency_brake_1_inner_feedback"] is False
    assert states["cab_activation"] is False
    assert states["c2_control_state_2_2"] is False

    states["cab_activation"] = True
    assert equipment.train_out_states["cab_activation"] is False
    assert equipment.read_state().train_out_signal == "0" * 30
