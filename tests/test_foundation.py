"""Phase 0 acceptance tests (TODO.md §Phase 0 passing criteria).

* ``python -m a_train --help`` documents the ``run`` command.
* The application starts and stops cleanly.
* ``pytest`` discovers and runs the integration suite.
* The fixture starts the real application and a controllable TCP test ATP
  server without mocking production modules.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys


def test_cli_help_documents_run_command() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "a_train", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "run" in result.stdout


async def test_application_starts_and_stops_cleanly(app_client) -> None:
    # The fixture's lifespan has already started the real core and ATP manager.
    # A successful request proves the app responds; clean fixture teardown
    # proves graceful shutdown.
    response = await app_client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["simulation_state"] == "STOPPED"
    assert body["simulation_time"] == 0.0


async def test_atp_server_speaks_ndjson(atp_server) -> None:
    port = atp_server.port
    assert port > 0
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        sent = {"type": "train_state", "train_id": "T1", "cab_id": 1, "speed": 0.0}
        writer.write((json.dumps(sent) + "\n").encode("utf-8"))
        await writer.drain()

        received = await atp_server.wait_for_message()
        assert received == sent

        await atp_server.send({"type": "heartbeat_ack"})
        line = await reader.readline()
        assert json.loads(line.decode("utf-8"))["type"] == "heartbeat_ack"
    finally:
        writer.close()
        await writer.wait_closed()


async def test_fixture_starts_real_app_and_atp_server(app_client, atp_server) -> None:
    response = await app_client.get("/api/status")
    assert response.status_code == 200
    assert atp_server.port > 0
