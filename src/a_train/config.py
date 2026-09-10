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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ATP_ENDPOINTS_ENV = "A_TRAIN_ATP_ENDPOINTS"

_MAX_PORT = 65535


class ConfigError(ValueError):
    """Invalid ATP configuration; reported at startup before the server runs."""


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
