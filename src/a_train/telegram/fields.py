"""Declarative field elements for packet tables.

Values are raw coded integers exactly as they appear in the SUBSET-026-8
packet tables (official enums and unit offsets stay with the reader of those
tables); the encoder validates bit widths and framing, not semantics.

Every element records the value it consumed into the shared ``written``
mapping so later ``Conditional`` elements can see earlier field values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .bits import BitWriter
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

    def encode(
        self, data: Mapping[str, Any], path: str, written: dict[str, int], writer: BitWriter
    ) -> None:
        if self.name not in data:
            raise TelegramError(f"{path}.{self.name}", "missing required field")
        value = require_int(data, self.name, path, self.width)
        writer.write(value, self.width)
        written[self.name] = value


@dataclass(frozen=True)
class Conditional:
    """A field present in the bit stream only while an earlier field holds a value.

    When the condition is false the field is ignored if provided.
    """

    name: str
    width: int
    depends_on: str
    depends_value: int

    def encode(
        self, data: Mapping[str, Any], path: str, written: dict[str, int], writer: BitWriter
    ) -> None:
        if written.get(self.depends_on) != self.depends_value:
            return
        if self.name not in data:
            raise TelegramError(
                f"{path}.{self.name}",
                f"required while {self.depends_on} == {self.depends_value}",
            )
        value = require_int(data, self.name, path, self.width)
        writer.write(value, self.width)
        written[self.name] = value


@dataclass(frozen=True)
class Group:
    """A list of repeated items written after their 5-bit ``N_ITER`` count.

    The JSON list lives under ``json_key``; the count is derived from the
    list length and never supplied by the caller.
    """

    json_key: str
    count_width: int
    item: tuple[Scalar | Conditional, ...]

    def allowed_fields(self) -> set[str]:
        return {element.name for element in self.item}

    def encode(self, data: Mapping[str, Any], path: str, writer: BitWriter) -> None:
        items = data.get(self.json_key, [])
        item_path = f"{path}.{self.json_key}"
        if not isinstance(items, list):
            raise TelegramError(item_path, "must be a list of items")
        allowed = self.allowed_fields()
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise TelegramError(f"{item_path}[{index}]", "must be an object")
            reject_unknown(item, allowed, f"{item_path}[{index}]")
        if len(items) >= (1 << self.count_width):
            raise TelegramError(
                item_path,
                f"too many items for {self.count_width}-bit count (max {2**self.count_width - 1})",
            )
        writer.write(len(items), self.count_width)
        for index, item in enumerate(items):
            written: dict[str, int] = {}
            for element in self.item:
                element.encode(item, f"{item_path}[{index}]", written, writer)
