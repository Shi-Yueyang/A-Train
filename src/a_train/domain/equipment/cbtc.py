"""Cab-scoped CBTC authorization state."""

from __future__ import annotations

from ..controls import CbtcControl
from ..snapshots import CbtcSnapshot
from .base import TrainMotion


class Cbtc:
    """Stores the CBTC authorization state for one cab."""

    type = "cbtc"

    def __init__(
        self,
        key: str,
        *,
        cab_id: int,
        is_cbtc_authorized: bool = False,
    ) -> None:
        if not isinstance(is_cbtc_authorized, bool):
            raise ValueError("is_cbtc_authorized must be a boolean")
        self._key = key
        self._cab_id = cab_id
        self._initial_is_cbtc_authorized = is_cbtc_authorized
        self._is_cbtc_authorized = is_cbtc_authorized

    @property
    def key(self) -> str:
        return self._key

    @property
    def cab_id(self) -> int:
        return self._cab_id

    def apply_control(self, control: CbtcControl, *, received_at: float | None = None) -> None:
        if not isinstance(control, CbtcControl):
            raise ValueError("cbtc control is invalid")
        if control.cab_id is not None and control.cab_id != self._cab_id:
            raise ValueError("cab_id does not match equipment key")
        if control.is_cbtc_authorized is None:
            raise ValueError("cbtc requires is_cbtc_authorized")
        if not isinstance(control.is_cbtc_authorized, bool):
            raise ValueError("is_cbtc_authorized must be a boolean")
        self._is_cbtc_authorized = control.is_cbtc_authorized

    def read_state(self) -> CbtcSnapshot:
        return CbtcSnapshot(
            cab_id=self._cab_id,
            is_cbtc_authorized=self._is_cbtc_authorized,
        )

    def reset(self) -> None:
        self._is_cbtc_authorized = self._initial_is_cbtc_authorized

    def step(self, dt: float, motion: TrainMotion) -> None:
        pass

    def emit_intents(self):
        return ()
