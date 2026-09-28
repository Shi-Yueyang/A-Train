"""CTCS balise telegram assembly.

Encodes a JSON-style telegram object into the transponder telegram user-data
bit stream: the 50-bit frame header, the packet sequence in transmission
order inside the 772-bit user packet area, the 8-bit information-end field
(``1111 1111``), and zero padding to the fixed 830-bit frame. The result is
the unshaped user data a BTM delivers to ATP; air-interface channel coding
(scrambling, 10-to-11 symbols, check bits) is not applied here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .bits import BitWriter
from .errors import TelegramError
from .fields import reject_unknown
from .packets import encode_packet

FRAME_BITS = 830
USER_PACKET_AREA_BITS = 772

EOT_BITS = 8
EOT_VALUE = 0xFF

# Frame header order and widths from the CTCS transponder telegram table
# (Q_UPDOWN 1, M_VERSION 7, Q_MEDIA 1, N_PIG 3, N_TOTAL 3, M_DUP 2,
# M_MCOUNTER 8, NID_C 10, NID_BG 14, Q_LINK 1 = 50 bits). All fields are
# caller-settable; the defaults encode an uplink balise telegram. N_PIG and
# N_TOTAL are offset-coded: value 0 means one (first balise / one balise).
_HEADER: tuple[tuple[str, int, Any], ...] = (
    ("q_updown", 1, 1),
    ("m_version", 7, 3),
    ("q_media", 1, 0),
    ("n_pig", 3, 0),
    ("n_total", 3, 0),
    ("m_dup", 2, 0),
    ("m_mcount", 8, 255),
    ("nid_c", 10, 0),
    ("nid_bg", 14, 0),
    ("q_link", 1, False),
)

_ALLOWED = {"packets"} | {name for name, _, _ in _HEADER}


def encode_telegram(payload: Mapping[str, Any]) -> bytes:
    """Encode a telegram object into its 104-byte zero-padded frame.

    Raises :class:`TelegramError` with a JSON field path on invalid input.
    """
    if not isinstance(payload, Mapping):
        raise TelegramError("telegram", "must be an object")
    reject_unknown(payload, _ALLOWED, "telegram")

    packets = payload.get("packets")
    if not isinstance(packets, list) or not packets:
        raise TelegramError("telegram.packets", "must be a non-empty list")

    writer = BitWriter()
    for name, width, fallback in _HEADER:
        value = payload.get(name, fallback)
        if name == "q_link":
            if not isinstance(value, bool):
                raise TelegramError(f"telegram.{name}", "must be a boolean")
            writer.write(int(value), width)
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise TelegramError(f"telegram.{name}", "must be an integer coded value")
        if not 0 <= value < (1 << width):
            raise TelegramError(
                f"telegram.{name}",
                f"value {value} out of range for {width} bits (0-{(1 << width) - 1})",
            )
        writer.write(value, width)

    for index, packet in enumerate(packets):
        encode_packet(packet, writer, f"telegram.packets[{index}]")

    writer.write(EOT_VALUE, EOT_BITS)

    if writer.bit_count > FRAME_BITS:
        raise TelegramError(
            "telegram.packets",
            f"telegram needs {writer.bit_count} bits and exceeds the 830-bit frame",
        )

    writer.write(0, FRAME_BITS - writer.bit_count)
    return writer.to_bytes()


__all__ = ["TelegramError", "encode_telegram", "FRAME_BITS", "USER_PACKET_AREA_BITS"]
