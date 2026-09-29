"""CTCS balise packet tables and packet encoder.

The supported packet is 41, the CTCS level transition order (等级转换
信息包), per the project owner's packet table. Each packet is framed on the
wire as ``NID_PACKET`` (8 bits), then the table fields up to and including
the 13-bit ``L_PACKET`` position, where ``L_PACKET`` carries the number of
packet bits that follow it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .bits import BitWriter
from .errors import TelegramError
from .fields import Conditional, Group, Scalar, reject_unknown, require_int

NID_PACKET_WIDTH = 8
L_PACKET_WIDTH = 13


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


@dataclass(frozen=True)
class Transition:
    """One normalized Packet 41 repeated transition item."""

    m_leveltr: int
    nid_stm: int | None
    l_ackleveltr: int


@dataclass(frozen=True)
class Packet41:
    """Validated, typed Packet 41 data before bit encoding."""

    packet: Literal[41]
    q_dir: int
    l_packet: int
    q_scale: int
    d_leveltr: int
    m_leveltr: int
    nid_stm: int | None
    l_ackleveltr: int
    n_iter: int
    transitions: tuple[Transition, ...]


Packet = Packet41
PacketParser = Callable[[Mapping[str, Any], str], Packet]
PacketEncoder = Callable[[Packet, BitWriter, str], None]


@dataclass(frozen=True)
class PacketCodec:
    parse: PacketParser
    encode: PacketEncoder


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


def _parse_packet41(data: Mapping[str, Any], path: str) -> Packet41:
    """Validate a complete JSON packet into typed Packet 41 data."""
    spec = LEVEL_TRANSITION_ORDER
    reject_unknown(data, {"packet", "l_packet", "n_iter"} | spec.allowed_fields(), path)

    def value(name: str, width: int) -> int:
        if name not in data:
            raise TelegramError(f"{path}.{name}", "missing required field")
        return require_int(data, name, path, width)

    q_dir = value("q_dir", 2)
    l_packet = value("l_packet", L_PACKET_WIDTH)
    q_scale = value("q_scale", 2)
    d_leveltr = value("d_leveltr", 15)
    m_leveltr = value("m_leveltr", 3)
    if "nid_stm" not in data:
        raise TelegramError(f"{path}.nid_stm", "missing required field")
    nid_stm = None
    if m_leveltr == 1:
        nid_stm = require_int(data, "nid_stm", path, 8)
    elif data["nid_stm"] is not None:
        raise TelegramError(f"{path}.nid_stm", "must be null while m_leveltr != 1")
    if "transitions" not in data:
        raise TelegramError(f"{path}.transitions", "missing required field")
    transitions_data = data["transitions"]
    if not isinstance(transitions_data, list):
        raise TelegramError(f"{path}.transitions", "must be a list of items")
    if len(transitions_data) >= (1 << 5):
        raise TelegramError(
            f"{path}.transitions",
            "too many items for 5-bit count (max 31)",
        )
    transitions: list[Transition] = []
    for index, item in enumerate(transitions_data):
        item_path = f"{path}.transitions[{index}]"
        if not isinstance(item, Mapping):
            raise TelegramError(item_path, "must be an object")
        reject_unknown(item, {"m_leveltr", "nid_stm", "l_ackleveltr"}, item_path)
        item_level = value_from(item, "m_leveltr", 3, item_path)
        if item_level == 1 and "nid_stm" not in item:
            raise TelegramError(f"{item_path}.nid_stm", "required while m_leveltr == 1")
        if "nid_stm" not in item:
            raise TelegramError(f"{item_path}.nid_stm", "missing required field")
        if item_level == 1:
            item_nid_stm = value_from(item, "nid_stm", 8, item_path)
        else:
            if item["nid_stm"] is not None:
                raise TelegramError(f"{item_path}.nid_stm", "must be null while m_leveltr != 1")
            item_nid_stm = None
        transitions.append(
            Transition(
                m_leveltr=item_level,
                nid_stm=item_nid_stm,
                l_ackleveltr=value_from(item, "l_ackleveltr", 15, item_path),
            )
        )
    return Packet41(
        packet=41,
        q_dir=q_dir,
        l_packet=l_packet,
        q_scale=q_scale,
        d_leveltr=d_leveltr,
        m_leveltr=m_leveltr,
        nid_stm=nid_stm,
        l_ackleveltr=value("l_ackleveltr", 15),
        n_iter=value("n_iter", 5),
        transitions=tuple(transitions),
    )


def parse_packet(data: Mapping[str, Any], path: str) -> Packet:
    """Validate a complete JSON packet through its registered codec."""
    if not isinstance(data, Mapping):
        raise TelegramError(path, "packet must be an object")
    number = data.get("packet")
    if isinstance(number, bool) or not isinstance(number, int):
        raise TelegramError(f"{path}.packet", "must be an integer packet number")
    codec = PACKET_CODECS.get(number)
    if codec is None:
        supported = sorted(PACKET_CODECS)
        raise TelegramError(
            f"{path}.packet", f"unknown packet number {number} (supported: {supported})"
        )
    return codec.parse(data, path)


def value_from(data: Mapping[str, Any], name: str, width: int, path: str) -> int:
    if name not in data:
        raise TelegramError(f"{path}.{name}", "missing required field")
    return require_int(data, name, path, width)


def _encode_packet41(packet: Packet41, writer: BitWriter, path: str) -> None:
    """Encode typed Packet 41 data."""
    writer.write(packet.packet, NID_PACKET_WIDTH)
    writer.write(packet.q_dir, 2)
    writer.write(packet.l_packet, L_PACKET_WIDTH)
    writer.write(packet.q_scale, 2)
    writer.write(packet.d_leveltr, 15)
    writer.write(packet.m_leveltr, 3)
    if packet.m_leveltr == 1:
        writer.write(packet.nid_stm, 8)
    writer.write(packet.l_ackleveltr, 15)
    writer.write(packet.n_iter, 5)
    for item in packet.transitions:
        writer.write(item.m_leveltr, 3)
        if item.m_leveltr == 1:
            writer.write(item.nid_stm, 8)
        writer.write(item.l_ackleveltr, 15)


PACKET_CODECS: dict[int, PacketCodec] = {
    LEVEL_TRANSITION_ORDER.number: PacketCodec(
        parse=_parse_packet41,
        encode=_encode_packet41,
    ),
}


def encode_packet(packet: Packet, writer: BitWriter, path: str) -> None:
    """Encode a validated typed packet."""
    codec = PACKET_CODECS.get(packet.packet)
    if codec is None:
        supported = sorted(PACKET_CODECS)
        raise TelegramError(
            f"{path}.packet", f"unknown packet number {packet.packet} (supported: {supported})"
        )
    codec.encode(packet, writer, path)
