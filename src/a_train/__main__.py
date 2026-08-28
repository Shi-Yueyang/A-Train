"""Command-line entry point: ``python -m a_train``.

Exposes the ``run`` subcommand, which starts the simulator HTTP/WebSocket
server (headless). The web UI is an optional client.
"""

from __future__ import annotations

import argparse


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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _run_server(host=args.host, port=args.port, reload=args.reload)
    parser.error(f"unknown command: {args.command!r}")
    return 2


def _run_server(host: str, port: int, reload: bool) -> int:
    import uvicorn

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
