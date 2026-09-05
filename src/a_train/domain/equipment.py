"""Doors, cabs, and BTM equipment behaviour (§3.5, §3.6, atp-api.md §3.2).

Every train-facing equipment capability is a narrow component behind the train
aggregate. A component keeps private mutable state, accepts plain-value
controls (e.g. ``apply_control("open")``), produces its own frozen snapshot via
``read_state``, and restores its configured state on ``reset``. It never
imports adapters, accesses the simulation clock, or modifies train physical
state directly. Adapters translate protocol data into equipment calls and
publish snapshot data.

Addon equipment (Cab, Door, BTM, StcsAtp, and future equipment) implements
the ``Equipment`` protocol: a ``type`` naming the behavior and a unique
``key`` naming the instance. ``EQUIPMENT_FACTORIES`` maps type to a factory
and may be invoked any number of times; a factory receives ``(key,
EquipmentContext, **params)``. The train stores instances by key and
iterates them generically. Components that need to observe core physics state
may also implement an optional ``step(dt, speed)`` hook, called generically by
the train after integration.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .controls import (
    BtmControl,
    CabControl,
    Control,
    DoorControl,
    EquipmentControl,
    StcsAtpControl,
    TrainControl,
)
from .snapshots import BtmSnapshot, CabSnapshot, DoorSnapshot, StcsAtpSnapshot

# -- Equipment protocol -------------------------------------------------------


@dataclass(frozen=True)
class EquipmentIntent:
    """A reference-free request carrying a target's control object.

    ``target`` is an equipment key or the reserved ``"train"`` target.
    Intent resolution is owned by the train aggregate; equipment only emits
    intents and never applies them to another component.
    """

    source: str
    target: str
    control: Control


@runtime_checkable
class Equipment(Protocol):
    """Common lifecycle interface for pluggable addon equipment instances."""

    @property
    def type(self) -> str:
        """Equipment type identifier (e.g. 'btm')."""
        ...

    @property
    def key(self) -> str:
        """Unique instance identity within one train."""
        ...

    def apply_control(self, control: EquipmentControl) -> None:
        """Apply a control owned by this equipment."""
        ...

    def read_state(self) -> Any:
        """Return a frozen snapshot of current equipment state."""
        ...

    def reset(self) -> None:
        """Restore configured initial state."""
        ...

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        """Report cross-equipment requests for the train to resolve."""
        ...


# -- Equipment factories ------------------------------------------------------

EQUIPMENT_FACTORIES: dict[str, Callable[..., Equipment]] = {}

# -- Door --------------------------------------------------------------------


class Door:
    """One train door's state. Door motion is instantaneous on ``apply_control``."""

    type = "door"

    def __init__(self, key: str, *, initial_state: str = "closed") -> None:
        if initial_state not in ("open", "closed"):
            raise ValueError(f"invalid initial door state: {initial_state!r}")
        self._key = key
        self._initial = initial_state
        self._state = initial_state

    @property
    def key(self) -> str:
        return self._key

    @property
    def closed(self) -> bool:
        return self._state == "closed"

    def apply_control(self, control: EquipmentControl) -> None:
        if not isinstance(control, DoorControl) or control.command not in ("open", "close"):
            raise ValueError("door control must be 'open' or 'close'")
        self._state = "open" if control.command == "open" else "closed"

    def read_state(self) -> DoorSnapshot:
        return DoorSnapshot(state=self._state)

    def reset(self) -> None:
        self._state = self._initial

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        return ()


# -- Cab ---------------------------------------------------------------------


class Cab:
    """One cab's local state: an activation flag with no control-side effect."""

    type = "cab"

    def __init__(self, key: str, cab_id: int, *, initial_active: bool = False) -> None:
        self._key = key
        self._cab_id = cab_id
        self._initial_active = initial_active
        self._active = initial_active

    @property
    def cab_id(self) -> int:
        return self._cab_id

    @property
    def key(self) -> str:
        return self._key

    @property
    def active(self) -> bool:
        return self._active

    def apply_control(self, control: EquipmentControl) -> None:
        if not isinstance(control, CabControl):
            raise ValueError("cab control is invalid")
        if control.command not in ("activate", "deactivate"):
            raise ValueError("cab control must be 'activate' or 'deactivate'")
        if control.cab_id is not None and control.cab_id != self._cab_id:
            raise ValueError("cab_id does not match equipment key")
        self._active = control.command == "activate"

    def read_state(self) -> CabSnapshot:
        return CabSnapshot(cab_id=self._cab_id, active=self._active)

    def reset(self) -> None:
        self._active = self._initial_active

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        return ()


# -- BTM ---------------------------------------------------------------------


class Btm:
    """Simulated BTM equipment for one cab. Payloads are opaque bytes."""

    type = "btm"

    def __init__(self, key: str, cab_id: int) -> None:
        self._key = key
        self._cab_id = cab_id
        self._pending: bytes | None = None
        self._received_count = 0

    @property
    def cab_id(self) -> int:
        return self._cab_id

    @property
    def key(self) -> str:
        return self._key

    def apply_control(self, control: EquipmentControl) -> None:
        if not isinstance(control, BtmControl):
            raise ValueError("btm control is invalid")
        if control.cab_id is not None and control.cab_id != self._cab_id:
            raise ValueError("cab_id does not match equipment key")
        self._pending = bytes(control.data)
        self._received_count += 1

    def read_state(self) -> BtmSnapshot:
        return BtmSnapshot(
            cab_id=self._cab_id,
            pending=self._pending is not None,
            payload_b64=(
                base64.b64encode(self._pending).decode("ascii")
                if self._pending is not None
                else None
            ),
            received_count=self._received_count,
        )

    def reset(self) -> None:
        self._pending = None
        self._received_count = 0

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        return ()


# -- ATP protection state -------------------------------------------------------


class StcsAtp:
    """Train-level ATP equipment that decodes ATP output bits into state.

    Bit zero is the leftmost character in the command. A command updates the
    logical states it contains; positions beyond its length retain their last
    value so shorter assertions do not clear unrelated outputs.
    """

    type = "stcs_atp"

    ATP_TO_TRAIN_SIGNAL_BY_BIT: dict[int, str] = {
        0: "emergency_brake_1",
        1: "emergency_brake_2",
        2: "maximum_service_brake_7",
        3: "ato_enable",
        4: "turnback_activation",
        5: "powerless_passed_command",
        6: "cut_off_traction",
        7: "service_brake_4",
        8: "service_brake_1",
        9: "open_left_door_permit_1",
        10: "open_left_door_permit_2",
        11: "open_right_door_permit_1",
        12: "open_right_door_permit_2",
        13: "powerless_passed_select",
        14: "c2_authorized",
        15: "c2_zero_speed",
        16: "turnback_indicator",
    }

    TRAIN_TO_ATP_SIGNAL_BY_BIT: dict[int, str] = {
        0: "emergency_brake_1_inner_feedback",
        1: "emergency_brake_2_inner_feedback",
        2: "emergency_brake_feedback",
        3: "service_brake_7_feedback",
        4: "cab_activation",
        5: "direction_handle_forward_1",
        6: "direction_handle_forward_2",
        7: "direction_handle_backward",
        8: "sleep_signal",
        9: "traction_handle_traction",
        10: "traction_handle_brake",
        11: "turnback_button",
        12: "turnback_activation_feedback",
        13: "left_door_open_button",
        14: "right_door_open_button",
        15: "left_door_close_button",
        16: "right_door_close_button",
        17: "key_activation",
        18: "left_door_open_permit_feedback",
        19: "right_door_open_permit_feedback",
        20: "door_state_1",
        21: "door_state_2",
        22: "cbtc_authorized_command",
        23: "c2_control_state_1_1",
        24: "c2_control_state_2_1",
        25: "system_switch_c2",
        26: "system_switch_auto",
        27: "system_switch_cbtc",
        28: "c2_control_state_1_2",
        29: "c2_control_state_2_2",
    }

    def __init__(self, key: str) -> None:
        self._key = key
        self._last_command: str | None = None
        self._train_out_states = {
            state_name: False for state_name in self.TRAIN_TO_ATP_SIGNAL_BY_BIT.values()
        }
        self._logical_states = {
            state_name: False for state_name in self.ATP_TO_TRAIN_SIGNAL_BY_BIT.values()
        }

    @property
    def key(self) -> str:
        return self._key

    def apply_control(self, control: EquipmentControl) -> None:
        """Apply a binary output command to the corresponding logical states."""
        if not isinstance(control, StcsAtpControl):
            raise ValueError("stcs_atp control is invalid")
        command = control.command
        if not isinstance(command, str) or not command or any(bit not in "01" for bit in command):
            raise ValueError("stcs_atp command must be a non-empty string of '0' and '1'")

        self._last_command = command
        for bit_index, bit in enumerate(command):
            state_name = self.ATP_TO_TRAIN_SIGNAL_BY_BIT.get(bit_index)
            if state_name is not None:
                self._logical_states[state_name] = bit == "1"
        self._update_train_out_states()

    def _update_train_out_states(self) -> None:
        self._train_out_states["emergency_brake_1_inner_feedback"] = (
            self._logical_states["emergency_brake_1"]
        )
        self._train_out_states["emergency_brake_2_inner_feedback"] = (
            self._logical_states["emergency_brake_2"]
        )
        self._train_out_states["emergency_brake_feedback"] = (
            self._logical_states["emergency_brake_1"]
            or self._logical_states["emergency_brake_2"]
        )
        self._train_out_states["service_brake_7_feedback"] = (
            self._logical_states["maximum_service_brake_7"]
        )

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        brake_active = self._logical_states["maximum_service_brake_7"]
        if not brake_active:
            return ()
        return (
            EquipmentIntent(
                source=self.key,
                target="train",
                control=TrainControl(
                    cab_id=1,
                    drive_demand=-1.0,
                ),
            ),
        )

    @property
    def logical_states(self) -> dict[str, bool]:
        """Return a copy of the decoded logical state, excluding snapshots."""
        return self._logical_states.copy()

    @property
    def train_out_states(self) -> dict[str, bool]:
        """Return the unprocessed train-output state shape."""
        return self._train_out_states.copy()

    def read_state(self) -> StcsAtpSnapshot:
        return StcsAtpSnapshot(
            last_command=self._last_command,
            train_out_signal="".join(
                "1"
                if self._train_out_states[state_name]
                else "0"
                for bit_index in range(len(self.TRAIN_TO_ATP_SIGNAL_BY_BIT))
                for state_name in (self.TRAIN_TO_ATP_SIGNAL_BY_BIT[bit_index],)
            ),
        )

    def reset(self) -> None:
        self._last_command = None
        for state_name in self._logical_states:
            self._logical_states[state_name] = False
        for state_name in self._train_out_states:
            self._train_out_states[state_name] = False


# -- Equipment factory registration -------------------------------------------


@dataclass(frozen=True, kw_only=True)
class EquipmentContext:
    """Train-scope configuration factories may need to build instances."""

    initial_door_state: str
    initial_active_cab: int


def _create_cab(
    key: str,
    ctx: EquipmentContext,
    *,
    initial_active: bool | None = None,
) -> Cab:
    cab_id = int(key.removeprefix("cab_"))
    if initial_active is None:
        initial_active = cab_id == ctx.initial_active_cab
    return Cab(key, cab_id, initial_active=initial_active)


def _create_door(
    key: str,
    ctx: EquipmentContext,
    *,
    initial_state: str | None = None,
) -> Door:
    if initial_state is None:
        initial_state = ctx.initial_door_state
    return Door(key, initial_state=initial_state)


def _create_btm(key: str, _ctx: EquipmentContext) -> Btm:
    return Btm(key, int(key.removeprefix("btm_")))


def _create_stcs_atp(key: str, _ctx: EquipmentContext) -> StcsAtp:
    return StcsAtp(key)


EQUIPMENT_FACTORIES["cab"] = _create_cab
EQUIPMENT_FACTORIES["door"] = _create_door
EQUIPMENT_FACTORIES["btm"] = _create_btm
EQUIPMENT_FACTORIES["stcs_atp"] = _create_stcs_atp
