from __future__ import annotations

import json

import pytest
import pytest_asyncio

from a_train.config import (
    TRAIN_CONFIG_ENV,
    ConfigError,
    encode_train_config,
    load_train_config_file,
    train_config_from_data,
)
from a_train.domain.train import Train
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
                {"type": "door", "key": "left_door", "params": {"side": "left"}},
                {"type": "btm", "key": "front_balise", "cab_id": 10},
                {"type": "stcs_atp", "key": "front_atp", "cab_id": 10},
                {
                    "type": "driving_system",
                    "key": "front_driver",
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
        "front_balise",
        "front_atp",
        "front_driver",
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
    assert "front_balise" not in {entry.key for entry in snapshot.equipment}


def test_enabled_must_be_boolean() -> None:
    data = valid_config()
    data["train"]["equipment"][0]["enabled"] = "false"
    with pytest.raises(ConfigError, match="enabled"):
        train_config_from_data(data)


def test_train_config_file_loads_json(tmp_path) -> None:
    path = tmp_path / "train.json"
    path.write_text(json.dumps(valid_config()), encoding="utf-8")
    assert load_train_config_file(path).train_id == "CUSTOM001"


@pytest_asyncio.fixture
async def configured_client(monkeypatch):
    monkeypatch.setenv(
        TRAIN_CONFIG_ENV,
        encode_train_config(train_config_from_data(valid_config())),
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
        "front_balise",
        "front_atp",
        "front_driver",
    }


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
