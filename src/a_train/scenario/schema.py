"""Frozen scenario and event data models (§2.4, §6.2).

A scenario contains an initial world state and an ordered list of predefined
events. Each event has a unique identifier, a scheduled simulation time, and a
payload describing an action. Phase 2 implements these immutable types and the
load-time schema validation performed by ``loader.py``.
"""

from __future__ import annotations
