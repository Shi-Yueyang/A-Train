"""NDJSON framing and protocol message validation (atp-api.md §1.2, §5).

The protocol uses TCP + NDJSON (one JSON object per line). TCP provides the
transport; the newline provides application-level message framing. There is
no handshake message: the channel is live the moment TCP opens. Builders and
validators cover cyclic ``TRAIN_STATE`` (atp-api.md §3.1), inbound
``ATP_COMMAND`` (atp-api.md §4.1), and ``ERROR`` reporting (§5).
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping
from typing import Any


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


# -- Outbound message builders (atp-api.md §3, §5) -------------------------------


def _serialize_equipment_state(value: object) -> object:
    """Convert frozen equipment snapshot values to JSON-safe Python objects."""

    if dataclasses.is_dataclass(value):
        return {field.name: _serialize_equipment_state(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, tuple):
        return [_serialize_equipment_state(item) for item in value]
    if isinstance(value, list):
        return [_serialize_equipment_state(item) for item in value]
    return value


def make_train_state(train_id: str, cab_id: int, train: Any) -> dict[str, Any]:
    """One ``TRAIN_STATE`` line from a train snapshot (atp-api.md §3.1)."""

    message: dict[str, Any] = {
        "type": "train_state",
        "train_id": train_id,
        "cab_id": cab_id,
        "speed": train.speed,
        "acceleration": train.acceleration,
        "position": train.position,
        "direction": train.direction,
    }
    equipment = getattr(train, "equipment", ())
    if equipment:
        message["equipment"] = [_serialize_equipment_state(value) for value in equipment]
    return message


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


def parse_atp_command(
    message: Mapping[str, Any],
    train_id: str,
    cab_id: int,
) -> tuple[float | None, str | None, str | None]:
    """Validate a ``ATP_COMMAND`` on a channel bound to (train_id, cab_id).

    Returns ``(drive_demand, door, atp_signal)``; at least one is always set.
    Raises ValueError (reported as ``ERROR`` by the caller, atp-api.md §5) on
    identity mismatch or an invalid or missing payload.
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

    atp_signal = message.get("atp_signal")
    if atp_signal is not None:
        if not isinstance(atp_signal, str) or not atp_signal:
            raise ValueError(f"atp_signal must be a non-empty string, got {atp_signal!r}")
        normalized = atp_signal.replace("_", "")
        if not normalized:
            raise ValueError(f"atp_signal must contain at least one '0' or '1' after removing separators, got {atp_signal!r}")
        if not all(c in "01" for c in normalized):
            raise ValueError(
                f"atp_signal must consist of '0' and '1' characters, got {atp_signal!r}"
            )
        atp_signal = normalized

    if drive_demand is None and door is None and atp_signal is None:
        raise ValueError("atp_command requires drive_demand, door or atp_signal")
    return drive_demand, door, atp_signal
