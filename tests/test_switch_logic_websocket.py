"""Integration tests for the switch-logic CBTC WebSocket adapter."""

from __future__ import annotations

import asyncio

from a_train.domain.train import EquipmentConfig, TrainConfig
from tests.support.app import running_app
from tests.support.ws import ws_connect


def _train_config(*, switch_box_cabs: tuple[int, ...]) -> TrainConfig:
    return TrainConfig(
        train_id="TRAIN001",
        cab_ids=(1, 2),
        initial_active_cab=1,
        max_traction_accel=1.0,
        max_decel=2.0,
        equipment_configs=tuple(
            EquipmentConfig(
                "switch_box",
                f"switch_box_{cab_id}",
                {"cab_id": cab_id, "initial_position": "auto"},
            )
            for cab_id in switch_box_cabs
        ),
    )


async def _handshake(ws, cab_id: int) -> tuple[dict, dict]:
    await ws.send_json({"type": "hello", "cab_id": cab_id})
    acknowledgement = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
    initial_inputs = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
    return acknowledgement, initial_inputs


async def test_switch_logic_websocket_exchanges_switch_box_state() -> None:
    async with running_app([_train_config(switch_box_cabs=(1,))]) as client:
        async with ws_connect(client.app, "/ws/cbtc") as ws:
            acknowledgement, initial = await _handshake(ws, 1)
            assert acknowledgement == {"type": "hello_ack", "cab_id": 1}
            assert initial == {
                "type": "cbtc-in",
                "cbtc_active": False,
                "c2_authorized": False,
            }

            await ws.send_json({"type": "cbtc-out", "cbtc_authorized": True})
            updated_inputs = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
            assert updated_inputs == {
                "type": "cbtc-in",
                "cbtc_active": True,
                "c2_authorized": False,
            }
            box = (await client.get("/api/trains/TRAIN001")).json()["equipment"]
            switch_box = next(item for item in box if item["key"] == "switch_box_1")
            assert switch_box["state"]["cbtc_authorized"] is True

            response = await client.post(
                "/api/trains/TRAIN001/equipment/switch_box_1",
                json={"c2_authorized": True},
            )
            assert response.status_code == 200
            changed_inputs = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
            assert changed_inputs == {
                "type": "cbtc-in",
                "cbtc_active": False,
                "c2_authorized": True,
            }


async def test_switch_logic_handshake_requires_fitted_box() -> None:
    async with running_app([_train_config(switch_box_cabs=(1,))]) as client:
        async with ws_connect(client.app, "/ws/cbtc") as ws:
            await ws.send_json({"type": "hello", "cab_id": 2})
            await asyncio.wait_for(ws.wait_closed(), timeout=2.0)
            assert ws.close_code == 1008
            assert ws._app_to_client.empty()


async def test_switch_logic_allows_one_client_per_cab() -> None:
    async with running_app([_train_config(switch_box_cabs=(1, 2))]) as client:
        async with ws_connect(client.app, "/ws/cbtc") as first:
            first_ack, _ = await _handshake(first, 1)
            assert first_ack == {"type": "hello_ack", "cab_id": 1}

            async with ws_connect(client.app, "/ws/cbtc") as duplicate:
                await duplicate.send_json({"type": "hello", "cab_id": 1})
                await asyncio.wait_for(duplicate.wait_closed(), timeout=2.0)
                assert duplicate.close_code == 1008
                assert duplicate._app_to_client.empty()

            async with ws_connect(client.app, "/ws/cbtc") as second_cab:
                second_ack, _ = await _handshake(second_cab, 2)
                assert second_ack == {"type": "hello_ack", "cab_id": 2}


async def test_switch_logic_closes_on_invalid_cbtc_out_message() -> None:
    async with running_app([_train_config(switch_box_cabs=(1,))]) as client:
        async with ws_connect(client.app, "/ws/cbtc") as ws:
            await _handshake(ws, 1)
            await ws.send_json({"type": "cbtc-out", "cbtc_authorized": "true"})
            await asyncio.wait_for(ws.wait_closed(), timeout=2.0)
            assert ws.close_code == 1002
