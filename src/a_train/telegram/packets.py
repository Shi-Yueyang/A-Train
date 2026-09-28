"""CTCS balise packet tables and packet encoder.

The supported packet is 41, the CTCS level transition order (等级转换
信息包), per the project owner's packet table. Each packet is framed on the
wire as ``NID_PACKET`` (8 bits), then the table fields up to and including
the 13-bit ``L_PACKET`` position, where ``L_PACKET`` carries the number of
packet bits that follow it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .bits import BitWriter
from .errors import TelegramError
from .fields import Conditional, Group, Scalar, reject_unknown

NID_PACKET_WIDTH = 8
L_PACKET_WIDTH = 13
_MAX_L_PACKET = (1 << L_PACKET_WIDTH) - 1


@dataclass(frozen=True)
class PacketSpec:
    number: int
    before_l_packet: tuple[Scalar | Conditional, ...]
    after_l_packet: tuple[Scalar | Conditional, ...]
    group: Group

    def allowed_fields(self) -> set[str]:
        names = {element.name for element in self.before_l_packet}
        names |= {element.name for element in self.after_l_packet}
        names |= {self.group.json_key}
        return names


# Packet 41 - level transition order. NID_STM (non-ETCS level, e.g. CTCS
# 0/1/2) is transmitted only when the accompanying M_LEVELTR is STM (coded 1).
LEVEL_TRANSITION_ORDER = PacketSpec(
    number=41,
    before_l_packet=(Scalar("q_dir", 2),),
    after_l_packet=(
        Scalar("q_scale", 2),
        Scalar("d_leveltr", 15),
        Scalar("m_leveltr", 3),
        Conditional("nid_stm", 8, depends_on="m_leveltr", depends_value=1),
        Scalar("l_ackleveltr", 15),
    ),
    group=Group(
        json_key="transitions",
        count_width=5,
        item=(
            Scalar("m_leveltr", 3),
            Conditional("nid_stm", 8, depends_on="m_leveltr", depends_value=1),
            Scalar("l_ackleveltr", 15),
        ),
    ),
)

PACKET_SPECS: dict[int, PacketSpec] = {
    LEVEL_TRANSITION_ORDER.number: LEVEL_TRANSITION_ORDER,
}


def encode_packet(data: Mapping[str, Any], writer: BitWriter, path: str) -> None:
    """Encode one packet object; it must carry a supported ``packet`` number."""
    if not isinstance(data, Mapping):
        raise TelegramError(path, "packet must be an object")
    number = data.get("packet")
    if isinstance(number, bool) or not isinstance(number, int):
        raise TelegramError(f"{path}.packet", "must be an integer packet number")
    spec = PACKET_SPECS.get(number)
    if spec is None:
        supported = sorted(PACKET_SPECS)
        raise TelegramError(
            f"{path}.packet", f"unknown packet number {number} (supported: {supported})"
        )

    reject_unknown(data, {"packet"} | spec.allowed_fields(), path)

    head = BitWriter()
    written: dict[str, int] = {}
    for element in spec.before_l_packet:
        element.encode(data, path, written, head)

    body = BitWriter()
    for element in spec.after_l_packet:
        element.encode(data, path, written, body)
    spec.group.encode(data, path, body)

    if body.bit_count > _MAX_L_PACKET:
        raise TelegramError(
            path, f"packet body of {body.bit_count} bits exceeds the 13-bit L_PACKET range"
        )

    writer.write(spec.number, NID_PACKET_WIDTH)
    writer.extend(head)
    writer.write(body.bit_count, L_PACKET_WIDTH)
    writer.extend(body)
