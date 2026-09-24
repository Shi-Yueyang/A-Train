"""Cab system-selection switch box equipment.

One three-position hardware switch (C2 / AUTO / CBTC). Every collect pass it
asserts its position to the matching cab's STCS ATP instance through the shared
``stcs_atp`` feedback group, mirroring the door pattern: the ATP
``system_switch_c2`` / ``system_switch_auto`` / ``system_switch_cbtc`` bits read
the switch as installed, mutually exclusive hardware state. A cab without a
fitted box leaves those three rows as free operator-asserted panel signals, and
a cut ``switch_box_<cab> -> stcs_atp_duo_<cab>`` wire freezes the mirror so the
operator can drive it again (stale-not-zeroed, architectural.md section 3.7).
"""

from __future__ import annotations

from ..controls import StcsAtpControl, SwitchBoxControl
from ..snapshots import SwitchBoxSnapshot
from .base import EquipmentIntent


class SwitchBox:
    """A cab's three-position system-selection switch."""

    type = "switch_box"
    POSITIONS = ("c2", "auto", "cbtc")

    def __init__(self, key: str, *, cab_id: int, initial_position: str = "c2") -> None:
        if initial_position not in self.POSITIONS:
            raise ValueError(f"invalid initial switch box position: {initial_position!r}")
        self._key = key
        self._cab_id = cab_id
        self._initial = initial_position
        self._position = initial_position

    @property
    def key(self) -> str:
        return self._key

    @property
    def cab_id(self) -> int:
        return self._cab_id

    def apply_control(self, control: SwitchBoxControl, *, received_at: float | None = None) -> None:
        if not isinstance(control, SwitchBoxControl) or control.position not in self.POSITIONS:
            raise ValueError(f"switch box position must be one of {self.POSITIONS}")
        self._position = control.position

    def read_state(self) -> SwitchBoxSnapshot:
        return SwitchBoxSnapshot(cab_id=self._cab_id, position=self._position)

    def reset(self) -> None:
        self._position = self._initial

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        return (
            EquipmentIntent(
                source=self._key,
                target="stcs_atp",
                control=StcsAtpControl(cab_id=self._cab_id, system_switch=self._position),
            ),
        )
