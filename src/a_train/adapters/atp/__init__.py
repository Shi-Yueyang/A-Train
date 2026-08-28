"""ATP adapter public exports (§4).

The adapter owns one reconnecting TCP client per configured train cab, encodes
and decodes the NDJSON protocol, and bridges core snapshots and ATP commands.
Phase 4 implements the client, protocol, and manager behaviour; Phase 0 provides
the module boundary and a no-op manager so the application lifecycle runs with
zero configured ATP endpoints.
"""

from __future__ import annotations

from .manager import AtpManager

__all__ = ["AtpManager"]
