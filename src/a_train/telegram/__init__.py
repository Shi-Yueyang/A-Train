"""CTCS balise telegram encoder: JSON telegram object to frame user data.

A pure, deterministic codec with no simulation, domain, or transport
knowledge. Adapters call :func:`encode_telegram` and hand the resulting bytes
to the opaque BTM equipment path; the simulator never interprets payloads on
the output side (architectural.md §7.3 "BTM is opaque to the simulation").

Supported packet: 41 Level Transition Order. The wire
contract is documented in docs/btm-telegram.md.
"""

from __future__ import annotations

from .errors import TelegramError
from .telegram import FRAME_BITS, USER_PACKET_AREA_BITS, encode_telegram

__all__ = ["TelegramError", "encode_telegram", "FRAME_BITS", "USER_PACKET_AREA_BITS"]
