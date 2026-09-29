"""Phase 0 acceptance tests (TODO.md §Phase 0 passing criteria).

* ``python -m a_train --help`` documents the ``run`` command.
* The application starts and stops cleanly.
* ``pytest`` discovers and runs the integration suite.
* The fixture starts the real application and a controllable TCP test ATP
  server without mocking production modules.
"""

from __future__ import annotations

import subprocess
import sys

from a_train import __main__ as cli


def test_cli_help_documents_run_command() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "a_train", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "run" in result.stdout


def test_cli_log_level_defaults_to_info_and_accepts_case_insensitive_values() -> None:
    parser = cli._build_parser()

    default_args = parser.parse_args(["run", "--train-config", "train.json"])
    assert default_args.log_level == "INFO"

    debug_args = parser.parse_args(["run", "--train-config", "train.json", "--log-level", "debug"])
    assert debug_args.log_level == "DEBUG"


async def test_application_starts_and_stops_cleanly(app_client) -> None:
    # The fixture's lifespan has already started the real core and ATP manager.
    # A successful request proves the app responds; clean fixture teardown
    # proves graceful shutdown.
    response = await app_client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["simulation_state"] == "STOPPED"
    assert body["simulation_time"] == 0.0


async def test_fixture_starts_real_app_and_atp_server(app_client, atp_server) -> None:
    response = await app_client.get("/api/status")
    assert response.status_code == 200
    assert atp_server.port > 0
