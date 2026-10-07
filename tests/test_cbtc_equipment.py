"""CBTC authorization equipment through the public configuration and API."""

from __future__ import annotations

from a_train.config import train_config_from_data
from tests.support.app import running_app


def _train_config():
    return train_config_from_data(
        {
            "train": {
                "train_id": "TRAIN001",
                "cabs": [{"cab_id": 1, "active": True}],
                "physics": {"max_traction_accel": 1.0, "max_decel": 2.0},
                "equipment": [
                    {
                        "type": "cbtc",
                        "cab_id": 1,
                        "params": {"is_cbtc_authorized": True},
                    },
                    {"type": "switch_box", "cab_id": 1},
                ],
            }
        }
    )


def _equipment(train: dict, key: str) -> dict:
    return next(entry for entry in train["equipment"] if entry["key"] == key)


async def test_cbtc_authorization_is_configurable_and_independent() -> None:
    async with running_app([_train_config()]) as client:
        response = await client.get("/api/trains/TRAIN001")
        assert response.status_code == 200
        train = response.json()
        cbtc = _equipment(train, "cbtc_1")
        assert cbtc["type"] == "cbtc"
        assert cbtc["cab_id"] == 1
        assert cbtc["state"] == {"cab_id": 1, "is_cbtc_authorized": True}

        response = await client.post(
            "/api/trains/TRAIN001/equipment/cbtc_1",
            json={"is_cbtc_authorized": False},
        )
        assert response.status_code == 200
        train = response.json()
        assert _equipment(train, "cbtc_1")["state"]["is_cbtc_authorized"] is False
        assert _equipment(train, "switch_box_1")["state"]["cbtc_authorized"] is False

        response = await client.post(
            "/api/trains/TRAIN001/equipment/switch_box_1",
            json={"cbtc_authorized": True},
        )
        assert response.status_code == 200
        train = response.json()
        assert _equipment(train, "cbtc_1")["state"]["is_cbtc_authorized"] is False
        assert _equipment(train, "switch_box_1")["state"]["cbtc_authorized"] is True

        response = await client.post("/api/trains/TRAIN001/equipment/cbtc_1", json={})
        assert response.status_code == 400
        unchanged = (await client.get("/api/trains/TRAIN001")).json()
        assert _equipment(unchanged, "cbtc_1")["state"]["is_cbtc_authorized"] is False

        response = await client.post("/api/trains/TRAIN001/equipment/cbtc_1/reset", json={})
        assert response.status_code == 200
        assert _equipment(response.json(), "cbtc_1")["state"]["is_cbtc_authorized"] is True
