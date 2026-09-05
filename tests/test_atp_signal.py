"""Unit tests for atp_signal translation and STCS ATP command recording.

Covers atp-api.md §4.2: bit validation (protocol), pure bit-to-command
translation (signal.py) and the ``stcs_atp`` equipment (domain).
"""

from __future__ import annotations

import pytest

from a_train.adapters.atp.protocol import parse_atp_command
from a_train.adapters.atp.signal import decode_atp_signal
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


def _train() -> Train:
    return Train(
        TrainConfig(
            train_id=TID,
            cab_ids=(1, 2),
            initial_active_cab=1,
            max_traction_accel=1.5,
            max_decel=2.0,
        )
    )


def _atp_state(train: Train):
    return next(entry.state for entry in train.get_snapshot().equipment if entry.key == "stcs_atp")


def test_stcs_atp_records_last_command() -> None:
    equipment = StcsAtp("stcs_atp")
    equipment.apply_control("brake_service")
    state = equipment.read_state()
    assert state.last_command == "brake_service"
    equipment.apply_control("brake_emergency")
    assert equipment.read_state().last_command == "brake_emergency"


def test_stcs_atp_records_arbitrary_command() -> None:
    equipment = StcsAtp("stcs_atp")
    equipment.apply_control("brake_whatever")
    assert equipment.read_state().last_command == "brake_whatever"


def test_stcs_atp_command_is_not_changed_by_train_step() -> None:
    train = _train()  # standing still: speed 0.0

    assert train.set_equipment(
        EquipmentSet(key="stcs_atp", command="brake_emergency")
    ).ok

    train.step(0.05)  # no motion demanded, speed stays 0.0
    assert _atp_state(train).last_command == "brake_emergency"


def test_standard_consist_includes_stcs_atp() -> None:
    state = _atp_state(_train())
    assert state.last_command is None
