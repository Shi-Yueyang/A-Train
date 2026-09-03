"""NDJSON framing and protocol message validation (§4.2, §4.3).

The protocol uses TCP + NDJSON (one JSON object per line). TCP provides the
transport; the newline provides application-level message framing. Phase 3.2
adds per-message validation and builders for every wire type: ``HELLO`` /
``HELLO_ACK``, cyclic ``TRAIN_STATE``, ``BTM_RX`` (opaque base64 payload,
§4.5), ``HEARTBEAT`` / ``HEARTBEAT_ACK`` keepalive, ``TRAIN_COMMAND`` (inbound
ATP action request, §4.1), and ``ERROR`` reporting.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from typing import Any


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one NDJSON message from a stream reader.

    Returns the decoded message, or ``None`` on clean end-of-stream (peer
    closed between messages). Malformed JSON, an oversized line, or a message
    without the mandatory ``type`` field (§4.3) raises ``ValueError`` so the
    caller can treat it as a protocol failure.
    """

    try:
        line = await reader.readline()
    except asyncio.LimitOverrunError as exc:
        raise ValueError(f"NDJSON line exceeds the reader limit: {exc}") from exc
    if not line:
        return None
    return decode_line(line)


def decode_line(line: bytes) -> dict[str, Any]:
    """Decode one NDJSON line into a message, raising ValueError when invalid."""

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


# -- Outbound message builders (§4.3-§4.5) -------------------------------------


def make_hello(train_id: str, cab_id: int) -> dict[str, Any]:
    return {"type": "hello", "train_id": train_id, "cab_id": cab_id}


def make_heartbeat(train_id: str, cab_id: int) -> dict[str, Any]:
    return {"type": "heartbeat", "train_id": train_id, "cab_id": cab_id}


def make_heartbeat_ack(train_id: str, cab_id: int) -> dict[str, Any]:
    return {"type": "heartbeat_ack", "train_id": train_id, "cab_id": cab_id}


def make_train_state(train_id: str, cab_id: int, train: Any) -> dict[str, Any]:
    """One ``TRAIN_STATE`` line from a train snapshot (§4.4)."""

    return {
        "type": "train_state",
        "train_id": train_id,
        "cab_id": cab_id,
        "speed": train.speed,
        "acceleration": train.acceleration,
        "position": train.position,
        "direction": train.direction,
    }


def make_btm_rx(payload_b64: str) -> dict[str, Any]:
    """One ``BTM_RX`` line carrying an opaque base64 payload (§4.5)."""

    return {"type": "btm_rx", "data": payload_b64}


def make_error(
    code: str,
    detail: str,
    *,
    train_id: str | None = None,
    cab_id: int | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"type": "error", "code": code, "detail": detail}
    if train_id is not None:
        message["train_id"] = train_id
    if cab_id is not None:
        message["cab_id"] = cab_id
    return message


# -- Inbound validation ---------------------------------------------------------


def parse_train_command(
    message: Mapping[str, Any],
    train_id: str,
    cab_id: int,
) -> tuple[float | None, str | None]:
    """Validate a ``TRAIN_COMMAND`` on a channel bound to (train_id, cab_id).

    Returns ``(drive_demand, door)`` with at most one set. Raises ValueError
    (reported as ``ERROR`` by the caller, §4.3) on identity mismatch or an
    invalid or missing payload.
    """

    msg_train = message.get("train_id")
    if msg_train is not None and msg_train != train_id:
        raise ValueError(f"message train_id {msg_train!r} does not match channel {train_id!r}")
    msg_cab = message.get("cab_id")
    if msg_cab is not None and msg_cab != cab_id:
        raise ValueError(f"message cab_id {msg_cab!r} does not match channel cab {cab_id}")

    drive_demand = message.get("drive_demand")
    if drive_demand is not None:
        if isinstance(drive_demand, bool) or not isinstance(drive_demand, (int, float)):
            raise ValueError(f"drive_demand must be a number, got {drive_demand!r}")
        drive_demand = float(drive_demand)
        if not math.isfinite(drive_demand) or not -1.0 <= drive_demand <= 1.0:
            raise ValueError("drive_demand must be a finite value in [-1.0, 1.0]")

    door = message.get("door")
    if door is not None and door not in ("open", "close"):
        raise ValueError(f"door must be 'open' or 'close', got {door!r}")

    if drive_demand is None and door is None:
        raise ValueError("train_command requires drive_demand or door")
    return drive_demand, door
