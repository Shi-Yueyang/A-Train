"""Phase 3.1 acceptance tests for repeatable ATP endpoint arguments."""

from __future__ import annotations

import asyncio
import os

import pytest

from a_train import __main__ as cli
from a_train.config import (
    ATP_ENDPOINTS_ENV,
    ConfigError,
    decode_env,
    encode_env,
    parse_endpoint_spec,
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

EP1 = {"cab_id": 1, "host": "127.0.0.1", "port": 9101}


# -- Endpoint spec / file parsing ----------------------------------------------


def test_parse_endpoint_spec_accepts_ipv4() -> None:
    assert parse_endpoint_spec("1=127.0.0.1:9101") == EP1


def test_parse_endpoint_spec_accepts_bracketed_ipv6() -> None:
    got = parse_endpoint_spec("2=[::1]:9102")
    assert got == {"cab_id": 2, "host": "::1", "port": 9102}


@pytest.mark.parametrize(
    "bad",
    [
        "1:127.0.0.1",  # no '='
        "=127.0.0.1:9101",  # no cab id
        "x=127.0.0.1:9101",  # cab id not int
        "0=127.0.0.1:9101",  # cab id below 1
        "1=127.0.0.1",  # no port
        "1=127.0.0.1:abc",  # port not int
        "1=127.0.0.1:0",  # port out of range
        "1=127.0.0.1:99999",  # port out of range
    ],
)
def test_parse_endpoint_spec_rejects_invalid(bad: str) -> None:
    with pytest.raises(ConfigError):
        parse_endpoint_spec(bad)


def test_env_roundtrip() -> None:
    assert decode_env(encode_env([EP1])) == [EP1]
    assert decode_env("") == []


def test_env_rejects_malformed() -> None:
    with pytest.raises(ConfigError):
        decode_env("{not json")
    with pytest.raises(ConfigError):
        decode_env('{"cab_id": 1}')


# -- run command wiring ---------------------------------------------------------


def test_run_command_configures_endpoints_for_the_server(monkeypatch) -> None:
    calls: dict = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: calls.update(app=app, **kw))
    monkeypatch.delenv(ATP_ENDPOINTS_ENV, raising=False)

    code = cli.main(["run", "--host", "0.0.0.0", "--port", "8123", "--atp", "1=127.0.0.1:9101"])

    try:
        assert code == 0
        assert calls["app"] == "a_train.bootstrap:create_app"
        assert calls["host"] == "0.0.0.0" and calls["port"] == 8123
        assert decode_env(os.environ[ATP_ENDPOINTS_ENV]) == [EP1]
    finally:
        os.environ.pop(ATP_ENDPOINTS_ENV, None)


def test_run_command_clears_stale_env_without_endpoints(monkeypatch) -> None:
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: None)
    monkeypatch.setenv(ATP_ENDPOINTS_ENV, encode_env([EP1]))
    cli.main(["run"])
    assert ATP_ENDPOINTS_ENV not in os.environ


def test_run_command_reports_invalid_spec_without_starting(monkeypatch, capsys) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("uvicorn must not start on invalid config")

    monkeypatch.setattr("uvicorn.run", boom)
    code = cli.main(["run", "--atp", "nonsense"])
    assert code == 2
    assert "CAB_ID=HOST:PORT" in capsys.readouterr().err


# -- The factory path: create_app() with no explicit endpoints ------------------


async def test_env_configured_endpoints_connect_through_run_wiring(monkeypatch) -> None:
    server = TestAtpServer()
    port = await server.start()
    endpoint = {**EP1, "port": port}
    monkeypatch.setenv(ATP_ENDPOINTS_ENV, encode_env([endpoint]))
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
                "train_id": "TRAIN001",
                "cab_id": 1,
                "host": "127.0.0.1",
                "port": port,
                "state": "READY",
                "ready": True,
            }
            # The channel carries content with no handshake exchange.
            first = await server.wait_for_message()
            assert first["type"] != "hello"
    finally:
        await server.stop()


async def test_invalid_env_config_fails_startup(monkeypatch) -> None:
    monkeypatch.setenv(ATP_ENDPOINTS_ENV, "{not json")
    with pytest.raises(ConfigError):
        async with running_app([T1]):
            pass


async def test_no_endpoints_configured_exposes_empty_status(app_client) -> None:
    body = (await app_client.get("/api/atp/status")).json()
    assert body == {"connections": []}
