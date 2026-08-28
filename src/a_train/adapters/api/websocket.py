"""Snapshot-to-browser WebSocket publisher task (§5.3).

A publisher task consumes the core's bounded snapshot queue and pushes state to
connected browsers. It never performs network I/O inside ``run_loop()`` so a
slow client cannot delay physics. Phase 5 implements the publisher; Phase 0
provides the module boundary.
"""

from __future__ import annotations
