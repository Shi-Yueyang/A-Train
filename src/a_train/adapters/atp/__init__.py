"""ATP adapter public exports (docs/atp-api.md).

The adapter owns one reconnecting TCP client per configured train cab, frames
and validates the NDJSON protocol, publishes ``TRAIN_STATE`` content from core
snapshots, and converts inbound ``ATP_COMMAND`` messages into core commands.
"""

from __future__ import annotations

from .client import AtpClient, ClientState
from .manager import AtpEndpoint, AtpManager

__all__ = ["AtpClient", "AtpEndpoint", "AtpManager", "ClientState"]
