"""STCS ATP protection state and train-output feedback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..controls import CabStateControl, DriverControl, StcsAtpControl
from ..snapshots import SignalState, StcsAtpSnapshot
from .base import EquipmentIntent


@dataclass(frozen=True)
class SignalDefinition:
    """One named boolean signal at a protocol-defined bit position."""

    name: str
    default: bool = False


class StcsAtpBase:
    """Shared lifecycle and feedback behavior for STCS ATP variants."""

    type: ClassVar[str]
    ATP_TO_TRAIN_SIGNALS: ClassVar[tuple[SignalDefinition, ...]]
    TRAIN_TO_ATP_SIGNALS: ClassVar[tuple[SignalDefinition, ...]]

    def __init__(self, key: str, *, cab_id: int) -> None:
        self._key = key
        self._cab_id = cab_id
        self._last_command: str | None = None
        self._handles: dict[int, tuple[str, str]] = {}
        self._train_out_states = {
            signal.name: signal.default for signal in self.TRAIN_TO_ATP_SIGNALS
        }
        self._train_in_states = {
            signal.name: signal.default for signal in self.ATP_TO_TRAIN_SIGNALS
        }
        self._update_train_out_states()

    @property
    def key(self) -> str:
        return self._key

    @property
    def cab_id(self) -> int:
        return self._cab_id

    def apply_control(self, control: StcsAtpControl) -> None:
        if not isinstance(control, StcsAtpControl):
            raise ValueError("stcs_atp control is invalid")
        if control.left_door_open is not None:
            self._set_train_out_state("door_state_1", control.left_door_open)
        if control.right_door_open is not None:
            self._set_train_out_state("door_state_2", control.right_door_open)
        if control.direction is not None or control.mode is not None:
            self._apply_handle_feedback(control)
        if control.command is not None:
            if (
                not isinstance(control.command, str)
                or not control.command
                or any(bit not in "01" for bit in control.command)
            ):
                raise ValueError("stcs_atp command must be a non-empty string of '0' and '1'")
            self._last_command = control.command
            for signal, bit in zip(self.ATP_TO_TRAIN_SIGNALS, control.command):
                self._train_in_states[signal.name] = bit == "1"
        self._update_train_out_states()

    def _set_train_out_state(self, name: str, value: bool) -> None:
        if name in self._train_out_states:
            self._train_out_states[name] = value

    def observe_cab_state(self, state: CabStateControl) -> None:
        if state.cab_id != self._cab_id:
            return
        self._set_train_out_state("cab_activation", state.active)
        self._set_train_out_state("key_activation", state.key_inserted)
        self._update_train_out_states()

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

    def _update_train_out_states(self) -> None:
        self._set_train_out_state(
            "emergency_brake_1_inner_feedback",
            not self._train_in_states.get("emergency_brake_1", False),
        )
        self._set_train_out_state(
            "emergency_brake_2_inner_feedback",
            not self._train_in_states.get("emergency_brake_2", False),
        )
        self._set_train_out_state(
            "emergency_brake_feedback",
            not (
                self._train_in_states.get("emergency_brake_1", False)
                or self._train_in_states.get("emergency_brake_2", False)
            ),
        )
        self._set_train_out_state(
            "service_brake_7_feedback",
            not self._train_in_states.get("maximum_service_brake_7", False),
        )
        self._set_train_out_state(
            "sleep_signal", not self._train_out_states.get("cab_activation", False)
        )
        modes = {cab: handle[0] for cab, handle in self._handles.items()}
        directions = {cab: handle[1] for cab, handle in self._handles.items()}
        self._set_train_out_state(
            "direction_handle_forward_1", directions.get(1, "off") == "forward"
        )
        self._set_train_out_state(
            "direction_handle_forward_2", directions.get(2, "off") == "forward"
        )
        self._set_train_out_state("direction_handle_backward", "backward" in directions.values())
        self._set_train_out_state("traction_handle_traction", "traction" in modes.values())
        self._set_train_out_state("traction_handle_brake", "brake" in modes.values())
        self._update_variant_train_out_states()

    def _update_variant_train_out_states(self) -> None:
        """Allow a variant to derive additional output signals."""

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
        return self._train_out_states.copy()

    def read_state(self) -> StcsAtpSnapshot:
        train_out = tuple(
            SignalState(name=name, value=self._train_out_states[name])
            for name in (signal.name for signal in self.TRAIN_TO_ATP_SIGNALS)
        )
        return StcsAtpSnapshot(
            last_command=self._last_command,
            train_out_signal="".join("1" if signal.value else "0" for signal in train_out),
            train_in_states=tuple(
                SignalState(name=name, value=self._train_in_states[name])
                for name in (signal.name for signal in self.ATP_TO_TRAIN_SIGNALS)
            ),
            train_out_states=train_out,
        )

    def reset(self) -> None:
        self._last_command = None
        self._handles.clear()
        self._train_in_states = {
            signal.name: signal.default for signal in self.ATP_TO_TRAIN_SIGNALS
        }
        self._train_out_states = {
            signal.name: signal.default for signal in self.TRAIN_TO_ATP_SIGNALS
        }
        self._update_train_out_states()


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
        SignalDefinition("emergency_brake_1_inner_feedback"),
        SignalDefinition("emergency_brake_2_inner_feedback"),
        SignalDefinition("emergency_brake_feedback"),
        SignalDefinition("service_brake_7_feedback"),
        SignalDefinition("cab_activation"),
        SignalDefinition("direction_handle_forward_1"),
        SignalDefinition("direction_handle_forward_2"),
        SignalDefinition("direction_handle_backward"),
        SignalDefinition("sleep_signal"),
        SignalDefinition("traction_handle_traction"),
        SignalDefinition("traction_handle_brake"),
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
        SignalDefinition("c2_control_state_1_1"),
        SignalDefinition("c2_control_state_2_1"),
        SignalDefinition("system_switch_c2"),
        SignalDefinition("system_switch_auto"),
        SignalDefinition("system_switch_cbtc"),
        SignalDefinition("c2_control_state_1_2"),
        SignalDefinition("c2_control_state_2_2"),
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
        SignalDefinition("emergency_brake_1_inner_feedback"),
        SignalDefinition("emergency_brake_2_inner_feedback"),
        SignalDefinition("emergency_brake_feedback"),
        SignalDefinition("service_brake_7_feedback"),
        SignalDefinition("cab_activation"),
        SignalDefinition("direction_handle_forward_1"),
        SignalDefinition("direction_handle_forward_2"),
        SignalDefinition("direction_handle_backward"),
        SignalDefinition("sleep_signal"),
        SignalDefinition("traction_handle_traction"),
        SignalDefinition("traction_handle_brake"),
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
