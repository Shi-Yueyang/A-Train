"""I/O-boundary adapters that translate external data into core commands.

Adapters never advance simulation time or mutate world state. They enqueue
``Command`` objects onto the core's command queue and publish read-only
``SimulationSnapshot`` objects produced by the core (§1.2, §2.5).
"""

from __future__ import annotations
