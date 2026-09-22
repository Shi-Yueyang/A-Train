"""ATP endpoint configuration for the ``run`` command (Phase 3.1, atp-api.md §1.1, §6).

The ``run`` command accepts ATP endpoints as ``--atp CAB_ID=HOST:PORT``
specifications and/or a JSON ``--atp-config`` file::

    { "atp_endpoints": [ { "cab_id": 1,
                           "host": "127.0.0.1", "port": 9101 } ] }

Resolved endpoints are handed to the server through the
``A_TRAIN_ATP_ENDPOINTS`` environment variable (a JSON list). The env-var
passthrough keeps uvicorn's factory import-string startup intact, and reload
subprocesses inherit the same configuration. ``bootstrap.create_app`` decodes
the variable when no endpoints are passed explicitly.

Endpoint dictionaries are plain transport-neutral data: ``cab_id`` (positive
int), ``host`` (non-empty str), ``port`` (1-65535). The single train identity
is supplied by the simulator, not repeated in each endpoint.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ATP_ENDPOINTS_ENV = "A_TRAIN_ATP_ENDPOINTS"
TRAIN_CONFIG_ENV = "A_TRAIN_CONFIG"

_MAX_PORT = 65535


class ConfigError(ValueError):
    """Invalid ATP configuration; reported at startup before the server runs."""


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


def validate_endpoint(entry: Mapping[str, Any], where: str = "atp endpoint") -> dict[str, Any]:
    """Normalise one endpoint mapping, raising ConfigError on invalid fields."""

    if not isinstance(entry, Mapping):
        raise ConfigError(f"{where}: expected an object, got {entry!r}")
    return {
        "cab_id": _require_int(entry.get("cab_id"), "cab_id", where, 1, 10_000),
        "host": _require_str(entry.get("host"), "host", where),
        "port": _require_int(entry.get("port"), "port", where, 1, _MAX_PORT),
    }


def parse_endpoint_spec(spec: str) -> dict[str, Any]:
    """Parse one ``--atp`` value: ``1=127.0.0.1:9001``.

    The host may be bracketed IPv6, e.g. ``2=[::1]:9102``.
    """

    ident, sep, address = spec.partition("=")
    if not sep:
        raise ConfigError(f"--atp {spec!r}: expected CAB_ID=HOST:PORT")
    cab_raw = ident
    try:
        cab_id = int(cab_raw)
    except ValueError:
        raise ConfigError(f"--atp {spec!r}: cab id {cab_raw!r} is not an integer") from None
    host, sep, port_raw = address.rpartition(":")
    if not sep or not port_raw:
        raise ConfigError(f"--atp {spec!r}: address {address!r} is missing HOST:PORT")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        port = int(port_raw)
    except ValueError:
        raise ConfigError(f"--atp {spec!r}: port {port_raw!r} is not an integer") from None
    return validate_endpoint(
        {"cab_id": cab_id, "host": host, "port": port},
        where=f"--atp {spec!r}",
    )


def load_endpoints_file(path: str | Path) -> list[dict[str, Any]]:
    """Load endpoints from a JSON file: ``{"atp_endpoints": [...]}`` or a list."""

    try:
        raw = Path(path).read_text("utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read ATP config file {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"ATP config file {path} is not valid JSON: {exc}") from None
    if isinstance(data, Mapping):
        entries = data.get("atp_endpoints")
    else:
        entries = data
    if not isinstance(entries, list):
        raise ConfigError(f"ATP config file {path}: expected an 'atp_endpoints' list")
    return [
        validate_endpoint(entry, where=f"{path} atp_endpoints[{i}]")
        for i, entry in enumerate(entries)
    ]


def decode_env(value: str) -> list[dict[str, Any]]:
    """Decode the ``A_TRAIN_ATP_ENDPOINTS`` JSON list; empty/missing means []."""

    if not value or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{ATP_ENDPOINTS_ENV} is not valid JSON: {exc}") from None
    if not isinstance(parsed, list):
        raise ConfigError(f"{ATP_ENDPOINTS_ENV}: expected a JSON list, got {type(parsed).__name__}")
    return [
        validate_endpoint(entry, where=f"{ATP_ENDPOINTS_ENV}[{i}]")
        for i, entry in enumerate(parsed)
    ]


def encode_env(endpoints: Sequence[Mapping[str, Any]]) -> str:
    """Serialise validated endpoints for the environment variable."""

    return json.dumps([dict(entry) for entry in endpoints])


def resolve_endpoints(
    config_file: str | Path | None = None,
    specs: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Merge ``--atp-config`` entries and ``--atp`` specs, rejecting duplicates."""

    endpoints: list[dict[str, Any]] = []
    if config_file is not None:
        endpoints.extend(load_endpoints_file(config_file))
    for spec in specs:
        endpoints.append(parse_endpoint_spec(spec))
    seen: set[int] = set()
    for endpoint in endpoints:
        key = endpoint["cab_id"]
        if key in seen:
            raise ConfigError(f"duplicate ATP endpoint for cab {key}")
        seen.add(key)
    return endpoints


def train_config_from_data(data: Mapping[str, Any]):
    """Build one domain ``TrainConfig`` from the declarative JSON shape."""

    from .domain.train import EquipmentConfig, TrainConfig

    root = _require_mapping(data, "train config")
    if "train" in root:
        root = _require_mapping(root["train"], "train config.train")

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
        for index, raw_equipment in enumerate(equipment_data):
            where = f"train config.equipment[{index}]"
            item = _require_mapping(raw_equipment, where)
            eq_type = _require_str(item.get("type"), "type", where)
            key = _require_str(item.get("key"), "key", where)
            params = item.get("params", {})
            if not isinstance(params, Mapping):
                raise ConfigError(f"{where}: 'params' must be an object")
            params = dict(params)
            if "cab_id" in item:
                cab_id = _require_int(item["cab_id"], "cab_id", where, 1, 10_000)
                if cab_id not in cab_ids:
                    raise ConfigError(f"{where}: cab_id {cab_id} is not configured")
                if "cab_id" in params and params["cab_id"] != cab_id:
                    raise ConfigError(f"{where}: cab_id conflicts with params.cab_id")
                params["cab_id"] = cab_id
            equipment_configs.append(EquipmentConfig(eq_type, key, params))

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


def load_train_config_file(path: str | Path):
    """Load and validate one train configuration JSON file."""

    try:
        raw = Path(path).read_text("utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read train config file {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"train config file {path} is not valid JSON: {exc}") from None
    try:
        return train_config_from_data(data)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError(f"train config file {path}: {exc}") from None


def train_config_to_data(config) -> dict[str, Any]:
    """Serialize a domain train config for the uvicorn factory handoff."""

    equipment = []
    for item in config.equipment_configs:
        params = dict(item.params)
        entry: dict[str, Any] = {"type": item.type, "key": item.key}
        if "cab_id" in params:
            entry["cab_id"] = params.pop("cab_id")
        if params:
            entry["params"] = params
        equipment.append(entry)
    return {
        "train": {
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
    }


def encode_train_config(config) -> str:
    return json.dumps(train_config_to_data(config))


def decode_train_config(value: str):
    if not value or not value.strip():
        raise ConfigError(f"{TRAIN_CONFIG_ENV}: value is empty")
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{TRAIN_CONFIG_ENV} is not valid JSON: {exc}") from None
    return train_config_from_data(data)
