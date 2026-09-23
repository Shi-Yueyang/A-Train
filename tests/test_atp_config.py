"""End-to-end acceptance for ATP endpoints configured at startup (atp-api.md §6.1).

Only the real boundaries are exercised: the environment-configured app dials a
production-protocol TCP test server, invalid configuration fails startup, and
the real CLI run command rejects bad input without starting the server.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from a_train import __main__ as cli
from a_train.config import TRAIN_CONFIG_ENV, ConfigError
from a_train.domain.train import TrainConfig
from tests.support.app import running_app
from tests.support.atp_server import TestAtpServer

T1 = TrainConfig(
    train_id="TRAIN001",
    cab_ids=(1, 2),
    initial_active_cab=1,
    max_traction_accel=1.0,
    max_decel=2.0,
    initial_position=0.0,
)

EP1 = {"host": "127.0.0.1", "port": 9101}
TRAIN_CONFIG = "docs/train.json"


def test_run_command_reports_invalid_atp_config_without_starting(
    monkeypatch, capsys, tmp_path
) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("uvicorn must not start on invalid config")

    monkeypatch.setattr("uvicorn.run", boom)
    monkeypatch.delenv(TRAIN_CONFIG_ENV, raising=False)
    config = json.loads(Path(TRAIN_CONFIG).read_text("utf-8"))
    config["atp"] = [{"host": "127.0.0.1"}]  # missing port
    path = tmp_path / "train.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    code = cli.main(["run", "--train-config", str(path)])
    assert code == 2
    assert "port" in capsys.readouterr().err
    assert TRAIN_CONFIG_ENV not in os.environ


async def test_env_configured_endpoints_connect_through_run_wiring(monkeypatch) -> None:
    server = TestAtpServer()
    port = await server.start()
    endpoint = {**EP1, "port": port}
    monkeypatch.setenv(TRAIN_CONFIG_ENV, json.dumps({"atp": [endpoint]}))
    try:
        async with running_app([T1]) as c:

            async def _poll_ready() -> dict:
                while True:
                    body = (await c.get("/api/atp/status")).json()
                    if body["connections"][0]["ready"]:
                        return body
                    await asyncio.sleep(0.02)

            body = await asyncio.wait_for(_poll_ready(), 5.0)
            conn = body["connections"][0]
            assert conn == {
                "host": "127.0.0.1",
                "port": port,
                "state": "READY",
                "ready": True,
            }
            # The channel carries content with no handshake exchange.
            first = await server.wait_for_message()
            assert first["type"] == "train_state"
            assert "cab_id" not in first
    finally:
        await server.stop()


async def test_invalid_env_config_fails_startup(monkeypatch) -> None:
    monkeypatch.setenv(TRAIN_CONFIG_ENV, "{not json")
    with pytest.raises(ConfigError):
        async with running_app([T1]):
            pass


async def test_no_endpoints_configured_exposes_empty_status(app_client) -> None:
    body = (await app_client.get("/api/atp/status")).json()
    assert body == {"connections": []}
