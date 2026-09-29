"""Declarative field elements for packet tables.

Values are raw coded integers exactly as they appear in the SUBSET-026-8
packet tables (official enums and unit offsets stay with the reader of those
tables). The descriptors provide packet field metadata for validation; packet
codecs perform the actual parsing and encoding.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import TelegramError


def require_int(data: Mapping[str, Any], name: str, path: str, width: int) -> int:
    value = data[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TelegramError(f"{path}.{name}", "must be an integer coded value")
    if not 0 <= value < (1 << width):
        raise TelegramError(
            f"{path}.{name}", f"value {value} out of range for {width} bits (0-{(1 << width) - 1})"
        )
    return value


def reject_unknown(data: Mapping[str, Any], allowed: set[str], path: str) -> None:
    for name in sorted(set(data) - allowed):
        raise TelegramError(f"{path}.{name}", f"unknown field (expected one of: {sorted(allowed)})")


@dataclass(frozen=True)
class Scalar:
    """One fixed-width integer field."""

    name: str
    width: int


@dataclass(frozen=True)
class Conditional:
    """A field present in the bit stream while an earlier field has a value."""

    name: str
    width: int
    depends_on: str
    depends_value: int


@dataclass(frozen=True)
class Group:
    """Metadata for a list of repeated items and its count field."""

    json_key: str
    count_width: int
    item: tuple[Scalar | Conditional, ...]
