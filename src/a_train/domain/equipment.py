"""Doors, BTM, ATP protection, and driving-system equipment behaviour (§3.5).

Every train-facing equipment capability is a narrow component behind the train
aggregate. A component keeps private mutable state, accepts plain-value
controls (e.g. ``apply_control("open")``), produces its own frozen snapshot via
``read_state``, and restores its configured state on ``reset``. It never
imports adapters, accesses the simulation clock, or modifies train physical
state directly; components that act on the train (driving systems, ATP
protection brake) do so exclusively through ``train``-target intents resolved
by the aggregate. Adapters translate protocol data into equipment calls and
publish snapshot data.

Addon equipment (Door, BTM, StcsAtp, DrivingSystem, and future equipment)
implements the ``Equipment`` protocol: a ``type`` naming the behavior and a
unique ``key`` naming the instance. ``EQUIPMENT_FACTORIES`` maps type to a
factory and may be invoked any number of times; a factory receives ``(key,
EquipmentContext, **params)``. The train stores instances by key and
iterates them generically. Components that need to observe core physics state
may also implement an optional ``step(dt, speed)`` hook, called generically by
the train after integration.
"""

from __future__ import annotations

import base64
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from .controls import (
    BtmControl,
    Control,
    DoorControl,
    DriverControl,
    DrivingSystemControl,
    EquipmentControl,
    StcsAtpControl,
)
from .snapshots import (
    BtmSnapshot,
    DoorSnapshot,
    DrivingSystemSnapshot,
    SignalState,
    StcsAtpSnapshot,
)

# -- Equipment protocol -------------------------------------------------------

EquipmentControlT = TypeVar("EquipmentControlT", bound=EquipmentControl)


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
class Equipment(Protocol[EquipmentControlT]):
    """Common lifecycle interface for pluggable addon equipment instances.

    The protocol is generic in the component's own control type: a component
    accepts exactly its declared control, which is also its complete writable
    surface. Instances are stored by key and dispatched dynamically, so the
    aggregate holds ``Equipment[Any]`` and each component keeps a runtime
    type guard as the boundary check.
    """

    @property
    def type(self) -> str:
        """Equipment type identifier (e.g. 'btm')."""
        ...

    @property
    def key(self) -> str:
        """Unique instance identity within one train."""
        ...

    def apply_control(self, control: EquipmentControlT) -> None:
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

EQUIPMENT_FACTORIES: dict[str, Callable[..., Equipment[Any]]] = {}

# -- Door --------------------------------------------------------------------


class Door:
    """One train door's state. Door motion is instantaneous on ``apply_control``.

    A door with a known ``side`` ("left" or "right") reports its current
    state as an intent to the train-level ``stcs_atp`` feedback; the aggregate
    resolves it, so the door never references another component.
    """

    type = "door"

    def __init__(self, key: str, *, initial_state: str = "closed", side: str | None = None) -> None:
        if initial_state not in ("open", "closed"):
            raise ValueError(f"invalid initial door state: {initial_state!r}")
        if side not in (None, "left", "right"):
            raise ValueError(f"invalid door side: {side!r}")
        self._key = key
        self._side = side
        self._initial = initial_state
        self._state = initial_state

    @property
    def key(self) -> str:
        return self._key

    @property
    def closed(self) -> bool:
        return self._state == "closed"

    def apply_control(self, control: DoorControl) -> None:
        if not isinstance(control, DoorControl) or control.command not in ("open", "close"):
            raise ValueError("door control must be 'open' or 'close'")
        self._state = "open" if control.command == "open" else "closed"

    def read_state(self) -> DoorSnapshot:
        return DoorSnapshot(state=self._state)

    def reset(self) -> None:
        self._state = self._initial

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        if self._side is None:
            return ()
        opened = self._state == "open"
        if self._side == "left":
            control = StcsAtpControl(left_door_open=opened)
        else:
            control = StcsAtpControl(right_door_open=opened)
        return (EquipmentIntent(source=self._key, target="stcs_atp", control=control),)


# -- Driving system -----------------------------------------------------------


class DrivingSystem:
    """One cab's driver room: mode, direction, and acceleration handles.

    The mode handle selects ``"traction"``, ``"off"``, or ``"brake"``; the
    direction handle selects ``"forward"``, ``"off"``, or ``"backward"``
    relative to the cab's track facing; the acceleration handle is a
    continuous effort in ``[0.0, 1.0]``. The handles have no interlock: each
    position is settable independently, and the ``"off"`` position of the
    mode handle means "no driver request" (the legacy drive-demand lever then
    applies again).

    While the mode handle is engaged the system acts on the train through a
    ``train``-target intent carrying the resolved ``DriverControl``: traction
    is mapped from the cab-relative handle through the facing into
    track-relative signed effort; brake effort always opposes the current
    motion and is independent of the direction handle. Every handle also
    feeds the train-level ``stcs_atp`` direction/traction feedback bits
    through an ``stcs_atp``-target intent.
    """

    type = "driving_system"

    MODES = ("traction", "off", "brake")
    DIRECTIONS = ("forward", "off", "backward")

    _HANDLE_TO_TRACK_SIGN = {"forward": 1, "off": 0, "backward": -1}

    def __init__(
        self,
        key: str,
        *,
        cab_id: int,
        facing: int,
        initial_mode: str = "off",
        initial_direction: str = "off",
        initial_acceleration: float = 0.0,
    ) -> None:
        if initial_mode not in self.MODES:
            raise ValueError(f"invalid initial mode: {initial_mode!r}")
        if initial_direction not in self.DIRECTIONS:
            raise ValueError(f"invalid initial direction: {initial_direction!r}")
        if facing not in (-1, 1):
            raise ValueError("facing must be +1 (track-increasing) or -1")
        self._key = key
        self._cab_id = cab_id
        self._facing = facing
        self._mode = initial_mode
        self._direction = initial_direction
        self._acceleration = initial_acceleration

    @property
    def key(self) -> str:
        return self._key

    @property
    def cab_id(self) -> int:
        return self._cab_id

    def apply_control(self, control: DrivingSystemControl) -> None:
        """Set any subset of the three handles; invalid input changes nothing."""
        if not isinstance(control, DrivingSystemControl):
            raise ValueError("driving system control is invalid")
        if control.mode is not None and control.mode not in self.MODES:
            raise ValueError(f"driving mode must be one of {self.MODES}, got {control.mode!r}")
        if control.direction is not None and control.direction not in self.DIRECTIONS:
            raise ValueError(
                f"driving direction must be one of {self.DIRECTIONS}, got {control.direction!r}"
            )
        acceleration = control.acceleration
        if acceleration is not None:
            if (
                isinstance(acceleration, bool)
                or not isinstance(acceleration, (int, float))
                or not math.isfinite(acceleration)
                or not 0.0 <= acceleration <= 1.0
            ):
                raise ValueError("driving acceleration must be a finite value in [0.0, 1.0]")
        self._mode = control.mode if control.mode is not None else self._mode
        self._direction = control.direction if control.direction is not None else self._direction
        self._acceleration = float(acceleration) if acceleration is not None else self._acceleration

    def read_state(self) -> DrivingSystemSnapshot:
        return DrivingSystemSnapshot(
            cab_id=self._cab_id,
            facing="forward" if self._facing == 1 else "backward",
            mode=self._mode,
            direction=self._direction,
            acceleration=self._acceleration,
        )

    def reset(self) -> None:
        self._mode = "off"
        self._direction = "off"
        self._acceleration = 0.0

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        intents: list[EquipmentIntent] = [
            EquipmentIntent(
                source=self._key,
                target="stcs_atp",
                control=StcsAtpControl(
                    cab_id=self._cab_id, mode=self._mode, direction=self._direction
                ),
            )
        ]
        if self._mode != "off":
            if self._mode == "traction":
                traction = (
                    self._facing * self._HANDLE_TO_TRACK_SIGN[self._direction] * self._acceleration
                )
                brake = 0.0
            else:
                traction = 0.0
                brake = self._acceleration
            intents.append(
                EquipmentIntent(
                    source=self._key,
                    target="train",
                    control=DriverControl(cab_id=self._cab_id, traction=traction, brake=brake),
                )
            )
        return tuple(intents)


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

    def apply_control(self, control: BtmControl) -> None:
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
    train-in states it contains; positions beyond its length retain their last
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
        self._handles: dict[int, tuple[str, str]] = {}
        self._train_out_states = {
            state_name: False for state_name in self.TRAIN_TO_ATP_SIGNAL_BY_BIT.values()
        }
        self._train_in_states = {
            state_name: False for state_name in self.ATP_TO_TRAIN_SIGNAL_BY_BIT.values()
        }

    @property
    def key(self) -> str:
        return self._key

    def apply_control(self, control: StcsAtpControl) -> None:
        """Apply the stcs_atp control: feedback fields and/or ATP bit string.

        Door and driving-system handle fields arrive through intents; the
        bit string arrives from the ATP adapter. Every input refreshes the
        derived train-out feedback bits.
        """
        if not isinstance(control, StcsAtpControl):
            raise ValueError("stcs_atp control is invalid")
        if control.left_door_open is not None:
            self._train_out_states["door_state_1"] = control.left_door_open
        if control.right_door_open is not None:
            self._train_out_states["door_state_2"] = control.right_door_open
        if control.direction is not None or control.mode is not None:
            self._apply_handle_feedback(control)
        command = control.command
        if command is not None:
            if (
                not isinstance(command, str)
                or not command
                or any(bit not in "01" for bit in command)
            ):
                raise ValueError("stcs_atp command must be a non-empty string of '0' and '1'")
            self._last_command = command
            for bit_index, bit in enumerate(command):
                state_name = self.ATP_TO_TRAIN_SIGNAL_BY_BIT.get(bit_index)
                if state_name is not None:
                    self._train_in_states[state_name] = bit == "1"
        self._update_train_out_states()

    def _apply_handle_feedback(self, control: StcsAtpControl) -> None:
        if control.cab_id is None:
            raise ValueError("driving-system feedback requires cab_id")
        current_mode, current_direction = self._handles.get(control.cab_id, ("off", "off"))
        mode = control.mode if control.mode is not None else current_mode
        direction = control.direction if control.direction is not None else current_direction
        if mode not in ("traction", "off", "brake"):
            raise ValueError(f"invalid driving mode feedback: {mode!r}")
        if direction not in ("forward", "off", "backward"):
            raise ValueError(f"invalid driving direction feedback: {direction!r}")
        self._handles[control.cab_id] = (mode, direction)

    def _update_train_out_states(self) -> None:
        self._train_out_states["emergency_brake_1_inner_feedback"] = self._train_in_states[
            "emergency_brake_1"
        ]
        self._train_out_states["emergency_brake_2_inner_feedback"] = self._train_in_states[
            "emergency_brake_2"
        ]
        self._train_out_states["emergency_brake_feedback"] = (
            self._train_in_states["emergency_brake_1"] or self._train_in_states["emergency_brake_2"]
        )
        self._train_out_states["service_brake_7_feedback"] = self._train_in_states[
            "maximum_service_brake_7"
        ]
        modes = {cab: handle[0] for cab, handle in self._handles.items()}
        directions = {cab: handle[1] for cab, handle in self._handles.items()}
        self._train_out_states["direction_handle_forward_1"] = directions.get(1, "off") == "forward"
        self._train_out_states["direction_handle_forward_2"] = directions.get(2, "off") == "forward"
        self._train_out_states["direction_handle_backward"] = "backward" in directions.values()
        self._train_out_states["traction_handle_traction"] = "traction" in modes.values()
        self._train_out_states["traction_handle_brake"] = "brake" in modes.values()

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        if not self._train_in_states["maximum_service_brake_7"]:
            return ()
        return (
            EquipmentIntent(
                source=self._key,
                target="train",
                control=DriverControl(brake=1.0),
            ),
        )

    @property
    def train_in_states(self) -> dict[str, bool]:
        """Return a copy of the decoded train-in state, excluding snapshots."""
        return self._train_in_states.copy()

    @property
    def train_out_states(self) -> dict[str, bool]:
        """Return the unprocessed train-output state shape."""
        return self._train_out_states.copy()

    def read_state(self) -> StcsAtpSnapshot:
        train_out = tuple(
            SignalState(name=name, value=self._train_out_states[name])
            for name in self.TRAIN_TO_ATP_SIGNAL_BY_BIT.values()
        )
        return StcsAtpSnapshot(
            last_command=self._last_command,
            train_out_signal="".join("1" if signal.value else "0" for signal in train_out),
            train_in_states=tuple(
                SignalState(name=name, value=self._train_in_states[name])
                for name in self.ATP_TO_TRAIN_SIGNAL_BY_BIT.values()
            ),
            train_out_states=train_out,
        )

    def reset(self) -> None:
        self._last_command = None
        self._handles.clear()
        for state_name in self._train_in_states:
            self._train_in_states[state_name] = False
        for state_name in self._train_out_states:
            self._train_out_states[state_name] = False


# -- Equipment factory registration -------------------------------------------


@dataclass(frozen=True, kw_only=True)
class EquipmentContext:
    """Train-scope configuration factories may need to build instances."""

    initial_door_state: str
    cab_facings: dict[int, int] = field(default_factory=dict)


def _create_door(
    key: str,
    ctx: EquipmentContext,
    *,
    initial_state: str | None = None,
    side: str | None = None,
) -> Door:
    if initial_state is None:
        initial_state = ctx.initial_door_state
    if side is None:
        prefix = key.split("_", 1)[0]
        side = prefix if prefix in ("left", "right") else None
    return Door(key, initial_state=initial_state, side=side)


def _create_btm(key: str, _ctx: EquipmentContext) -> Btm:
    return Btm(key, int(key.removeprefix("btm_")))


def _create_stcs_atp(key: str, _ctx: EquipmentContext) -> StcsAtp:
    return StcsAtp(key)


def _create_driving_system(
    key: str,
    ctx: EquipmentContext,
    *,
    facing: int | None = None,
    initial_mode: str = "off",
    initial_direction: str = "off",
    initial_acceleration: float = 0.0,
) -> DrivingSystem:
    cab_id = int(key.removeprefix("driving_"))
    if facing is None:
        facing = ctx.cab_facings[cab_id]
    return DrivingSystem(
        key,
        cab_id=cab_id,
        facing=facing,
        initial_mode=initial_mode,
        initial_direction=initial_direction,
        initial_acceleration=initial_acceleration,
    )


EQUIPMENT_FACTORIES["door"] = _create_door
EQUIPMENT_FACTORIES["btm"] = _create_btm
EQUIPMENT_FACTORIES["stcs_atp"] = _create_stcs_atp
EQUIPMENT_FACTORIES["driving_system"] = _create_driving_system
