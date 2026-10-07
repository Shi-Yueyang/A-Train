"""CBTC switch-logic WebSocket protocol (§5.3)."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal, TypeVar

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from ...domain.snapshots import EquipmentSnapshot, SwitchBoxSnapshot, TrainSnapshot
from ...domain.train import EquipmentControlRequest
from ...simulation.commands import EquipmentCommand
from ...simulation.core import SimulationCore

logger = logging.getLogger("a_train.adapters.switch_logic")

router = APIRouter()


class _HelloMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["hello"]
    cab_id: StrictInt = Field(gt=0)


class _CbtcOutMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["cbtc-out"]
    cbtc_authorized: StrictBool


MessageT = TypeVar("MessageT", bound=BaseModel)


def _parse_message(text: str, model: type[MessageT]) -> MessageT | None:
    try:
        return model.model_validate_json(text)
    except ValidationError:
        return None


def _switch_box(
    snapshot: TrainSnapshot, cab_id: int, key: str | None = None
) -> EquipmentSnapshot | None:
    return next(
        (
            equipment
            for equipment in snapshot.equipment
            if equipment.type == "switch_box"
            and equipment.cab_id == cab_id
            and (key is None or equipment.key == key)
        ),
        None,
    )


def _cbtc_inputs(snapshot: TrainSnapshot, cab_id: int, key: str) -> tuple[bool, bool] | None:
    equipment = _switch_box(snapshot, cab_id, key)
    if equipment is None or not isinstance(equipment.state, SwitchBoxSnapshot):
        return None
    box = equipment.state
    cbtc_active = box.position == "cbtc" or (
        box.position == "auto" and box.cbtc_authorized and not box.c2_authorized
    )
    return cbtc_active, box.c2_authorized


async def _receive_text(websocket: WebSocket) -> str | None:
    try:
        return await websocket.receive_text()
    except WebSocketDisconnect:
        return None
    except RuntimeError:
        await websocket.close(code=1002, reason="text JSON messages required")
        return None


async def _exchange(
    websocket: WebSocket,
    core: SimulationCore,
    cab_id: int,
    box_key: str,
    queue: asyncio.Queue,
    last_inputs: tuple[bool, bool],
) -> None:
    receive_task = asyncio.create_task(_receive_text(websocket))
    snapshot_task = asyncio.create_task(queue.get())
    try:
        while True:
            done, _ = await asyncio.wait(
                (receive_task, snapshot_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                text = receive_task.result()
                if text is None:
                    return
                message = _parse_message(text, _CbtcOutMessage)
                if message is None:
                    await websocket.close(code=1002, reason="expected cbtc-out message")
                    return
                result = await core.submit_command(
                    EquipmentCommand(
                        train_id=core.train_id,
                        payload=EquipmentControlRequest(
                            key=box_key,
                            cbtc_authorized=message.cbtc_authorized,
                        ),
                    )
                )
                if not result.ok:
                    logger.error(
                        "CBTC WebSocket cab %s switch-box update failed: %s",
                        cab_id,
                        result.error,
                    )
                    await websocket.close(code=1011, reason="switch-box update failed")
                    return
                receive_task = asyncio.create_task(_receive_text(websocket))

            if snapshot_task in done:
                snapshot = snapshot_task.result()
                train = snapshot.trains[0]
                current_inputs = _cbtc_inputs(train, cab_id, box_key)
                snapshot_task = asyncio.create_task(queue.get())
                if current_inputs is None:
                    logger.error("CBTC WebSocket cab %s lost its switch-box snapshot", cab_id)
                    await websocket.close(code=1011, reason="switch-box state unavailable")
                    return
                if current_inputs != last_inputs:
                    last_inputs = current_inputs
                    await websocket.send_json(
                        {
                            "type": "cbtc-in",
                            "cbtc_active": current_inputs[0],
                            "c2_authorized": current_inputs[1],
                        }
                    )
    finally:
        for task in (receive_task, snapshot_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(receive_task, snapshot_task, return_exceptions=True)


@router.websocket("/ws/cbtc")
async def switch_logic_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    core: SimulationCore = websocket.app.state.core
    connected_cabs: set[int] = websocket.app.state.switch_logic_connected_cabs
    cab_id: int | None = None
    queue: asyncio.Queue | None = None

    try:
        text = await _receive_text(websocket)
        if text is None:
            return
        hello = _parse_message(text, _HelloMessage)
        if hello is None:
            await websocket.close(code=1002, reason="expected hello message")
            return

        snapshot = core.get_snapshot().trains[0]
        box = _switch_box(snapshot, hello.cab_id)
        if box is None:
            await websocket.close(code=1008, reason="cab has no configured switch box")
            return
        if hello.cab_id in connected_cabs:
            await websocket.close(code=1008, reason="cab already has a connected client")
            return

        cab_id = hello.cab_id
        connected_cabs.add(cab_id)
        queue = core.subscribe()
        initial_snapshot = queue.get_nowait().trains[0]
        initial_inputs = _cbtc_inputs(initial_snapshot, cab_id, box.key)
        if initial_inputs is None:
            logger.error("CBTC WebSocket cab %s has no usable switch-box state", cab_id)
            await websocket.close(code=1011, reason="switch-box state unavailable")
            return

        await websocket.send_json({"type": "hello_ack", "cab_id": cab_id})
        await websocket.send_json(
            {
                "type": "cbtc-in",
                "cbtc_active": initial_inputs[0],
                "c2_authorized": initial_inputs[1],
            }
        )
        await _exchange(
            websocket,
            core,
            cab_id,
            box.key,
            queue,
            initial_inputs,
        )
    except WebSocketDisconnect:
        return
    finally:
        if queue is not None:
            core.unsubscribe(queue)
        if cab_id is not None:
            connected_cabs.discard(cab_id)
