"""One accepted ATP TCP connection carrying NDJSON messages."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from ...simulation.snapshots import SimulationSnapshot
from .protocol import encode_message, make_train_state


class AtpConnection:
    """An accepted ATP peer; the simulator owns the listening socket."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        peer = writer.get_extra_info("peername")
        self.host = str(peer[0]) if peer else "unknown"
        self.port = int(peer[1]) if peer else 0

    @property
    def ready(self) -> bool:
        return not self._writer.is_closing()

    @property
    def ident(self) -> str:
        return f"ATP {self.host}:{self.port}"

    def send_message(self, message: Mapping[str, Any]) -> bool:
        if not self.ready:
            return False
        try:
            self._writer.write(encode_message(message))
        except (ConnectionError, OSError, RuntimeError):
            self._writer.close()
            return False
        return True

    def publish(self, snapshot: SimulationSnapshot) -> None:
        if snapshot.trains:
            self.send_message(make_train_state(snapshot.trains[0]))

    def close(self) -> None:
        self._writer.close()