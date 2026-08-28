"""Scheduled-event heap and event-handler registry (§2.4, §2.6).

Phase 2 implements the stable ``(at, sequence, event)`` heap, load-time event
payload validation against a handler registry, and the split-step / retry
semantics for failed handlers.
"""

from __future__ import annotations
