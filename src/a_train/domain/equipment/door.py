"""Door equipment."""

from __future__ import annotations

from ..controls import DoorControl, StcsAtpControl
from ..snapshots import DoorSnapshot
from .base import EquipmentIntent


class Door:
    """One train door whose state changes immediately when commanded."""

    type = "door"

    def __init__(self, key: str, *, initial_state: str = "closed", side: str | None = None) -> None:
        if initial_state not in ("open", "closed"):
            raise ValueError(f"invalid initial door state: {initial_state!r}")
        if side not in (None, "left", "right"):
            raise ValueError(f"invalid door side: {side!r}")
        self._key = key
        self._side = side
        self._initial = initial_state
        self._state = initial_state

    @property
    def key(self) -> str:
        return self._key

    @property
    def closed(self) -> bool:
        return self._state == "closed"

    def apply_control(self, control: DoorControl) -> None:
        if not isinstance(control, DoorControl) or control.command not in ("open", "close"):
            raise ValueError("door control must be 'open' or 'close'")
        self._state = "open" if control.command == "open" else "closed"

    def read_state(self) -> DoorSnapshot:
        return DoorSnapshot(state=self._state)

    def reset(self) -> None:
        self._state = self._initial

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        if self._side is None:
            return ()
        opened = self._state == "open"
        control = (
            StcsAtpControl(left_door_open=opened)
            if self._side == "left"
            else StcsAtpControl(right_door_open=opened)
        )
        return (EquipmentIntent(source=self._key, target="stcs_atp", control=control),)
