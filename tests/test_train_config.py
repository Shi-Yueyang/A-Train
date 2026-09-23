from __future__ import annotations

import json

import pytest
import pytest_asyncio

from a_train.config import (
    TRAIN_CONFIG_ENV,
    ConfigError,
    encode_config,
    load_config_file,
    train_config_from_data,
)
from a_train.domain.train import EquipmentConfig, Train, TrainConfig
from tests.support.app import running_app


def valid_config() -> dict:
    return {
        "train": {
            "train_id": "CUSTOM001",
            "cabs": [
                {"cab_id": 10, "facing": "forward", "active": True},
                {"cab_id": 20, "facing": "backward", "active": False},
            ],
            "physics": {
                "max_traction_accel": 2.5,
                "max_decel": 3.0,
                "initial_position": 12.5,
                "initial_speed": -1.0,
                "initial_door_state": "closed",
            },
            "equipment": [
                {"type": "door", "params": {"side": "left"}},
                {"type": "btm", "cab_id": 10},
                {"type": "stcs_atp_duo", "cab_id": 10},
                {
                    "type": "driving_system",
                    "cab_id": 10,
                    "params": {"initial_direction": "forward"},
                },
            ],
        }
    }


def test_train_config_from_data_supports_explicit_equipment() -> None:
    config = train_config_from_data(valid_config())

    assert config.train_id == "CUSTOM001"
    assert config.cab_ids == (10, 20)
    assert config.cab_facings == {10: 1, 20: -1}
    assert [item.key for item in config.equipment_configs] == [
        "left_door",
        "btm_10",
        "stcs_atp_duo_10",
        "driving_system_10",
    ]
    assert config.equipment_configs[1].params == {"cab_id": 10}


def test_empty_equipment_list_is_respected() -> None:
    data = valid_config()
    data["train"]["equipment"] = []
    assert train_config_from_data(data).equipment_configs == ()


def test_disabled_equipment_is_not_installed() -> None:
    data = valid_config()
    data["train"]["equipment"][1]["enabled"] = False
    config = train_config_from_data(data)
    snapshot = Train(config).get_snapshot()
    assert "btm_10" not in {entry.key for entry in snapshot.equipment}


def test_enabled_must_be_boolean() -> None:
    data = valid_config()
    data["train"]["equipment"][0]["enabled"] = "false"
    with pytest.raises(ConfigError, match="enabled"):
        train_config_from_data(data)


def test_config_file_loads_train_and_endpoints(tmp_path) -> None:
    data = valid_config()
    data["atp"] = [{"host": "127.0.0.1", "port": 9101}]
    path = tmp_path / "train.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    train, endpoints = load_config_file(path)
    assert train.train_id == "CUSTOM001"
    assert endpoints == [{"host": "127.0.0.1", "port": 9101}]


def test_config_file_requires_train_section(tmp_path) -> None:
    path = tmp_path / "train.json"
    path.write_text(json.dumps({"atp": []}), encoding="utf-8")
    with pytest.raises(ConfigError, match="train"):
        load_config_file(path)


def test_duplicate_equipment_for_same_cab_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate 'btm' equipment for cab 1"):
        Train(
            TrainConfig(
                train_id="TRAIN001",
                cab_ids=(1,),
                initial_active_cab=1,
                max_traction_accel=1.0,
                max_decel=1.0,
                equipment_configs=(
                    EquipmentConfig("btm", "btm_a", {"cab_id": 1}),
                    EquipmentConfig("btm", "btm_b", {"cab_id": 1}),
                ),
            )
        )


def test_duplicate_equipment_for_same_cab_rejected_at_config() -> None:
    data = valid_config()
    data["train"]["equipment"].append({"type": "btm", "cab_id": 10})
    with pytest.raises(ConfigError, match="duplicate 'btm' for cab 10"):
        train_config_from_data(data)


@pytest_asyncio.fixture
async def configured_client(monkeypatch):
    monkeypatch.setenv(
        TRAIN_CONFIG_ENV,
        encode_config(train_config_from_data(valid_config())),
    )
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


def test_train_section_rejects_nested_atp_key() -> None:
    data = valid_config()
    data["train"]["atp"] = [{"cab_id": 10, "host": "127.0.0.1", "port": 9101}]
    with pytest.raises(ConfigError, match="top-level 'atp'"):
        train_config_from_data(data)


@pytest.mark.parametrize(
    "change, message",
    [
        ({"cabs": []}, "cabs"),
        ({"physics": {"max_traction_accel": 1.0}}, "max_decel"),
        ({"cabs": [{"cab_id": 1, "facing": "sideways", "active": True}]}, "facing"),
    ],
)
def test_train_config_rejects_invalid_data(change, message) -> None:
    data = valid_config()["train"]
    data.update(change)
    with pytest.raises(ConfigError, match=message):
        train_config_from_data({"train": data})
