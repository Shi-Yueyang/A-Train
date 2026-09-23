"""End-to-end acceptance: the environment train config reaches the public API."""

from __future__ import annotations

import pytest_asyncio

from a_train.config import TRAIN_CONFIG_ENV, encode_config
from a_train.domain.train import EquipmentConfig, TrainConfig
from tests.support.app import running_app

CUSTOM = TrainConfig(
    train_id="CUSTOM001",
    cab_ids=(10, 20),
    initial_active_cab=10,
    max_traction_accel=2.5,
    max_decel=3.0,
    initial_position=12.5,
    initial_speed=-1.0,
    equipment_configs=(
        EquipmentConfig("door", "left_door", {"side": "left"}),
        EquipmentConfig("btm", "btm_10", {"cab_id": 10}),
        EquipmentConfig("stcs_atp_duo", "stcs_atp_duo_10", {"cab_id": 10}),
        EquipmentConfig(
            "driving_system",
            "driving_system_10",
            {"cab_id": 10, "initial_direction": "forward"},
        ),
    ),
)


@pytest_asyncio.fixture
async def configured_client(monkeypatch):
    monkeypatch.setenv(TRAIN_CONFIG_ENV, encode_config(CUSTOM))
    async with running_app(use_environment_train_config=True) as client:
        yield client


async def test_environment_train_config_reaches_public_snapshot(configured_client) -> None:
    response = await configured_client.get("/api/trains")
    assert response.status_code == 200
    train = response.json()["trains"][0]
    assert train["train_id"] == "CUSTOM001"
    assert train["position"] == 12.5
    assert {entry["key"] for entry in train["equipment"]} == {
        "left_door",
        "btm_10",
        "stcs_atp_duo_10",
        "driving_system_10",
    }
