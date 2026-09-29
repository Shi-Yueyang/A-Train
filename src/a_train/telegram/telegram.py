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
from dataclasses import dataclass
from typing import Any

from .bits import BitWriter
from .errors import TelegramError
from .fields import reject_unknown
from .packets import Packet41, encode_packet, parse_packet

FRAME_BITS = 830
USER_PACKET_AREA_BITS = 772

EOT_BITS = 8
EOT_VALUE = 0xFF


# Frame header order and widths from the CTCS transponder telegram table
# (Q_UPDOWN 1, M_VERSION 7, Q_MEDIA 1, N_PIG 3, N_TOTAL 3, M_DUP 2,
# M_MCOUNTER 8, NID_C 10, NID_BG 14, Q_LINK 1 = 50 bits). N_PIG and N_TOTAL
# are offset-coded: value 0 means one (first balise / one balise).
@dataclass(frozen=True)
class Telegram:
    """Fully expanded, typed telegram accepted by the BTM encoder."""

    q_updown: int
    m_version: int
    q_media: int
    n_pig: int
    n_total: int
    m_dup: int
    m_mcount: int
    nid_c: int
    nid_bg: int
    q_link: bool
    packets: tuple[Packet41, ...]

    # Example complete packet list:
    # {
    #     "packets": [
    #         {
    #             "packet": 41,
    #             "q_dir": 1,
    #             "l_packet": 40,
    #             "q_scale": 0,
    #             "d_leveltr": 1234,
    #             "m_leveltr": 2,
    #             "nid_stm": null,
    #             "l_ackleveltr": 5,
    #             "n_iter": 0,
    #             "transitions": [],
    #         },
    #         {
    #             "packet": 41,
    #             "q_dir": 1,
    #             "l_packet": 66,
    #             "q_scale": 0,
    #             "d_leveltr": 2000,
    #             "m_leveltr": 1,
    #             "nid_stm": 7,
    #             "l_ackleveltr": 10,
    #             "n_iter": 0,
    #             "transitions": [],
    #         },
    #     ],
    #     "q_updown": 1,
    #     "m_version": 3,
    #     "q_media": 0,
    #     "n_pig": 0,
    #     "n_total": 0,
    #     "m_dup": 0,
    #     "m_mcount": 255,
    #     "nid_c": 83,
    #     "nid_bg": 1000,
    #     "q_link": false,
    # }
    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> Telegram:
        """Validate a complete JSON telegram into typed values."""
        if not isinstance(payload, Mapping):
            raise TelegramError("telegram", "must be an object")
        reject_unknown(
            payload,
            {
                "packets",
                "q_updown",
                "m_version",
                "q_media",
                "n_pig",
                "n_total",
                "m_dup",
                "m_mcount",
                "nid_c",
                "nid_bg",
                "q_link",
            },
            "telegram",
        )
        packets = payload.get("packets")
        if not isinstance(packets, list) or not packets:
            raise TelegramError("telegram.packets", "must be a non-empty list")

        return cls(
            q_updown=cls._coded_int(payload, "q_updown", 1),
            m_version=cls._coded_int(payload, "m_version", 7),
            q_media=cls._coded_int(payload, "q_media", 1),
            n_pig=cls._coded_int(payload, "n_pig", 3),
            n_total=cls._coded_int(payload, "n_total", 3),
            m_dup=cls._coded_int(payload, "m_dup", 2),
            m_mcount=cls._coded_int(payload, "m_mcount", 8),
            nid_c=cls._coded_int(payload, "nid_c", 10),
            nid_bg=cls._coded_int(payload, "nid_bg", 14),
            q_link=cls._boolean(payload, "q_link"),
            packets=tuple(
                parse_packet(packet, f"telegram.packets[{index}]")
                for index, packet in enumerate(packets)
            ),
        )

    @staticmethod
    def _coded_int(payload: Mapping[str, Any], name: str, width: int) -> int:
        if name not in payload:
            raise TelegramError(f"telegram.{name}", "missing required field")
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise TelegramError(f"telegram.{name}", "must be an integer coded value")
        if not 0 <= value < (1 << width):
            raise TelegramError(
                f"telegram.{name}",
                f"value {value} out of range for {width} bits (0-{(1 << width) - 1})",
            )
        return value

    @staticmethod
    def _boolean(payload: Mapping[str, Any], name: str) -> bool:
        if name not in payload:
            raise TelegramError(f"telegram.{name}", "missing required field")
        value = payload[name]
        if not isinstance(value, bool):
            raise TelegramError(f"telegram.{name}", "must be a boolean")
        return value

    def encode_header(self, writer: BitWriter) -> None:
        """Write the typed header fields in their protocol order."""
        writer.write(self.q_updown, 1)
        writer.write(self.m_version, 7)
        writer.write(self.q_media, 1)
        writer.write(self.n_pig, 3)
        writer.write(self.n_total, 3)
        writer.write(self.m_dup, 2)
        writer.write(self.m_mcount, 8)
        writer.write(self.nid_c, 10)
        writer.write(self.nid_bg, 14)
        writer.write(int(self.q_link), 1)


def encode_telegram(payload: Mapping[str, Any]) -> bytes:
    """Encode a telegram object into its 104-byte zero-padded frame.

    Raises :class:`TelegramError` with a JSON field path on invalid input.
    """
    telegram = payload if isinstance(payload, Telegram) else Telegram.parse(payload)

    writer = BitWriter()
    telegram.encode_header(writer)

    for index, packet in enumerate(telegram.packets):
        encode_packet(packet, writer, f"telegram.packets[{index}]")

    writer.write(EOT_VALUE, EOT_BITS)

    if writer.bit_count > FRAME_BITS:
        raise TelegramError(
            "telegram.packets",
            f"telegram needs {writer.bit_count} bits and exceeds the 830-bit frame",
        )

    writer.write(0, FRAME_BITS - writer.bit_count)
    return writer.to_bytes()


__all__ = [
    "Telegram",
    "TelegramError",
    "encode_telegram",
    "FRAME_BITS",
    "USER_PACKET_AREA_BITS",
]
