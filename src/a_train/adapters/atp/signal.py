"""Translate ``ATP_COMMAND.atp_signal`` bit strings into core commands.

``atp_signal`` is a string of ``'0'``/``'1'`` characters, e.g. ``"0001000"``;
the leftmost character is bit index 0. Decoding is a stateless translation:
every bit position the string defines is asserted ('1' active, '0'
inactive), positions beyond the string length keep the state already stored
in the core. Handlers are pure ``(train_id, cab_id, active) -> commands``
functions; the state machine that stores and manipulates the flags lives in
the core's ``stcs_atp`` equipment (atp-api.md §4.2), so command ordering
through the queue keeps replay deterministic -- physics stays in the core
(§2.6), this module only translates protocol content.

Bit layout: index 0 reserved, 1 traction cut-off, 2 service brake, 3
emergency brake.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ...domain.train import EquipmentSet
from ...simulation.commands import Command, EquipmentCommand

BIT_RESERVED = 0
BIT_TRACTION_CUT_OFF = 1
BIT_SERVICE_BRAKE = 2
BIT_EMERGENCY_BRAKE = 3

# (train_id, cab_id, active) -> core commands the asserted bit requests.
BitHandler = Callable[[str, int, bool], Sequence[Command]]


def _traction_cut_off(train_id: str, _cab_id: int, active: bool) -> Sequence[Command]:
    return [
        EquipmentCommand(
            train_id=train_id,
            payload=EquipmentSet(
                key="stcs_atp",
                command="traction_cut" if active else "traction_release",
            ),
        )
    ]


def _service_brake(train_id: str, _cab_id: int, active: bool) -> Sequence[Command]:
    return [
        EquipmentCommand(
            train_id=train_id,
            payload=EquipmentSet(
                key="stcs_atp",
                command="brake_service" if active else "brake_service_off",
            ),
        )
    ]


def _emergency_brake(train_id: str, _cab_id: int, active: bool) -> Sequence[Command]:
    return [
        EquipmentCommand(
            train_id=train_id,
            payload=EquipmentSet(
                key="stcs_atp",
                command="brake_emergency" if active else "brake_emergency_off",
            ),
        )
    ]


HANDLERS: dict[int, BitHandler] = {
    BIT_TRACTION_CUT_OFF: _traction_cut_off,
    BIT_SERVICE_BRAKE: _service_brake,
    BIT_EMERGENCY_BRAKE: _emergency_brake,
}


def decode_atp_signal(bits: str, train_id: str, cab_id: int) -> list[Command]:
    """Translate a validated ``atp_signal`` string into core commands.

    Order is deterministic: bit indices ascending, left to right. The stored
    flags are independent, so a string asserting both brake bits leaves both
    set. Assumes the caller already validated framing
    (protocol.parse_atp_command).
    """

    commands: list[Command] = []
    for index, flag in enumerate(bits):
        handler = HANDLERS.get(index)
        if handler is not None:
            commands.extend(handler(train_id, cab_id, flag == "1"))
    return commands
