"""Physical link cuts: equipment-wire faults through the public API (§3.7).

Cuts validate per endpoint, address one concrete physical wire (grouped fan
out resolves per recipient), leave recipients stale rather than zeroed,
self-heal on the next delivery after restore, ride the snapshot stream, and
are cleared by reset. Driven through REST and the live WebSocket stream
against the real application (§6.1); no production module is mocked.
"""

from __future__ import annotations

import asyncio

from a_train.domain.train import TrainConfig
from tests.support.app import running_app
from tests.support.ws import ws_connect

T1 = TrainConfig(
    train_id="TRAIN001",
    cab_ids=(1, 2),
    initial_active_cab=1,
    max_traction_accel=1.0,
    max_decel=2.0,
    initial_position=0.0,
)


async def _manual_start(c) -> None:
    await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
    await c.post("/api/simulation/start")


async def _train(c) -> dict:
    return (await c.get("/api/trains/TRAIN001")).json()


async def _step(c, delta: float) -> None:
    await c.post("/api/simulation/step", json={"delta": delta})


async def _command_equipment(c, key: str, **body) -> dict:
    r = await c.post(f"/api/trains/TRAIN001/equipment/{key}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _cut(c, source: str, target: str) -> dict:
    r = await c.post("/api/trains/TRAIN001/links/cut", json={"source": source, "target": target})
    assert r.status_code == 200, r.text
    return r.json()


async def _restore(c, source: str, target: str) -> dict:
    r = await c.delete(
        "/api/trains/TRAIN001/links/cut", params={"source": source, "target": target}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _replace(c, cuts: list) -> dict:
    r = await c.put("/api/trains/TRAIN001/links", json={"cuts": cuts})
    assert r.status_code == 200, r.text
    return r.json()


def _cut_pairs(snap: dict) -> list[tuple[str, str]]:
    return [(cut["source"], cut["target"]) for cut in snap["link_cuts"]]


def _out_signal(snap: dict, key: str = "stcs_atp_duo_1") -> str:
    entry = next(e["state"] for e in snap["equipment"] if e["key"] == key)
    return entry["train_out_signal"]


# -- Validation, replacement semantics, idempotence ------------------------------


async def test_link_cut_endpoints_validate_and_reveal_state() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        assert _cut_pairs(await _train(c)) == []

        for bad in [
            {"source": "ghost", "target": "train"},  # unknown source
            {"source": "left_door", "target": "ghost"},  # unknown target
            {"source": "train", "target": "left_door"},  # train is never a source
            {"source": "left_door", "target": "cab_2"},  # cab ids are not wire targets
            {"source": "left_door", "target": "left_door"},  # no self-cut
        ]:
            r = await c.post("/api/trains/TRAIN001/links/cut", json=bad)
            assert r.status_code == 400, bad
            assert "link" in r.json()["detail"]

        snap = await _cut(c, "left_door", "stcs_atp_duo_1")
        assert _cut_pairs(snap) == [("left_door", "stcs_atp_duo_1")]

        # Cutting an already-cut wire and restoring a live wire are both 200 no-ops.
        snap = await _cut(c, "left_door", "stcs_atp_duo_1")
        assert _cut_pairs(snap) == [("left_door", "stcs_atp_duo_1")]
        snap = await _restore(c, "right_door", "train")
        assert _cut_pairs(snap) == [("left_door", "stcs_atp_duo_1")]

        # PUT replaces all-or-nothing: one invalid member keeps the old set.
        r = await c.put(
            "/api/trains/TRAIN001/links",
            json={
                "cuts": [
                    {"source": "cab_2", "target": "train"},
                    {"source": "ghost", "target": "train"},
                ]
            },
        )
        assert r.status_code == 400
        assert _cut_pairs(await _train(c)) == [("left_door", "stcs_atp_duo_1")]

        snap = await _replace(c, [])
        assert _cut_pairs(snap) == []


# -- Cut wires go stale, restores self-heal --------------------------------------


async def test_cut_door_feedback_wire_stales_mirrors_per_physical_link() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        await _cut(c, "left_door", "stcs_atp_duo_1")
        snap = await _command_equipment(c, "left_door", command="open")

        # duo_1's wire is cut: its door_state_1 mirror (bit 20) stays stale.
        assert _out_signal(snap, "stcs_atp_duo_1")[20] == "0"
        # The parallel physical wire to duo_2 delivers as always.
        assert _out_signal(snap, "stcs_atp_duo_2")[20] == "1"

        # Restore self-heals on the next delivery over the wire.
        await _restore(c, "left_door", "stcs_atp_duo_1")
        snap = await _command_equipment(c, "right_door", command="open")
        assert _out_signal(snap, "stcs_atp_duo_1")[20] == "1"
        assert _out_signal(snap, "stcs_atp_duo_1")[21] == "1"


async def test_cut_driver_to_train_wire_releases_the_legacy_lever() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        # An engaged driving system overwrites the zero legacy lever: motion.
        await _command_equipment(
            c, "driving_system_1", mode="traction", direction="forward", acceleration=1.0
        )
        await _step(c, 1.0)
        assert (await _train(c))["acceleration"] > 0.0

        # Cutting the handle's wire to "train" drops the driver intent, so the
        # zero lever applies again: constant speed, no more acceleration.
        await _cut(c, "driving_system_1", "train")
        await _step(c, 1.0)
        moving = await _train(c)
        assert moving["acceleration"] == 0.0
        assert moving["speed"] > 0.0

        await _restore(c, "driving_system_1", "train")
        await _step(c, 1.0)
        assert (await _train(c))["acceleration"] > 0.0


# -- Cab broadcasts are filtered per concrete recipient --------------------------


async def test_cut_cab_broadcast_freezes_only_the_blocked_recipient() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)

        # Activate cab 2 over a cut wire: native state moves, duo_2's
        # cab_activation mirror (bit 4) stays stale.
        await _cut(c, "cab_2", "stcs_atp_duo_2")
        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 2, "active": True})
        assert r.status_code == 200
        snap = r.json()
        assert {cab["cab_id"]: cab["active"] for cab in snap["cabs"]}[2] is True
        assert _out_signal(snap, "stcs_atp_duo_2")[4] == "0"

        # After the restore the next cab-2 broadcast self-heals both bits.
        await _restore(c, "cab_2", "stcs_atp_duo_2")
        r = await c.post("/api/trains/TRAIN001/commands", json={"cab_id": 2, "key": True})
        healed = _out_signal(r.json(), "stcs_atp_duo_2")
        assert healed[4] == "1"
        assert healed[17] == "1"  # key_activation (cab 2) now delivered too


# -- Cuts ride the WebSocket snapshot stream --------------------------------------


async def test_link_cuts_are_visible_on_the_websocket_stream() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        async with ws_connect(c.app, "/ws") as ws:
            await ws.receive_json()  # initial snapshot: no cuts
            await _cut(c, "left_door", "stcs_atp_duo_1")

            async def _next_with_cuts() -> dict:
                while True:
                    snap = await asyncio.wait_for(ws.receive_json(), 5.0)
                    if snap["trains"][0]["link_cuts"]:
                        return snap

            snap = await _next_with_cuts()
            assert _cut_pairs(snap["trains"][0]) == [("left_door", "stcs_atp_duo_1")]


# -- Reset clears the whole cut set ------------------------------------------------


async def test_reset_clears_all_link_cuts() -> None:
    async with running_app([T1]) as c:
        await _manual_start(c)
        await _cut(c, "left_door", "stcs_atp_duo_1")
        await _cut(c, "cab_2", "stcs_atp_duo_2")
        assert len(_cut_pairs(await _train(c))) == 2

        assert (await c.post("/api/simulation/reset")).status_code == 200
        snap = await _train(c)
        assert _cut_pairs(snap) == []

        # Wires are live again after reset.
        snap = await _command_equipment(c, "left_door", command="open")
        assert _out_signal(snap, "stcs_atp_duo_1")[20] == "1"
