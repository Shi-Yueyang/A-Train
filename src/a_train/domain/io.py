"""Named digital-signal definitions and bit-string conversion (§4.7).

Digital signals are represented as bit strings ordered from bit 0 (leftmost) to
bit N (rightmost). Each bit's meaning is configurable per train type or ATP
configuration; the tables in §4.7 are examples only. This module converts
between the wire bit string and named on/off values.

The bit string is exactly ``max_bit + 1`` characters long; a missing tail is
treated as all-zero. A bit string longer than the mapping is still decoded for
the defined bits.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SignalDef:
    """A single named digital signal at a fixed bit position."""

    name: str
    bit: int


@dataclass(frozen=True)
class IoMapping:
    """An ordered set of named signals forming one bit string."""

    signals: tuple[SignalDef, ...]

    def __post_init__(self) -> None:
        seen: set[int] = set()
        names: set[str] = set()
        for s in self.signals:
            if s.bit < 0:
                raise ValueError(f"signal bit must be non-negative: {s!r}")
            if s.bit in seen:
                raise ValueError(f"duplicate signal bit: {s.bit}")
            if s.name in names:
                raise ValueError(f"duplicate signal name: {s.name!r}")
            seen.add(s.bit)
            names.add(s.name)

    @property
    def max_bit(self) -> int:
        return max((s.bit for s in self.signals), default=-1)

    @property
    def width(self) -> int:
        return self.max_bit + 1


# Example configuration from §4.7. These are defaults; real configurations may
# redefine every bit.
DEFAULT_TRAIN_TO_ATP = IoMapping(
    (
        SignalDef("cab_active", 0),
        SignalDef("doors_closed", 1),
        SignalDef("vigilance", 2),
        SignalDef("emergency_handle", 3),
    )
)
DEFAULT_ATP_TO_TRAIN = IoMapping(
    (
        SignalDef("warning", 0),
        SignalDef("service_brake", 1),
        SignalDef("emergency_brake", 2),
    )
)


def to_bit_string(values: Mapping[str, bool], mapping: IoMapping) -> str:
    """Render named values to a bit string (bit 0 leftmost)."""

    width = mapping.width
    chars = ["0"] * width
    for s in mapping.signals:
        if 0 <= s.bit < width:
            chars[s.bit] = "1" if values.get(s.name, False) else "0"
    return "".join(chars)


def from_bit_string(bits: str, mapping: IoMapping) -> dict[str, bool]:
    """Parse a bit string (bit 0 leftmost) into named values."""

    result: dict[str, bool] = {}
    for s in mapping.signals:
        result[s.name] = len(bits) > s.bit and bits[s.bit] == "1"
    return result
