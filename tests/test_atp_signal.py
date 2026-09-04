"""Unit tests for the atp_signal parsing and decode skeleton (atp-api.md §4.2)."""

from __future__ import annotations

import pytest

from a_train.adapters.atp.protocol import parse_train_command
from a_train.adapters.atp.signal import decode_atp_signal

TID, CAB = "TRAIN001", 1


# -- Validation ------------------------------------------------------------------


def test_atp_signal_parses_as_bit_string() -> None:
    drive, door, bits = parse_train_command({"atp_signal": "0001000"}, TID, CAB)
    assert (drive, door, bits) == (None, None, "0001000")


def test_atp_signal_alone_satisfies_the_payload_requirement() -> None:
    # No drive_demand and no door: a bare atp_signal is a valid command.
    assert parse_train_command({"atp_signal": "0"}, TID, CAB)[2] == "0"


@pytest.mark.parametrize("value", ["01x", "1 1", "true", 1, 0.1, ["01"]])
def test_invalid_atp_signal_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="atp_signal"):
        parse_train_command({"atp_signal": value}, TID, CAB)


def test_empty_atp_signal_rejected() -> None:
    with pytest.raises(ValueError, match="atp_signal"):
        parse_train_command({"atp_signal": ""}, TID, CAB)


def test_missing_payload_still_rejected() -> None:
    with pytest.raises(ValueError, match="atp_signal"):
        parse_train_command({"cab_id": CAB}, TID, CAB)


# -- Decode registry ---------------------------------------------------------------


def test_unbound_bits_produce_no_commands() -> None:
    assert decode_atp_signal("0001000", TID, CAB) == []


def test_bound_handlers_fire_in_ascending_bit_order(monkeypatch: pytest.MonkeyPatch) -> None:
    import a_train.adapters.atp.signal as signal

    monkeypatch.setitem(signal.HANDLERS, 1, lambda t, c: [{"bit": 1, "train": t, "cab": c}])
    monkeypatch.setitem(signal.HANDLERS, 3, lambda t, c: [{"bit": 3, "train": t, "cab": c}])

    commands = decode_atp_signal("01010", TID, CAB)
    assert commands == [
        {"bit": 1, "train": TID, "cab": CAB},
        {"bit": 3, "train": TID, "cab": CAB},
    ]


def test_zero_bits_do_not_trigger_bound_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    import a_train.adapters.atp.signal as signal

    monkeypatch.setitem(signal.HANDLERS, 2, lambda t, c: [{"bit": 2}])
    assert decode_atp_signal("0000", TID, CAB) == []
