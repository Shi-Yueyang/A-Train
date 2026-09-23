"""Acceptance tests for ATP endpoints configured in the startup config file."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from a_train import __main__ as cli
from a_train.config import (
    TRAIN_CONFIG_ENV,
    ConfigError,
    decode_config,
    encode_config,
    endpoints_from_data,
    startup_config_from_data,
)
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


# -- Endpoint validation ---------------------------------------------------------


def test_endpoints_from_data_normalises_entries() -> None:
    assert endpoints_from_data([EP1]) == [EP1]
    assert endpoints_from_data(None) == []


@pytest.mark.parametrize(
    "bad",
    [
        {"cab_id": 1, "host": "127.0.0.1", "port": 9101},  # legacy cab binding removed
        {"host": "", "port": 9101},  # empty host
        {"port": 9101},  # missing host
        {"host": "127.0.0.1"},  # missing port
        {"host": "127.0.0.1", "port": 0},  # port out of range
        {"host": "127.0.0.1", "port": 99999},  # port out of range
        {"host": "127.0.0.1", "port": "abc"},  # port not int
    ],
)
def test_endpoints_from_data_rejects_invalid(bad: dict) -> None:
    with pytest.raises(ConfigError):
        endpoints_from_data([bad])


def test_endpoints_from_data_rejects_non_list() -> None:
    with pytest.raises(ConfigError):
        endpoints_from_data({"host": "127.0.0.1"})


def test_startup_config_roundtrip_through_env() -> None:
    value = encode_config(T1, [EP1])
    train, endpoints = decode_config(value)
    assert train is not None and train.train_id == "TRAIN001"
    assert endpoints == [EP1]


def test_startup_config_accepts_bare_train_or_atp_only() -> None:
    train, endpoints = startup_config_from_data(
        {
            "train_id": "TRAIN001",
            "cabs": [{"cab_id": 1, "active": True}],
            "physics": {"max_traction_accel": 1.0, "max_decel": 1.0},
        }
    )
    assert train is not None and endpoints == []

    train, endpoints = startup_config_from_data({"atp": [EP1]})
    assert train is None and endpoints == [EP1]


# -- run command wiring ---------------------------------------------------------


def test_run_command_configures_endpoints_for_the_server(monkeypatch, tmp_path) -> None:
    calls: dict = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: calls.update(app=app, **kw))
    monkeypatch.delenv(TRAIN_CONFIG_ENV, raising=False)

    config = json.loads(Path(TRAIN_CONFIG).read_text("utf-8"))
    config["atp"] = [EP1]
    path = tmp_path / "train.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    code = cli.main(["run", "--host", "0.0.0.0", "--port", "8123", "--train-config", str(path)])

    try:
        assert code == 0
        assert calls["app"] == "a_train.bootstrap:create_app"
        assert calls["host"] == "0.0.0.0" and calls["port"] == 8123
        train, endpoints = decode_config(os.environ[TRAIN_CONFIG_ENV])
        assert endpoints == [EP1]
        assert train is not None and train.train_id == "TRAIN001"
    finally:
        os.environ.pop(TRAIN_CONFIG_ENV, None)


def test_run_command_reports_invalid_atp_config_without_starting(
    monkeypatch, capsys, tmp_path
) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("uvicorn must not start on invalid config")

    monkeypatch.setattr("uvicorn.run", boom)
    config = json.loads(Path(TRAIN_CONFIG).read_text("utf-8"))
    config["atp"] = [{"host": "127.0.0.1"}]  # missing port
    path = tmp_path / "train.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    code = cli.main(["run", "--train-config", str(path)])
    assert code == 2
    assert "port" in capsys.readouterr().err


# -- The factory path: create_app() with no explicit endpoints ------------------


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
