"""YAML parsing and load-time schema validation (§2.4).

The loader validates event IDs, times, and YAML structure; malformed, unknown,
duplicate, or unhandleable events cause loading to fail with a clear error --
events are never silently skipped. Phase 2 implements parsing and validation;
``SimulationCore.load_scenario()`` validates each event type and payload
against the event-handler registry.
"""

from __future__ import annotations
