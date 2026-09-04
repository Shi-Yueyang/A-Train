"""Decode ``ATP_COMMAND.atp_signal`` bit strings into core commands (skeleton).

``atp_signal`` is a string of ``'0'``/``'1'`` characters, e.g. ``"0001000"``;
the leftmost character is bit index 0. Each bit position carries a meaning
agreed between ATP and the simulator (atp-api.md §4.2); a ``'1'`` means the
meaning is active, a ``'0'`` means inactive.

Decoding is registry-driven: bind a bit index to a handler that returns
transport-neutral core commands, and the manager submits them through the
command queue like any other input -- physics stays in the core (§2.6), this
module only translates protocol content. A ``'1'`` bit with no registered
handler is ignored, so the field can be exercised end-to-end before its
meanings are implemented.

Planned meanings (skeleton, not implemented yet; atp-api.md §4.2): bit 0
reserved, bit 1 traction cut-off, bit 2 service brake, bit 3 emergency
brake. Example of a future binding (the brake command the ATP bit will
eventually transfer into)::

    HANDLERS[2] = lambda train_id, cab_id: [
        TrainControlCommand(
            train_id=train_id,
            payload=TrainControl(cab_id=cab_id, drive_demand=-1.0),
        )
    ]
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

# (train_id, cab_id) -> core commands the active bit requests.
BitHandler = Callable[[str, int], Sequence[Any]]

# Bit index -> registered handler. Populate as bit meanings are decided;
# see the module docstring for the traction-cut-off / brake plan.
HANDLERS: dict[int, BitHandler] = {}


def decode_atp_signal(bits: str, train_id: str, cab_id: int) -> list[Any]:
    """Translate a validated ``atp_signal`` string into core commands.

    Order is deterministic: bit indices ascending, left to right. Assumes the
    caller already validated framing (protocol.parse_atp_command).
    """

    commands: list[Any] = []
    for index, flag in enumerate(bits):
        if flag == "1":
            handler = HANDLERS.get(index)
            if handler is not None:
                commands.extend(handler(train_id, cab_id))
    return commands
