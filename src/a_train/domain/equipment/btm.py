"""Balise transmission module equipment."""

from __future__ import annotations

import base64

from ..controls import BtmControl
from ..snapshots import BtmSnapshot


class Btm:
    """BTM with opaque payload storage for one cab."""

    type = "btm"

    def __init__(self, key: str, cab_id: int) -> None:
        self._key = key
        self._cab_id = cab_id
        self._pending: bytes | None = None
        self._received_count = 0

    @property
    def cab_id(self) -> int:
        return self._cab_id

    @property
    def key(self) -> str:
        return self._key

    def apply_control(self, control: BtmControl) -> None:
        if not isinstance(control, BtmControl):
            raise ValueError("btm control is invalid")
        if control.cab_id is not None and control.cab_id != self._cab_id:
            raise ValueError("cab_id does not match equipment key")
        self._pending = bytes(control.data)
        self._received_count += 1

    def read_state(self) -> BtmSnapshot:
        return BtmSnapshot(
            cab_id=self._cab_id,
            pending=self._pending is not None,
            payload_b64=(
                base64.b64encode(self._pending).decode("ascii")
                if self._pending is not None
                else None
            ),
            received_count=self._received_count,
        )

    def reset(self) -> None:
        self._pending = None
        self._received_count = 0

    def emit_intents(self):
        return ()
