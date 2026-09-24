"""STCS ATP protection state and train-output feedback.

The component maintains two protocol bit maps. Train-in bits are asserted
verbatim by the ATP ``command`` string. The train-out map stores only what has
been *asserted* into it (door, cab, driving-handle, and switch-box mirrors,
operator panels, and settled blocks); ``derive`` declares STCS's own internal
logic -- boolean operations over signals the component already holds -- and is
evaluated as a pure fold over the store at read time, so mutation paths carry
no recompute obligation.
Duo and solo variants differ only by their tables.

A derived row is *blockable*: blocking settles its current derived value into
the store (the one timing decision in the component) and takes it out of the
read-time fold, so operator ``train_out_signal`` assertions stick; unblocking
lets the rule shine through again immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..controls import CabStateControl, DriverControl, StcsAtpControl
from ..snapshots import SignalState, StcsAtpSnapshot
from .base import EquipmentIntent


@dataclass(frozen=True)
class Invert:
    """Derived train-out signal: ``not`` one named signal of either map."""

    source: str


@dataclass(frozen=True)
class Or:
    """Derived train-out signal: ``any of`` the named signals."""

    sources: tuple[str, ...]


@dataclass(frozen=True)
class Follows:
    """Derived train-out signal: an exact copy of another signal STCS holds."""

    source: str


@dataclass(frozen=True)
class SignalDefinition:
    """One named boolean signal at a protocol-defined bit position.

    ``derive`` declares the rule that derives this bit's value at read time;
    rows without a rule are plain asserted signals the operator owns.
    """

    name: str
    default: bool = False
    derive: Invert | Or | Follows | None = None

    @property
    def blockable(self) -> bool:
        return self.derive is not None


# Train-out signals the simulator derives from other state. Duo and solo
# variants share these rows; a future variant declares its own derivations.
_EB1_FEEDBACK = SignalDefinition(
    "emergency_brake_1_inner_feedback", derive=Invert("emergency_brake_1")
)
_EB2_FEEDBACK = SignalDefinition(
    "emergency_brake_2_inner_feedback", derive=Invert("emergency_brake_2")
)
_EB_FEEDBACK = SignalDefinition(
    "emergency_brake_feedback", derive=Or(("emergency_brake_1", "emergency_brake_2"))
)
_SB7_FEEDBACK = SignalDefinition(
    "service_brake_7_feedback", derive=Follows("maximum_service_brake_7")
)
_SLEEP = SignalDefinition("sleep_signal", derive=Invert("cab_activation"))

# Duo-only C2/CBTC control-state groups: group 1 is high with the system
# switch on C2, group 2 with CBTC, and all four low on AUTO or a frozen/
# unpowered switch feed. Identity copies of the switch bits, so the fold
# tracks every path that moves them: box feed, operator asserts on the
# switch rows, blocks, and reset.
_C2_STATE_1_1 = SignalDefinition("c2_control_state_1_1", derive=Follows("system_switch_c2"))
_C2_STATE_1_2 = SignalDefinition("c2_control_state_1_2", derive=Follows("system_switch_c2"))
_C2_STATE_2_1 = SignalDefinition("c2_control_state_2_1", derive=Follows("system_switch_cbtc"))
_C2_STATE_2_2 = SignalDefinition("c2_control_state_2_2", derive=Follows("system_switch_cbtc"))

# Handle-mirror rows are asserted at ingest from the driving system's feedback
# intent (see ``_apply_handle_feedback``), not derived from STCS's own signal
# maps: plain signals, ``blockable`` false, and the freeze lever is the
# physical wire cut ``driving_system_<cab> -> stcs_atp_duo_<cab>`` (§3.7).
# The forward pair are duplicate bit wires of a single signal — both rows
# always carry one value; the ``_1``/``_2`` suffix is not a cab index.
_DIRECTION_FORWARD_1 = SignalDefinition("direction_handle_forward_1")
_DIRECTION_FORWARD_2 = SignalDefinition("direction_handle_forward_2")
_DIRECTION_BACKWARD = SignalDefinition("direction_handle_backward")
_HANDLE_TRACTION = SignalDefinition("traction_handle_traction")
_HANDLE_BRAKE = SignalDefinition("traction_handle_brake")


class StcsAtpBase:
    """Shared lifecycle and feedback behavior for STCS ATP variants."""

    type: ClassVar[str]
    ATP_TO_TRAIN_SIGNALS: ClassVar[tuple[SignalDefinition, ...]]
    TRAIN_TO_ATP_SIGNALS: ClassVar[tuple[SignalDefinition, ...]]

    def __init__(self, key: str, *, cab_id: int) -> None:
        self._key = key
        self._cab_id = cab_id
        self._last_command: str | None = None
        self._last_command_time: float | None = None
        self._handles: dict[int, tuple[str, str]] = {}
        self._blocked: set[str] = set()
        self._train_out_states = {
            signal.name: signal.default for signal in self.TRAIN_TO_ATP_SIGNALS
        }
        self._train_in_states = {
            signal.name: signal.default for signal in self.ATP_TO_TRAIN_SIGNALS
        }

    @property
    def key(self) -> str:
        return self._key

    @property
    def cab_id(self) -> int:
        return self._cab_id

    def apply_control(self, control: StcsAtpControl, *, received_at: float | None = None) -> None:
        if not isinstance(control, StcsAtpControl):
            raise ValueError("stcs_atp control is invalid")
        if control.block is not None or control.unblock is not None:
            self._apply_blocks(control.block, control.unblock)
        if control.left_door_open is not None:
            self._set_train_out_state("door_state_1", control.left_door_open)
        if control.right_door_open is not None:
            self._set_train_out_state("door_state_2", control.right_door_open)
        if control.direction is not None or control.mode is not None:
            self._apply_handle_feedback(control)
        if control.system_switch is not None:
            self._apply_system_switch(control.system_switch)
        if control.command is not None:
            if (
                not isinstance(control.command, str)
                or not control.command
                or any(bit not in "01" for bit in control.command)
            ):
                raise ValueError("stcs_atp command must be a non-empty string of '0' and '1'")
            self._last_command = control.command
            self._last_command_time = received_at
            for signal, bit in zip(self.ATP_TO_TRAIN_SIGNALS, control.command):
                self._train_in_states[signal.name] = bit == "1"
        if control.train_out_signal is not None:
            bits = control.train_out_signal
            if not isinstance(bits, str) or not bits or any(bit not in "01" for bit in bits):
                raise ValueError(
                    "stcs_atp train_out_signal must be a non-empty string of '0' and '1'"
                )
            for signal, bit in zip(self.TRAIN_TO_ATP_SIGNALS, bits):
                self._train_out_states[signal.name] = bit == "1"

    def _apply_blocks(self, block: tuple[str, ...] | None, unblock: tuple[str, ...] | None) -> None:
        block_names = tuple(block or ())
        unblock_names = tuple(unblock or ())
        for name in block_names + unblock_names:
            if not self._train_out_definition(name).blockable:
                raise ValueError(f"stcs_atp signal is not blockable: {name}")
        if block_names:
            settled = self._effective_train_out_states()
            for name in block_names:
                # Only a newly blocked row settles: re-blocking keeps an
                # already-frozen (possibly manual) value untouched.
                if name not in self._blocked:
                    self._train_out_states[name] = settled[name]
        self._blocked.update(block_names)
        self._blocked.difference_update(unblock_names)

    def _train_out_definition(self, name: str) -> SignalDefinition:
        for definition in self.TRAIN_TO_ATP_SIGNALS:
            if definition.name == name:
                return definition
        raise ValueError(f"unknown stcs_atp signal: {name}")

    def _set_train_out_state(self, name: str, value: bool) -> None:
        if name in self._train_out_states:
            self._train_out_states[name] = value

    def observe_cab_state(self, state: CabStateControl) -> None:
        if state.cab_id != self._cab_id:
            return
        self._set_train_out_state("cab_activation", state.active)
        self._set_train_out_state("key_activation", state.key_inserted)

    def _apply_handle_feedback(self, control: StcsAtpControl) -> None:
        cab_id = control.cab_id if control.cab_id is not None else self._cab_id
        current_mode, current_direction = self._handles.get(cab_id, ("off", "off"))
        mode = control.mode if control.mode is not None else current_mode
        direction = control.direction if control.direction is not None else current_direction
        if mode not in ("traction", "off", "brake"):
            raise ValueError(f"invalid driving mode feedback: {mode!r}")
        if direction not in ("forward", "off", "backward"):
            raise ValueError(f"invalid driving direction feedback: {direction!r}")
        self._handles[cab_id] = (mode, direction)

        self._set_train_out_state("direction_handle_forward_1", direction == "forward")
        self._set_train_out_state("direction_handle_forward_2", direction == "forward")
        self._set_train_out_state("direction_handle_backward", direction == "backward")
        self._set_train_out_state("traction_handle_traction", mode == "traction")
        self._set_train_out_state("traction_handle_brake", mode == "brake")

    def _apply_system_switch(self, position: str) -> None:
        if position not in ("c2", "auto", "cbtc"):
            raise ValueError(f"invalid system switch position feedback: {position!r}")
        # One-hot mirror of a fitted cab box; a layout without the rows
        # (solo) silently ignores the asserts via the store guard.
        self._set_train_out_state("system_switch_c2", position == "c2")
        self._set_train_out_state("system_switch_auto", position == "auto")
        self._set_train_out_state("system_switch_cbtc", position == "cbtc")

    def _effective_train_out_states(self) -> dict[str, bool]:
        """The train-out map as seen by every reader.

        The store keeps asserted values; unblocked derived rows are folded
        over it in table (declaration) order, so a rule may reference an
        earlier output row as well as any train-in bit. Pure: derives on
        read, mutates nothing.
        """
        out = dict(self._train_out_states)
        for definition in self.TRAIN_TO_ATP_SIGNALS:
            if definition.derive is not None and definition.name not in self._blocked:
                out[definition.name] = self._evaluate(definition.derive, out)
        return out

    def _evaluate(self, rule: Invert | Or | Follows, out: dict[str, bool]) -> bool:
        if isinstance(rule, Follows):
            return self._bit(rule.source, out)
        if isinstance(rule, Invert):
            return not self._bit(rule.source, out)
        return any(self._bit(source, out) for source in rule.sources)

    def _bit(self, name: str, out: dict[str, bool]) -> bool:
        if name in self._train_in_states:
            return self._train_in_states[name]
        return out.get(name, False)

    def emit_intents(self) -> tuple[EquipmentIntent, ...]:
        intents: list[EquipmentIntent] = []
        if self._train_in_states.get("maximum_service_brake_7", False):
            intents.append(
                EquipmentIntent(
                    source=self._key,
                    target="train",
                    control=DriverControl(brake=1.0),
                )
            )
        intents.extend(self._emit_variant_intents())
        return tuple(intents)

    def _emit_variant_intents(self) -> tuple[EquipmentIntent, ...]:
        """Allow a variant to emit additional train-facing intents."""
        return ()

    @property
    def train_in_states(self) -> dict[str, bool]:
        return self._train_in_states.copy()

    @property
    def train_out_states(self) -> dict[str, bool]:
        return self._effective_train_out_states()

    def read_state(self) -> StcsAtpSnapshot:
        effective = self._effective_train_out_states()
        train_out = tuple(
            SignalState(
                name=definition.name,
                value=effective[definition.name],
                blockable=definition.blockable,
                blocked=definition.name in self._blocked,
            )
            for definition in self.TRAIN_TO_ATP_SIGNALS
        )
        return StcsAtpSnapshot(
            last_command=self._last_command,
            last_command_time=self._last_command_time,
            train_out_signal="".join("1" if signal.value else "0" for signal in train_out),
            train_in_states=tuple(
                SignalState(name=name, value=self._train_in_states[name])
                for name in (signal.name for signal in self.ATP_TO_TRAIN_SIGNALS)
            ),
            train_out_states=train_out,
        )

    def reset(self) -> None:
        self._last_command = None
        self._last_command_time = None
        self._handles.clear()
        self._blocked.clear()
        self._train_in_states = {
            signal.name: signal.default for signal in self.ATP_TO_TRAIN_SIGNALS
        }
        self._train_out_states = {
            signal.name: signal.default for signal in self.TRAIN_TO_ATP_SIGNALS
        }


class StcsAtpDuo(StcsAtpBase):
    """Duo STCS ATP signal layout."""

    type = "stcs_atp_duo"
    ATP_TO_TRAIN_SIGNALS = (
        SignalDefinition("emergency_brake_1"),
        SignalDefinition("emergency_brake_2"),
        SignalDefinition("maximum_service_brake_7"),
        SignalDefinition("ato_enable"),
        SignalDefinition("turnback_activation"),
        SignalDefinition("powerless_passed_command"),
        SignalDefinition("cut_off_traction"),
        SignalDefinition("service_brake_4"),
        SignalDefinition("service_brake_1"),
        SignalDefinition("open_left_door_permit_1"),
        SignalDefinition("open_left_door_permit_2"),
        SignalDefinition("open_right_door_permit_1"),
        SignalDefinition("open_right_door_permit_2"),
        SignalDefinition("powerless_passed_select"),
        SignalDefinition("c2_authorized"),
        SignalDefinition("c2_zero_speed"),
        SignalDefinition("turnback_indicator"),
    )
    TRAIN_TO_ATP_SIGNALS = (
        _EB1_FEEDBACK,
        _EB2_FEEDBACK,
        _EB_FEEDBACK,
        _SB7_FEEDBACK,
        SignalDefinition("cab_activation"),
        _DIRECTION_FORWARD_1,
        _DIRECTION_FORWARD_2,
        _DIRECTION_BACKWARD,
        _SLEEP,
        _HANDLE_TRACTION,
        _HANDLE_BRAKE,
        SignalDefinition("turnback_button"),
        SignalDefinition("turnback_activation_feedback"),
        SignalDefinition("left_door_open_button"),
        SignalDefinition("right_door_open_button"),
        SignalDefinition("left_door_close_button"),
        SignalDefinition("right_door_close_button"),
        SignalDefinition("key_activation"),
        SignalDefinition("left_door_open_permit_feedback"),
        SignalDefinition("right_door_open_permit_feedback"),
        SignalDefinition("door_state_1"),
        SignalDefinition("door_state_2"),
        SignalDefinition("cbtc_authorized_command"),
        _C2_STATE_1_1,
        _C2_STATE_2_1,
        SignalDefinition("system_switch_c2"),
        SignalDefinition("system_switch_auto"),
        SignalDefinition("system_switch_cbtc"),
        _C2_STATE_1_2,
        _C2_STATE_2_2,
    )


class StcsAtpSolo(StcsAtpBase):
    """C2+ATO solo STCS ATP signal layout."""

    type = "stcs_atp_solo"
    ATP_TO_TRAIN_SIGNALS = (
        SignalDefinition("emergency_brake_1"),
        SignalDefinition("emergency_brake_2"),
        SignalDefinition("maximum_service_brake_7"),
        SignalDefinition("ato_enable"),
        SignalDefinition("turnback_activation"),
        SignalDefinition("powerless_passed_command"),
        SignalDefinition("cut_off_traction"),
        SignalDefinition("service_brake_4"),
        SignalDefinition("service_brake_1"),
        SignalDefinition("powerless_passed_select"),
        SignalDefinition("open_left_door_permit_1"),
        SignalDefinition("open_left_door_permit_2"),
        SignalDefinition("open_right_door_permit_1"),
        SignalDefinition("open_right_door_permit_2"),
        SignalDefinition("turnback_indicator"),
    )
    TRAIN_TO_ATP_SIGNALS = (
        _EB1_FEEDBACK,
        _EB2_FEEDBACK,
        _EB_FEEDBACK,
        _SB7_FEEDBACK,
        SignalDefinition("cab_activation"),
        _DIRECTION_FORWARD_1,
        _DIRECTION_FORWARD_2,
        _DIRECTION_BACKWARD,
        _SLEEP,
        _HANDLE_TRACTION,
        _HANDLE_BRAKE,
        SignalDefinition("turnback_button"),
        SignalDefinition("turnback_activation_feedback"),
        SignalDefinition("left_door_open_button"),
        SignalDefinition("right_door_open_button"),
        SignalDefinition("left_door_close_button"),
        SignalDefinition("right_door_close_button"),
        SignalDefinition("key_activation"),
        SignalDefinition("left_door_open_permit_feedback"),
        SignalDefinition("right_door_open_permit_feedback"),
        SignalDefinition("door_state_1"),
        SignalDefinition("door_state_2"),
    )
