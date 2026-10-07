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
from .base import EquipmentIntent, TrainMotion


class SwitchBox:
    """A cab's three-position system-selection switch."""

    type = "switch_box"
    POSITIONS = ("c2", "auto", "cbtc")

    def __init__(
        self,
        key: str,
        *,
        cab_id: int,
        initial_position: str = "c2",
        initial_c2_authorized: bool = False,
        initial_cbtc_authorized: bool = False,
    ) -> None:
        if initial_position not in self.POSITIONS:
            raise ValueError(f"invalid initial switch box position: {initial_position!r}")
        self._key = key
        self._cab_id = cab_id
        self._initial = initial_position
        self._position = initial_position
        self._initial_c2_authorized = initial_c2_authorized
        self._initial_cbtc_authorized = initial_cbtc_authorized
        self._c2_authorized = initial_c2_authorized
        self._cbtc_authorized = initial_cbtc_authorized

    @property
    def key(self) -> str:
        return self._key

    @property
    def cab_id(self) -> int:
        return self._cab_id

    def apply_control(self, control: SwitchBoxControl, *, received_at: float | None = None) -> None:
        if not isinstance(control, SwitchBoxControl):
            raise ValueError("switch box control is invalid")
        if control.position is not None:
            if control.position not in self.POSITIONS:
                raise ValueError(f"switch box position must be one of {self.POSITIONS}")
            self._position = control.position
        if control.c2_authorized is not None:
            if not isinstance(control.c2_authorized, bool):
                raise ValueError("switch box c2_authorized must be a boolean")
            self._c2_authorized = control.c2_authorized
        if control.cbtc_authorized is not None:
            if not isinstance(control.cbtc_authorized, bool):
                raise ValueError("switch box cbtc_authorized must be a boolean")
            self._cbtc_authorized = control.cbtc_authorized
        if (
            control.position is None
            and control.c2_authorized is None
            and control.cbtc_authorized is None
        ):
            raise ValueError("switch box requires system_switch or authorization state")

    def read_state(self) -> SwitchBoxSnapshot:
        return SwitchBoxSnapshot(
            cab_id=self._cab_id,
            position=self._position,
            c2_authorized=self._c2_authorized,
            cbtc_authorized=self._cbtc_authorized,
        )

    def reset(self) -> None:
        self._position = self._initial
        self._c2_authorized = self._initial_c2_authorized
        self._cbtc_authorized = self._initial_cbtc_authorized

    def step(self, dt: float, motion: TrainMotion) -> None:
        pass

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        c2_active = self._position == "c2" or (
            self._position == "auto" and self._c2_authorized and not self._cbtc_authorized
        )
        cbtc_active = self._position == "cbtc" or (
            self._position == "auto" and self._cbtc_authorized and not self._c2_authorized
        )
        return (
            EquipmentIntent(
                source=self._key,
                target="stcs_atp",
                control=StcsAtpControl(
                    cab_id=self._cab_id,
                    system_switch=self._position,
                    train_out_signals={
                        "c2_control_state_1_1": c2_active,
                        "c2_control_state_1_2": c2_active,
                        "c2_control_state_2_1": cbtc_active,
                        "c2_control_state_2_2": cbtc_active,
                    },
                ),
            ),
        )
