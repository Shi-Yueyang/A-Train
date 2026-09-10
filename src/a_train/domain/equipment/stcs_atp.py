"""STCS ATP protection state and train-output feedback."""

from __future__ import annotations

from ..controls import CabStateControl, DriverControl, StcsAtpControl
from ..snapshots import SignalState, StcsAtpSnapshot
from .base import EquipmentIntent


class StcsAtp:
    """Decode ATP assertions and expose train-side feedback state."""

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

    def __init__(self, key: str, *, cab_id: int) -> None:
        self._key = key
        self._cab_id = cab_id
        self._last_command: str | None = None
        self._handles: dict[int, tuple[str, str]] = {}
        self._train_out_states = {name: False for name in self.TRAIN_TO_ATP_SIGNAL_BY_BIT.values()}
        self._train_in_states = {name: False for name in self.ATP_TO_TRAIN_SIGNAL_BY_BIT.values()}
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
            self._train_out_states["door_state_1"] = control.left_door_open
        if control.right_door_open is not None:
            self._train_out_states["door_state_2"] = control.right_door_open
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
            for bit_index, bit in enumerate(control.command):
                state_name = self.ATP_TO_TRAIN_SIGNAL_BY_BIT.get(bit_index)
                if state_name is not None:
                    self._train_in_states[state_name] = bit == "1"
        self._update_train_out_states()

    def observe_cab_state(self, state: CabStateControl) -> None:
        if state.cab_id != self._cab_id:
            return
        self._train_out_states["cab_activation"] = state.active
        self._train_out_states["key_activation"] = state.key_inserted
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
        self._train_out_states["emergency_brake_1_inner_feedback"] = not self._train_in_states[
            "emergency_brake_1"
        ]
        self._train_out_states["emergency_brake_2_inner_feedback"] = not self._train_in_states[
            "emergency_brake_2"
        ]
        self._train_out_states["emergency_brake_feedback"] = not (
            self._train_in_states["emergency_brake_1"] or self._train_in_states["emergency_brake_2"]
        )
        self._train_out_states["service_brake_7_feedback"] = not self._train_in_states[
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
            EquipmentIntent(source=self._key, target="train", control=DriverControl(brake=1.0)),
        )

    @property
    def train_in_states(self) -> dict[str, bool]:
        return self._train_in_states.copy()

    @property
    def train_out_states(self) -> dict[str, bool]:
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
        self._update_train_out_states()
