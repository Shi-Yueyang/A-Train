"""Named digital-signal definitions and bit-string conversion (§4.7).

Digital signals are represented as bit strings ordered from bit 0 (leftmost)
to bit N (rightmost). Each bit's meaning is configurable per train type or ATP
configuration; the tables in §4.7 are examples only. Phase 3 implements the
bit-string <-> named-signal conversion.
"""

from __future__ import annotations
