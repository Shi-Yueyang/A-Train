"""Command-line entry point: ``python -m a_train``.

Exposes the ``run`` subcommand, which starts the simulator HTTP/WebSocket
server (headless). The web UI is an optional client.
"""

from __future__ import annotations

import argparse
import os
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="a-train",
        description="Deterministic, headless train simulator with ATP integration.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_p = subparsers.add_parser(
        "run",
        help="Start the simulator HTTP/WebSocket server.",
        description=(
            "Start the simulator HTTP/WebSocket server. The server runs "
            "headlessly; the web UI is an optional client."
        ),
    )
    run_p.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    run_p.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000).")
    run_p.add_argument(
        "--reload", action="store_true", help="Enable uvicorn auto-reload (development)."
    )
    run_p.add_argument(
        "--atp",
        action="append",
        default=[],
        metavar="TRAIN_ID:CAB_ID=HOST:PORT",
        help=(
            "Connect to an external ATP process; repeatable, one per cab, "
            "e.g. --atp TRAIN001:1=127.0.0.1:9101"
        ),
    )
    run_p.add_argument(
        "--atp-config",
        metavar="FILE",
        help='JSON file: {"atp_endpoints": [{"train_id", "cab_id", "host", "port"}, ...]}',
    )
    run_p.add_argument(
        "--atp-heartbeat",
        type=float,
        default=None,
        metavar="SECONDS",
        help="HEARTBEAT keepalive interval per ATP connection (default: disabled).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _run_server(
            host=args.host,
            port=args.port,
            reload=args.reload,
            atp_config=args.atp_config,
            atp_specs=args.atp,
            atp_heartbeat=args.atp_heartbeat,
        )
    parser.error(f"unknown command: {args.command!r}")
    return 2


def _run_server(
    host: str,
    port: int,
    reload: bool,
    atp_config: str | None = None,
    atp_specs: list[str] | None = None,
    atp_heartbeat: float | None = None,
) -> int:
    import uvicorn

    from .config import (
        ATP_ENDPOINTS_ENV,
        ATP_HEARTBEAT_ENV,
        ConfigError,
        encode_env,
        resolve_endpoints,
    )

    try:
        endpoints = resolve_endpoints(atp_config, atp_specs or ())
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if endpoints:
        os.environ[ATP_ENDPOINTS_ENV] = encode_env(endpoints)
    else:
        os.environ.pop(ATP_ENDPOINTS_ENV, None)
    if atp_heartbeat is not None:
        os.environ[ATP_HEARTBEAT_ENV] = str(atp_heartbeat)
    else:
        os.environ.pop(ATP_HEARTBEAT_ENV, None)

    uvicorn.run(
        "a_train.bootstrap:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
