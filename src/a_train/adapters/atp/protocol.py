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


_ATP_EQUIPMENT_TYPES = ("btm", "stcs_atp")


def _serialize_equipment_state(value: object) -> object:
    """Convert frozen equipment snapshot values to JSON-safe Python objects."""

    if dataclasses.is_dataclass(value):
        return {
            field.name: _serialize_equipment_state(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, tuple):
        return [_serialize_equipment_state(item) for item in value]
    if isinstance(value, list):
        return [_serialize_equipment_state(item) for item in value]
    return value


def _is_atp_equipment(entry: Any) -> bool:
    """ATP sees only BTM and STCS ATP equipment (atp-api.md §3)."""

    eq_type = getattr(entry, "type", None)
    return isinstance(eq_type, str) and eq_type.startswith(_ATP_EQUIPMENT_TYPES)


def _atp_equipment_entry(entry: Any) -> dict[str, Any]:
    """One wire entry addressed by ``(type, cab_id)``; internal keys stay home."""

    state = _serialize_equipment_state(entry.state)
    if isinstance(state, dict):
        state.pop("cab_id", None)
        if entry.type.startswith("stcs_atp"):
            # ATP receives only the feedback line; the raw command and named
            # state maps stay in REST/WebSocket (atp-api.md §3.1).
            state = {"train_out_signal": state["train_out_signal"]}
    return {"type": entry.type, "cab_id": entry.cab_id, "state": state}


def make_train_state(train: Any) -> dict[str, Any]:
    """One whole-train ``TRAIN_STATE`` line from a train snapshot (atp-api.md §3.1)."""

    message: dict[str, Any] = {
        "type": "train_state",
        "speed": train.speed,
        "acceleration": train.acceleration,
        "position": train.position,
        "direction": train.direction,
    }
    equipment = [
        _atp_equipment_entry(item)
        for item in getattr(train, "equipment", ())
        if _is_atp_equipment(item)
    ]
    if equipment:
        message["equipment"] = equipment
    return message


def make_error(code: str, detail: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "detail": detail}


# -- Inbound validation ---------------------------------------------------------


def parse_atp_command(
    message: Mapping[str, Any],
) -> tuple[int, float | None, str | None, str | None]:
    """Validate one ``ATP_COMMAND`` line (atp-api.md §4.1).

    Returns ``(cab_id, drive_demand, door, atp_signal)``; ``cab_id`` is
    always set and at least one action is always present. Raises ValueError
    (reported as ``ERROR`` by the caller, atp-api.md §5) on an invalid or
    missing payload.
    """

    if "train_id" in message:
        raise ValueError("train_id is not part of the ATP protocol")

    cab_id = message.get("cab_id")
    if isinstance(cab_id, bool) or not isinstance(cab_id, int) or cab_id < 1:
        raise ValueError(f"cab_id is required and must be a positive integer, got {cab_id!r}")

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
            raise ValueError(
                "atp_signal must contain at least one '0' or '1' "
                f"after removing separators, got {atp_signal!r}"
            )
        if not all(c in "01" for c in normalized):
            raise ValueError(
                f"atp_signal must consist of '0' and '1' characters, got {atp_signal!r}"
            )
        atp_signal = normalized

    if drive_demand is None and door is None and atp_signal is None:
        raise ValueError("atp_command requires drive_demand, door or atp_signal")
    return cab_id, drive_demand, door, atp_signal
