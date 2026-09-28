"""MSB-first bit writer used by the telegram encoder."""

from __future__ import annotations


class BitWriter:
    """Accumulate unsigned integer fields most-significant-bit first."""

    def __init__(self) -> None:
        self._value = 0
        self._count = 0

    @property
    def bit_count(self) -> int:
        return self._count

    def write(self, value: int, width: int) -> None:
        if width < 0 or value < 0 or value >= (1 << width):
            raise ValueError(f"value {value} does not fit in {width} bits")
        self._value = (self._value << width) | value
        self._count += width

    def extend(self, other: BitWriter) -> None:
        self.write(other._value, other._count)

    def to_bytes(self) -> bytes:
        """Render the accumulated bits, zero-padded on the right to a byte."""
        byte_len = (self._count + 7) // 8
        padded = self._value << (byte_len * 8 - self._count) if self._count else 0
        return padded.to_bytes(byte_len, "big")
