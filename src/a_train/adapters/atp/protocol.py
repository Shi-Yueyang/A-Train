"""NDJSON framing and message helpers (§4.2, §4.3).

The protocol uses TCP + NDJSON (one JSON object per line). TCP provides the
transport; the newline provides application-level message framing. Phase 3.1
implements the framing used by the ``HELLO`` / ``HELLO_ACK`` handshake:
encode a mapping to one line, read one message per line. Per-type payload
validation and the remaining message kinds arrive with Phase 3.2.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one NDJSON message from a stream reader.

    Returns the decoded message, or ``None`` on clean end-of-stream (peer
    closed between messages). Malformed JSON, an oversized line, or a message
    without the mandatory ``type`` field (§4.3) raises ``ValueError`` so the
    caller can treat it as a protocol failure and reconnect.
    """

    try:
        line = await reader.readline()
    except asyncio.LimitOverrunError as exc:
        raise ValueError(f"NDJSON line exceeds the reader limit: {exc}") from exc
    if not line:
        return None
    try:
        message = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"malformed NDJSON line: {line!r}") from exc
    if not isinstance(message, dict) or "type" not in message:
        raise ValueError(f"message without a 'type' field: {message!r}")
    return message


def encode_message(message: Mapping[str, Any]) -> bytes:
    """Serialize one message as an NDJSON line (newline framing included)."""

    return (json.dumps(dict(message)) + "\n").encode("utf-8")
