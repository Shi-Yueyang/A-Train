"""ATP adapter public exports (§4).

The adapter owns one reconnecting TCP client per configured train cab, encodes
and decodes the NDJSON protocol, and bridges core snapshots and ATP commands.
Phase 3.1 implements the channel: connect/retry/reconnect and the
``HELLO`` / ``HELLO_ACK`` handshake; Phase 3.2 adds message validation and
content publishing.
"""

from __future__ import annotations

from .client import AtpClient, ClientState
from .manager import AtpEndpoint, AtpManager

__all__ = ["AtpClient", "AtpEndpoint", "AtpManager", "ClientState"]
