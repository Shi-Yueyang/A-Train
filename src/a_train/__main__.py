"""Command-line entry point: ``python -m a_train``.

Two subcommands are exposed (Phase 0):

* ``run``   -- start the simulator HTTP/WebSocket server (headless).
* ``test``  -- run a scenario file in MANUAL mode and report pass/fail.

The deterministic scenario runner (``MANUAL`` mode + assert events) is
implemented in Phase 6; Phase 0 documents the command and validates that the
target file exists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


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

    test_p = subparsers.add_parser(
        "test",
        help="Run a scenario file and report pass/fail.",
        description=(
            "Run a scenario file in MANUAL mode and report pass/fail. "
            "The deterministic scenario runner is implemented in Phase 6."
        ),
    )
    test_p.add_argument("scenario", type=Path, help="Path to the YAML scenario file.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return _run_server(host=args.host, port=args.port, reload=args.reload)
    if args.command == "test":
        return _run_scenario(args.scenario)
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


def _run_scenario(scenario: Path) -> int:
    if not scenario.is_file():
        print(f"error: scenario not found: {scenario}", file=sys.stderr)
        return 1
    # The deterministic scenario runner (MANUAL mode + assert events) is
    # implemented in Phase 6. Phase 0 only validates that the file exists.
    print(f"scenario runner is not implemented until Phase 6: {scenario}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
