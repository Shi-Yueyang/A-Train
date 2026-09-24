"""Switch-box equipment: a three-position system selector feeds one cab's STCS bits.

Covers config-only installation, one-hot cab-scoped mirroring, box authority
over operator assertions with wire-cut freeze/self-heal, and reset -- all
through the public API against the real application (§6.1).
"""

from __future__ import annotations

from a_train.domain.train import EquipmentConfig, TrainConfig
from tests.support.app import running_app

SWITCH_ROLES = ("system_switch_c2", "system_switch_auto", "system_switch_cbtc")

T2 = TrainConfig(
    train_id="TRAIN001",
    cab_ids=(1, 2),
    initial_active_cab=1,
    max_traction_accel=1.0,
    max_decel=2.0,
    equipment_configs=(
        EquipmentConfig("door", "left_door", {"side": "left"}),
        EquipmentConfig("door", "right_door", {"side": "right"}),
        *[EquipmentConfig("btm", f"btm_{cab}", {"cab_id": cab}) for cab in (1, 2)],
        *[
            EquipmentConfig("driving_system", f"driving_system_{cab}", {"cab_id": cab})
            for cab in (1, 2)
        ],
        *[
            EquipmentConfig("stcs_atp_duo", f"stcs_atp_duo_{cab}", {"cab_id": cab})
            for cab in (1, 2)
        ],
        # Only cab 1 is fitted with a system-selection box.
        EquipmentConfig("switch_box", "switch_box_1", {"cab_id": 1}),
    ),
)


async def _manual_start(c) -> None:
    await c.post("/api/simulation/time-mode", json={"mode": "MANUAL"})
    await c.post("/api/simulation/start")


def _state(snap: dict, key: str) -> dict:
    return next(e["state"] for e in snap["equipment"] if e["key"] == key)


def _switch_bits(snap: dict, key: str = "stcs_atp_duo_1") -> dict[str, bool]:
    return {
        s["name"]: s["value"]
        for s in _state(snap, key)["train_out_states"]
        if s["name"] in SWITCH_ROLES
    }


async def test_fitted_box_feeds_one_hot_system_bits_at_startup() -> None:
    async with running_app([T2]) as c:
        await _manual_start(c)
        snap = (await c.get("/api/trains/TRAIN001")).json()

        assert _state(snap, "switch_box_1") == {"cab_id": 1, "position": "c2"}
        assert _switch_bits(snap) == {
            "system_switch_c2": True,
            "system_switch_auto": False,
            "system_switch_cbtc": False,
        }
        # An unfitted cab leaves the roles unasserted panel bits.
        assert _switch_bits(snap, "stcs_atp_duo_2") == dict.fromkeys(SWITCH_ROLES, False)


async def test_position_changes_mirror_to_the_matching_cab_only() -> None:
    async with running_app([T2]) as c:
        await _manual_start(c)

        r = await c.post(
            "/api/trains/TRAIN001/equipment/switch_box_1", json={"system_switch": "cbtc"}
        )
        assert r.status_code == 200
        snap = r.json()
        assert _switch_bits(snap) == {
            "system_switch_c2": False,
            "system_switch_auto": False,
            "system_switch_cbtc": True,
        }
        # Roles 25-27 of the train-out bit string reach the ATP wire.
        assert _state(snap, "stcs_atp_duo_1")["train_out_signal"][25:28] == "001"
        assert _switch_bits(snap, "stcs_atp_duo_2") == dict.fromkeys(SWITCH_ROLES, False)

        r = await c.post(
            "/api/trains/TRAIN001/equipment/switch_box_1", json={"system_switch": "auto"}
        )
        assert r.status_code == 200
        assert _switch_bits(r.json())["system_switch_auto"] is True

        r = await c.post(
            "/api/trains/TRAIN001/equipment/switch_box_1", json={"system_switch": "rrr"}
        )
        assert r.status_code == 400
        assert "switch box position" in r.json()["detail"]
        r = await c.post("/api/trains/TRAIN001/equipment/switch_box_1", json={})
        assert r.status_code == 400  # system_switch required


async def test_box_state_is_authoritative_and_wire_cut_freezes_the_mirror() -> None:
    async with running_app([T2]) as c:
        await _manual_start(c)

        # An operator assertion of a box-fed bit is re-established by the box
        # within the same command (the intent resolver redelivers its state).
        zero = "0" * 28
        r = await c.post(
            "/api/trains/TRAIN001/equipment/stcs_atp_duo_1", json={"train_out_signal": zero}
        )
        assert r.status_code == 200
        assert _switch_bits(r.json())["system_switch_c2"] is True

        # Cut the box wire: the mirror is stale-not-zeroed and drives manually.
        r = await c.post(
            "/api/trains/TRAIN001/links/cut",
            json={"source": "switch_box_1", "target": "stcs_atp_duo_1"},
        )
        assert r.status_code == 200
        r = await c.post(
            "/api/trains/TRAIN001/equipment/stcs_atp_duo_1", json={"train_out_signal": zero}
        )
        assert _switch_bits(r.json()) == dict.fromkeys(SWITCH_ROLES, False)

        # Restoring the wire self-heals on the next delivery.
        r = await c.delete(
            "/api/trains/TRAIN001/links/cut?source=switch_box_1&target=stcs_atp_duo_1"
        )
        assert r.status_code == 200
        r = await c.post("/api/trains/TRAIN001/equipment/stcs_atp_duo_1", json={"command": "0"})
        assert _switch_bits(r.json())["system_switch_c2"] is True


async def test_reset_restores_the_box_default_position() -> None:
    async with running_app([T2]) as c:
        await _manual_start(c)
        await c.post("/api/trains/TRAIN001/equipment/switch_box_1", json={"system_switch": "cbtc"})
        await c.post("/api/simulation/reset")
        snap = (await c.get("/api/trains/TRAIN001")).json()
        assert _state(snap, "switch_box_1")["position"] == "c2"
        assert _switch_bits(snap)["system_switch_c2"] is True
