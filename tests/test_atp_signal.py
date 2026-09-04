"""Unit tests for atp_signal translation and the core-side state machine.

Covers atp-api.md §4.2: bit validation (protocol), pure bit-to-command
translation (signal.py), and the ``stcs_atp`` equipment state machine
(domain), including the placeholder release-at-rest rule.
"""

from __future__ import annotations

import pytest

from a_train.adapters.atp.protocol import parse_atp_command
from a_train.adapters.atp.signal import decode_atp_signal
from a_train.domain.equipment import StcsAtp
from a_train.domain.train import EquipmentSet, Train, TrainConfig, TrainControl

TID, CAB = "TRAIN001", 1


# -- Validation ------------------------------------------------------------------


def test_atp_signal_parses_as_bit_string() -> None:
    drive, door, bits = parse_atp_command({"atp_signal": "0001000"}, TID, CAB)
    assert (drive, door, bits) == (None, None, "0001000")


def test_atp_signal_alone_satisfies_the_payload_requirement() -> None:
    # No drive_demand and no door: a bare atp_signal is a valid command.
    assert parse_atp_command({"atp_signal": "0"}, TID, CAB)[2] == "0"


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
    assert [c.payload.command for c in commands] == [
        "traction_cut",
        "brake_service",
        "brake_emergency",
    ]
    assert all(c.train_id == TID and c.payload.key == "stcs_atp" for c in commands)


def test_explicit_zero_bits_produce_release_commands() -> None:
    commands = decode_atp_signal("010", TID, CAB)  # idx1 '1', idx2 '0'
    assert [c.payload.command for c in commands] == ["traction_cut", "brake_service_off"]


def test_reserved_and_unbound_positions_produce_no_commands() -> None:
    # Only index 0 (reserved) is defined: nothing is asserted, state keeps.
    assert decode_atp_signal("0", TID, CAB) == []


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
    return train.get_snapshot().equipment["stcs_atp"]


def test_brake_flags_are_independent() -> None:
    equipment = StcsAtp()
    equipment.apply_control("brake_service")
    equipment.apply_control("brake_emergency")
    state = equipment.read_state()
    assert state.service and state.emergency
    equipment.apply_control("brake_emergency_off")
    state = equipment.read_state()
    assert state.service and not state.emergency


def test_invalid_command_raises() -> None:
    with pytest.raises(ValueError, match="stcs_atp"):
        StcsAtp().apply_control("brake_whatever")


def test_release_at_rest_rule_combines_core_speed() -> None:
    train = _train()  # standing still: speed 0.0

    for command in ("traction_cut", "brake_emergency"):
        assert train.set_equipment(EquipmentSet(key="stcs_atp", command=command)).ok

    train.step(0.05)  # no motion demanded, speed stays 0.0
    state = _atp_state(train)
    assert not state.service and not state.emergency  # placeholder release rule fired
    assert state.traction_cutoff  # cut-off persists

    assert train.set_equipment(EquipmentSet(key="stcs_atp", command="traction_release")).ok
    assert not _atp_state(train).traction_cutoff


def test_brake_persists_while_moving() -> None:
    train = _train()
    train.apply_control(TrainControl(cab_id=1, drive_demand=1.0))
    train.step(0.5)  # accelerate: speed > 0
    assert train.set_equipment(EquipmentSet(key="stcs_atp", command="brake_service")).ok
    train.step(0.1)
    assert _atp_state(train).service


def test_standard_consist_includes_stcs_atp() -> None:
    state = _atp_state(_train())
    assert not (state.traction_cutoff or state.service or state.emergency)
