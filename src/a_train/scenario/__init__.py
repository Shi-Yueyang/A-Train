"""Public scenario-loading API.

Re-exports the frozen scenario data models and the YAML loader. The loader
parses and validates scenario structure, event IDs, times, and registered event
payloads at load time (§2.4). Phase 2 implements validation; Phase 0 provides
the module boundary only.
"""

from __future__ import annotations
