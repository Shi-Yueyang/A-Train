"""CBTC authorization equipment through the public configuration and API."""

from __future__ import annotations

from a_train.config import train_config_from_data
from tests.support.app import running_app


def _train_config():
    return train_config_from_data(
        {
            "train": {
                "train_id": "TRAIN001",
                "cabs": [{"cab_id": 1, "active": True}, {"cab_id": 2}],
                "physics": {"max_traction_accel": 1.0, "max_decel": 2.0},
                "equipment": [
                    {
                        "type": "cbtc",
                        "cab_id": 1,
                        "params": {"is_cbtc_authorized": True},
                    },
                    {"type": "stcs_atp_duo", "cab_id": 1},
                    {"type": "switch_box", "cab_id": 1},
                    {"type": "switch_box", "cab_id": 2},
                ],
            }
        }
    )


def _equipment(train: dict, key: str) -> dict:
    return next(entry for entry in train["equipment"] if entry["key"] == key)


def _control_state_bits(train: dict) -> dict[str, bool]:
    signals = _equipment(train, "stcs_atp_duo_1")["state"]["train_out_states"]
    names = (
        "c2_control_state_1_1",
        "c2_control_state_1_2",
        "c2_control_state_2_1",
        "c2_control_state_2_2",
    )
    values = {signal["name"]: signal["value"] for signal in signals}
    return {name: values[name] for name in names}


async def test_cbtc_authorization_feeds_same_cab_switch_box() -> None:
    async with running_app([_train_config()]) as client:
        response = await client.get("/api/trains/TRAIN001")
        assert response.status_code == 200
        train = response.json()
        cbtc = _equipment(train, "cbtc_1")
        assert cbtc["type"] == "cbtc"
        assert cbtc["cab_id"] == 1
        assert cbtc["state"] == {"cab_id": 1, "is_cbtc_authorized": True}
        assert _equipment(train, "switch_box_1")["state"]["cbtc_authorized"] is True
        assert _equipment(train, "switch_box_2")["state"]["cbtc_authorized"] is False
        assert _control_state_bits(train) == {
            "c2_control_state_1_1": True,
            "c2_control_state_1_2": True,
            "c2_control_state_2_1": False,
            "c2_control_state_2_2": False,
        }

        response = await client.post(
            "/api/trains/TRAIN001/equipment/switch_box_1",
            json={"system_switch": "auto"},
        )
        assert response.status_code == 200
        assert _control_state_bits(response.json()) == {
            "c2_control_state_1_1": False,
            "c2_control_state_1_2": False,
            "c2_control_state_2_1": True,
            "c2_control_state_2_2": True,
        }

        response = await client.post(
            "/api/trains/TRAIN001/equipment/cbtc_1",
            json={"is_cbtc_authorized": False},
        )
        assert response.status_code == 200
        train = response.json()
        assert _equipment(train, "cbtc_1")["state"]["is_cbtc_authorized"] is False
        assert _equipment(train, "switch_box_1")["state"]["cbtc_authorized"] is False
        assert _equipment(train, "switch_box_2")["state"]["cbtc_authorized"] is False
        assert _control_state_bits(train) == {
            "c2_control_state_1_1": False,
            "c2_control_state_1_2": False,
            "c2_control_state_2_1": False,
            "c2_control_state_2_2": False,
        }

        response = await client.post(
            "/api/trains/TRAIN001/equipment/cbtc_1",
            json={"is_cbtc_authorized": True},
        )
        assert response.status_code == 200
        assert _equipment(response.json(), "switch_box_1")["state"]["cbtc_authorized"] is True

        response = await client.post(
            "/api/trains/TRAIN001/links/cut",
            json={"source": "cbtc_1", "target": "switch_box_1"},
        )
        assert response.status_code == 200
        response = await client.post(
            "/api/trains/TRAIN001/equipment/cbtc_1",
            json={"is_cbtc_authorized": False},
        )
        assert response.status_code == 200
        train = response.json()
        assert _equipment(train, "cbtc_1")["state"]["is_cbtc_authorized"] is False
        assert _equipment(train, "switch_box_1")["state"]["cbtc_authorized"] is True
        assert _equipment(train, "switch_box_2")["state"]["cbtc_authorized"] is False

        response = await client.delete(
            "/api/trains/TRAIN001/links/cut",
            params={"source": "cbtc_1", "target": "switch_box_1"},
        )
        assert response.status_code == 200
        response = await client.post(
            "/api/trains/TRAIN001/equipment/cbtc_1",
            json={"is_cbtc_authorized": False},
        )
        assert response.status_code == 200
        train = response.json()
        assert _equipment(train, "switch_box_1")["state"]["cbtc_authorized"] is False

        response = await client.post("/api/trains/TRAIN001/equipment/cbtc_1", json={})
        assert response.status_code == 400
        unchanged = (await client.get("/api/trains/TRAIN001")).json()
        assert _equipment(unchanged, "cbtc_1")["state"]["is_cbtc_authorized"] is False

        response = await client.post("/api/trains/TRAIN001/equipment/cbtc_1/reset", json={})
        assert response.status_code == 200
        assert _equipment(response.json(), "cbtc_1")["state"]["is_cbtc_authorized"] is True
