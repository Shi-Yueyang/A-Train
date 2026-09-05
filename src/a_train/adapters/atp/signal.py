"""Translate ``ATP_COMMAND.atp_signal`` into one core command.

``atp_signal`` is a string of ``'0'``/``'1'`` characters, e.g. ``"0001000"``;
the leftmost character is bit index 0. The validated signal is kept intact and
recorded by the core's ``stcs_atp`` equipment; bit meanings are not interpreted
by the simulator.
"""

from __future__ import annotations

from ...domain.train import EquipmentSet
from ...simulation.commands import Command, EquipmentCommand

def decode_atp_signal(bits: str, train_id: str, cab_id: int) -> list[Command]:
    """Create one command that records the validated signal unchanged."""

    return [
        EquipmentCommand(
            train_id=train_id,
            payload=EquipmentSet(key="stcs_atp", command=bits),
        )
    ]
