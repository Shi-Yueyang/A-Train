"""Startup configuration for the declarative train and ATP listeners.

The whole startup configuration lives in the single ``--train-config`` JSON
file: a ``train`` object describing the train, cabs, physics, and equipment,
plus an optional ``atp`` array of local listener addresses. The parsed
configuration is handed to the server through the ``A_TRAIN_CONFIG``
environment variable so the uvicorn factory and reload subprocesses receive
the same values.

Listener dictionaries are plain transport-neutral data: ``host``
(non-empty str) and ``port`` (1-65535). ATP connections carry no cab binding;
the train identity and cab targets live in the protocol messages.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

TRAIN_CONFIG_ENV = "A_TRAIN_CONFIG"

_MAX_PORT = 65535


class ConfigError(ValueError):
    """Invalid startup configuration; reported before the server runs."""


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{where}: expected an object, got {value!r}")
    return value


def _require_number(value: Any, field: str, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConfigError(f"{where}: {field!r} must be a finite number, got {value!r}")
    return float(value)


def _require_str(value: Any, field: str, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: {field!r} must be a non-empty string, got {value!r}")
    return value


def _require_int(value: Any, field: str, where: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: {field!r} must be an integer, got {value!r}")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{where}: {field!r} must be in [{minimum}, {maximum}], got {value}")
    return value


def _generated_equipment_key(equipment_type: str, params: Mapping[str, Any], used: set[str]) -> str:
    if "cab_id" in params:
        base = f"{equipment_type}_{params['cab_id']}"
    elif equipment_type == "door" and "side" in params:
        base = f"{params['side']}_door"
    else:
        base = equipment_type
    key = base
    suffix = 2
    while key in used:
        key = f"{base}_{suffix}"
        suffix += 1
    used.add(key)
    return key


def validate_endpoint(entry: Mapping[str, Any], where: str = "atp listener") -> dict[str, Any]:
    """Normalise one listener mapping, raising ConfigError on invalid fields."""

    if not isinstance(entry, Mapping):
        raise ConfigError(f"{where}: expected an object, got {entry!r}")
    unknown = set(entry) - {"host", "port"}
    if unknown:
        raise ConfigError(f"{where}: unknown field(s) {sorted(unknown)}")
    return {
        "host": _require_str(entry.get("host"), "host", where),
        "port": _require_int(entry.get("port"), "port", where, 1, _MAX_PORT),
    }


def endpoints_from_data(data: Any, where: str = "atp listeners") -> list[dict[str, Any]]:
    """Validate the ``atp`` array: one local ``{host, port}`` listener per entry."""

    if data is None:
        return []
    if not isinstance(data, list):
        raise ConfigError(f"{where}: expected a list, got {type(data).__name__}")
    return [validate_endpoint(entry, where=f"{where}[{i}]") for i, entry in enumerate(data)]


def train_config_from_data(data: Mapping[str, Any]):
    """Build one domain ``TrainConfig`` from the declarative JSON shape."""

    from .domain.train import EquipmentConfig, TrainConfig

    root = _require_mapping(data, "train config")
    if "train" in root:
        root = _require_mapping(root["train"], "train config.train")
    unknown = set(root) - {"train_id", "cabs", "physics", "equipment"}
    if unknown:
        raise ConfigError(
            f"train config: unknown key(s) {sorted(unknown)}; ATP "
            "endpoints belong in the top-level 'atp' array, not in 'train'"
            if "atp" in unknown
            else f"train config: unknown key(s) {sorted(unknown)}"
        )

    train_id = _require_str(root.get("train_id"), "train_id", "train config")
    cabs = root.get("cabs")
    if not isinstance(cabs, list) or not cabs:
        raise ConfigError("train config: 'cabs' must be a non-empty list")
    cab_ids: list[int] = []
    facings: dict[int, int] = {}
    active_cabs: list[int] = []
    for index, raw_cab in enumerate(cabs):
        where = f"train config.cabs[{index}]"
        cab = _require_mapping(raw_cab, where)
        cab_id = _require_int(cab.get("cab_id"), "cab_id", where, 1, 10_000)
        if cab_id in cab_ids:
            raise ConfigError(f"{where}: duplicate cab_id {cab_id}")
        facing = cab.get("facing", "forward")
        if facing not in ("forward", "backward"):
            raise ConfigError(f"{where}: 'facing' must be 'forward' or 'backward'")
        cab_ids.append(cab_id)
        facings[cab_id] = 1 if facing == "forward" else -1
        if cab.get("active", False):
            active_cabs.append(cab_id)
    if len(active_cabs) != 1:
        raise ConfigError("train config.cabs: exactly one cab must have 'active': true")

    physics = _require_mapping(root.get("physics"), "train config.physics")
    equipment_data = root.get("equipment")
    equipment_configs: list[EquipmentConfig] | None = None
    if equipment_data is not None:
        if not isinstance(equipment_data, list):
            raise ConfigError("train config.equipment: expected a list")
        equipment_configs = []
        generated_keys: set[str] = set()
        for index, raw_equipment in enumerate(equipment_data):
            where = f"train config.equipment[{index}]"
            item = _require_mapping(raw_equipment, where)
            eq_type = _require_str(item.get("type"), "type", where)
            if "key" in item:
                raise ConfigError(f"{where}: 'key' is generated and must not be provided")
            enabled = item.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ConfigError(f"{where}: 'enabled' must be a boolean")
            params = item.get("params", {})
            if not isinstance(params, Mapping):
                raise ConfigError(f"{where}: 'params' must be an object")
            params = dict(params)
            if "side" in item:
                if "side" in params and params["side"] != item["side"]:
                    raise ConfigError(f"{where}: side conflicts with params.side")
                params["side"] = item["side"]
            if "cab_id" in item:
                cab_id = _require_int(item["cab_id"], "cab_id", where, 1, 10_000)
                if cab_id not in cab_ids:
                    raise ConfigError(f"{where}: cab_id {cab_id} is not configured")
                if "cab_id" in params and params["cab_id"] != cab_id:
                    raise ConfigError(f"{where}: cab_id conflicts with params.cab_id")
                params["cab_id"] = cab_id
            key = _generated_equipment_key(eq_type, params, generated_keys)
            equipment_configs.append(EquipmentConfig(eq_type, key, params, enabled=enabled))

        cab_addresses: set[tuple[str, int]] = set()
        for item in equipment_configs:
            cab_id = item.params.get("cab_id")
            if not item.enabled or not isinstance(cab_id, int):
                continue
            address = (item.type, cab_id)
            if address in cab_addresses:
                raise ConfigError(
                    f"train config.equipment: duplicate '{item.type}' for cab {cab_id}"
                )
            cab_addresses.add(address)

    return TrainConfig(
        train_id=train_id,
        cab_ids=tuple(cab_ids),
        initial_active_cab=active_cabs[0],
        max_traction_accel=_require_number(
            physics.get("max_traction_accel"), "max_traction_accel", "train config.physics"
        ),
        max_decel=_require_number(physics.get("max_decel"), "max_decel", "train config.physics"),
        initial_position=_require_number(
            physics.get("initial_position", 0.0), "initial_position", "train config.physics"
        ),
        initial_speed=_require_number(
            physics.get("initial_speed", 0.0), "initial_speed", "train config.physics"
        ),
        initial_door_state=physics.get("initial_door_state", "closed"),
        cab_facings=facings,
        equipment_configs=(None if equipment_configs is None else tuple(equipment_configs)),
    )


def startup_config_from_data(
    data: Mapping[str, Any],
) -> tuple[Any | None, list[dict[str, Any]]]:
    """Parse the full startup file shape: an optional train plus ATP endpoints.

    Accepts the wrapped ``{"train": {...}, "atp": [...]}`` form, a bare train
    object, or an ``{"atp": [...]}``-only mapping (training is then ``None``).
    """

    root = _require_mapping(data, "startup config")
    train = None if ("atp" in root and "train" not in root) else train_config_from_data(root)
    endpoints = endpoints_from_data(root.get("atp"), where="startup config.atp")
    return train, endpoints


def load_config_file(path: str | Path):
    """Load and validate one startup configuration JSON file.

    Returns ``(train_config, atp_endpoints)``.
    """

    try:
        raw = Path(path).read_text("utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read train config file {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"train config file {path} is not valid JSON: {exc}") from None
    try:
        train, endpoints = startup_config_from_data(data)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"train config file {path}: {exc}") from None
    if train is None:
        raise ConfigError(f"train config file {path}: 'train' section is required")
    return train, endpoints


def train_config_to_data(config) -> dict[str, Any]:
    """Serialize a domain train config into the startup file's ``train`` object."""

    equipment = []
    for item in config.equipment_configs:
        params = dict(item.params)
        entry: dict[str, Any] = {"type": item.type}
        if not item.enabled:
            entry["enabled"] = False
        if "cab_id" in params:
            entry["cab_id"] = params.pop("cab_id")
        if params:
            entry["params"] = params
        equipment.append(entry)
    return {
        "train_id": config.train_id,
        "cabs": [
            {
                "cab_id": cab_id,
                "facing": "forward" if config.cab_facings[cab_id] == 1 else "backward",
                "active": cab_id == config.initial_active_cab,
            }
            for cab_id in config.cab_ids
        ],
        "physics": {
            "initial_position": config.initial_position,
            "initial_speed": config.initial_speed,
            "max_traction_accel": config.max_traction_accel,
            "max_decel": config.max_decel,
            "initial_door_state": config.initial_door_state,
        },
        "equipment": equipment,
    }


def config_to_data(
    train_config,
    atp_endpoints: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Serialize parsed startup configuration into the file/env JSON shape."""

    data: dict[str, Any] = {}
    if train_config is not None:
        data["train"] = train_config_to_data(train_config)
    if atp_endpoints:
        data["atp"] = [dict(entry) for entry in atp_endpoints]
    return data


def encode_config(
    train_config,
    atp_endpoints: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Serialize validated startup configuration for the environment handoff."""

    return json.dumps(config_to_data(train_config, atp_endpoints))


def decode_config(value: str):
    """Decode the ``A_TRAIN_CONFIG`` environment value.

    Returns ``(train_config | None, atp_endpoints)``; either part may be
    absent so explicit constructor arguments can override just one of them.
    """

    if not value or not value.strip():
        raise ConfigError(f"{TRAIN_CONFIG_ENV}: value is empty")
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{TRAIN_CONFIG_ENV} is not valid JSON: {exc}") from None
    return startup_config_from_data(data)
