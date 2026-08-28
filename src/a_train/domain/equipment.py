"""Doors, cabs, BTM equipment, and train-local I/O behaviour (§3, §4.6).

The simulator simulates the BTM equipment and delivers opaque byte arrays to
ATP; it never interprets BTM payloads. Phase 3 implements this module.
"""

from __future__ import annotations
