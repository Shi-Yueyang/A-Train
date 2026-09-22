"""Train aggregate and stable per-step update entry point (§3).

The train model owns mutable physical and control state and converts accepted
control commands into physical motion. It does not know whether a command came
from the browser, an ATP process, or a test; adapters identify the train and
cab and submit a transport-neutral command to the simulation core, which calls
the small aggregate API:

    apply_control(control)   validate + update control state (no time advance)
    step(dt)                 resolve dynamics, integrate
    get_snapshot()           construct an immutable view
    reset()                  restore configured physical state, clear runtime

The aggregate coordinates its equipment in a documented, stable order (§3.6):
apply accepted controls, resolve dynamics, then construct the snapshot.
Physics lives in ``physics.py``; it never mutates aggregate state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .controls import (
    BtmControl,
    CabStateControl,
    DoorControl,
    DriverControl,
    DrivingSystemControl,
    StcsAtpControl,
    TrainControl,
)
from .equipment import (
    EQUIPMENT_FACTORIES,
    Btm,
    Door,
    DrivingSystem,
    Equipment,
    EquipmentContext,
    EquipmentIntent,
    StcsAtpBase,
)
from .physics import (
    integrate,
    is_finite,
    is_normalized,
    is_positive_finite,
    resolve_acceleration,
    resolve_driver_acceleration,
)
from .snapshots import CabSnapshot, EquipmentSnapshot, TrainSnapshot


@dataclass(frozen=True)
class ControlResult:
    """Structured result of applying a control at the aggregate boundary.

    Invalid input is reported here rather than raised, so adapter exceptions
    never enter the simulation loop.
    """

    ok: bool = True
    error: str | None = None


@dataclass(frozen=True)
class EquipmentControlRequest:
    """A transport-neutral equipment-control request (§3.5).

    The generic REST equipment endpoint maps its JSON body onto this command;
    the train dispatcher validates the fields each equipment type requires:

    - ``door``: ``command`` is ``"open"`` or ``"close"``.
    - ``btm``: ``cab_id`` plus opaque ``data`` bytes.
    - ``stcs_atp_duo_<cab_id>``: ``command`` is recorded on that cab's STCS instance.
    - ``driving_system``: ``mode``/``direction``/``acceleration`` handle
      positions; every combination of fields may be set together.
    """

    key: str
    command: str | None = None
    cab_id: int | None = None
    data: bytes | None = None
    mode: str | None = None
    direction: str | None = None
    acceleration: float | None = None


@dataclass(frozen=True)
class EquipmentConfig:
    """Configuration for a single pluggable addon equipment instance.

    ``type`` selects the factory in ``EQUIPMENT_FACTORIES``; ``key`` gives the
    unique instance identity within the train. ``params`` are forwarded as
    keyword arguments to the factory, so unknown names are rejected at
    construction.
    """

    type: str
    key: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True, kw_only=True)
class TrainConfig:
    """Frozen per-train configuration (§3.2).

    Acceleration limits are positive and finite. Motion is reversible along
    the single linear track: speed may be negative (rearward). Each cab's
    ``cab_facings`` entry is its track facing, ``+1`` (the cab drives toward
    increasing position) or ``-1``; the driving system maps its cab-relative
    direction handle through this facing.
    """

    train_id: str
    cab_ids: tuple[int, ...]
    initial_active_cab: int
    max_traction_accel: float
    max_decel: float
    initial_position: float = 0.0
    initial_speed: float = 0.0
    initial_door_state: str = "closed"
    cab_facings: dict[int, int] | None = None
    equipment_configs: tuple[EquipmentConfig, ...] | None = None

    def __post_init__(self) -> None:
        if not self.train_id or not isinstance(self.train_id, str):
            raise ValueError("train_id must be a non-empty string")
        object.__setattr__(self, "cab_ids", tuple(self.cab_ids))
        if len(self.cab_ids) not in (1, 2):
            raise ValueError("a train has one or two cabs")
        if len(set(self.cab_ids)) != len(self.cab_ids):
            raise ValueError("cab_ids must be unique")
        if any(not isinstance(c, int) or c <= 0 for c in self.cab_ids):
            raise ValueError("cab_ids must be positive integers")
        if self.initial_active_cab not in self.cab_ids:
            raise ValueError("initial_active_cab must be one of cab_ids")
        if not is_finite(self.initial_position):
            raise ValueError("initial_position must be finite")
        if not is_finite(self.initial_speed):
            raise ValueError("initial_speed must be a finite number")
        if not is_positive_finite(self.max_traction_accel):
            raise ValueError("max_traction_accel must be a positive finite number")
        if not is_positive_finite(self.max_decel):
            raise ValueError("max_decel must be a positive finite number")
        if self.initial_door_state not in ("open", "closed"):
            raise ValueError("initial_door_state must be 'open' or 'closed'")
        if self.cab_facings is None:
            # Real driver rooms: the first cab faces track-increasing, the
            # other faces the opposite way.
            object.__setattr__(
                self,
                "cab_facings",
                {cab_id: 1 if index == 0 else -1 for index, cab_id in enumerate(self.cab_ids)},
            )
        else:
            facings = dict(self.cab_facings)
            if set(facings) != set(self.cab_ids):
                raise ValueError("cab_facings must cover exactly the configured cabs")
            if any(facing not in (-1, 1) for facing in facings.values()):
                raise ValueError("cab facings must be +1 or -1")
            object.__setattr__(self, "cab_facings", facings)
        if self.equipment_configs is None:
            # Standard fit: one Btm and one DrivingSystem per cab, two Doors,
            # one StcsAtpDuo.
            object.__setattr__(
                self,
                "equipment_configs",
                tuple(
                    [
                        EquipmentConfig("door", "left_door"),
                        EquipmentConfig("door", "right_door"),
                    ]
                    + [EquipmentConfig("btm", f"btm_{cab_id}") for cab_id in self.cab_ids]
                    + [
                        EquipmentConfig("driving_system", f"driving_{cab_id}")
                        for cab_id in self.cab_ids
                    ]
                    + [
                        EquipmentConfig("stcs_atp_duo", f"stcs_atp_duo_{cab_id}")
                        for cab_id in self.cab_ids
                    ]
                ),
            )
        keys = [eq_cfg.key for eq_cfg in self.equipment_configs]
        if len(set(keys)) != len(keys):
            raise ValueError("equipment keys must be unique")
        for eq_cfg in self.equipment_configs:
            if eq_cfg.type not in EQUIPMENT_FACTORIES:
                raise ValueError(f"unknown equipment type: {eq_cfg.type!r}")
            if not isinstance(eq_cfg.enabled, bool):
                raise ValueError(f"equipment {eq_cfg.key!r} enabled must be a boolean")


class Train:
    """The aggregate that owns one train's mutable physical and control state."""

    def __init__(self, config: TrainConfig) -> None:
        self._config = config

        # Build every addon equipment instance through the factory registry;
        # TrainConfig guarantees a fully populated equipment_configs. The
        # context carries train-scope values; params override per instance.
        ctx = EquipmentContext(
            initial_door_state=config.initial_door_state,
            cab_facings=config.cab_facings,
        )
        self._equipment: dict[str, Equipment[Any]] = {
            eq_cfg.key: EQUIPMENT_FACTORIES[eq_cfg.type](eq_cfg.key, ctx, **eq_cfg.params)
            for eq_cfg in config.equipment_configs
            if eq_cfg.enabled
        }

        self._position = config.initial_position
        self._speed = config.initial_speed
        self._acceleration = 0.0

        self._drive_demand = 0.0
        self._driver_inputs: tuple[DriverControl, ...] = ()
        self._cab_active = {
            cab_id: cab_id == config.initial_active_cab for cab_id in config.cab_ids
        }
        self._cab_key = {cab_id: False for cab_id in config.cab_ids}
        # Sync observer equipment (e.g. door feedback into stcs_atp_duo) with the
        # configured initial state before the first snapshot is taken.
        self._resolve_all_intents()

    @property
    def train_id(self) -> str:
        return self._config.train_id

    def apply_control(self, control: TrainControl) -> ControlResult:
        """Validate a control request and update control state (no time advance).

        Validation is all-or-nothing: if any field is invalid, nothing is applied
        and the state is unchanged.
        """

        if control.cab_id is not None and control.cab_id not in self._config.cab_ids:
            return ControlResult(
                ok=False,
                error=f"cab {control.cab_id} is not configured on {self._config.train_id}",
            )
        if control.drive_demand is not None and not is_normalized(control.drive_demand):
            return ControlResult(ok=False, error="drive_demand must be in [-1.0, 1.0]")
        if control.active is not None and control.cab_id is None:
            return ControlResult(ok=False, error="cab activation requires cab_id")
        if control.key is not None and control.cab_id is None:
            return ControlResult(ok=False, error="cab key state requires cab_id")

        if control.drive_demand is not None:
            self._drive_demand = control.drive_demand
        if control.active is not None:
            self._cab_active[control.cab_id] = control.active
        if control.key is not None:
            self._cab_key[control.cab_id] = control.key
        if control.active is not None or control.key is not None:
            self._resolve_all_intents()
        return ControlResult()

    def set_equipment(self, command: EquipmentControlRequest) -> ControlResult:
        """Apply an equipment-control request immediately (no time advance).

        Every equipment state change is routed to the component and validated
        at the aggregate boundary.
        """

        equipment = self._equipment.get(command.key)
        if equipment is None and command.key.startswith("stcs_atp_cab_"):
            cab_id = int(command.key.removeprefix("stcs_atp_cab_"))
            equipment = next(
                (
                    candidate
                    for candidate in self._equipment.values()
                    if isinstance(candidate, StcsAtpBase) and candidate.cab_id == cab_id
                ),
                None,
            )
        if equipment is None:
            return ControlResult(
                ok=False,
                error=f"no '{command.key}' equipment on {self._config.train_id}",
            )

        try:
            equipment.apply_control(self._equipment_control(equipment, command))
        except ValueError as exc:
            return ControlResult(ok=False, error=str(exc))
        self._resolve_all_intents()
        return ControlResult()

    def _resolve_all_intents(self) -> None:
        self._resolve_equipment_intents(
            self._collect_equipment_intents() + self._cab_state_intents()
        )

    def _cab_state_intents(self) -> tuple[EquipmentIntent, ...]:
        return tuple(
            EquipmentIntent(
                source=f"cab_{cab_id}",
                target="all_equipments",
                control=CabStateControl(
                    cab_id=cab_id,
                    active=self._cab_active[cab_id],
                    key_inserted=self._cab_key[cab_id],
                ),
            )
            for cab_id in self._cab_active
        )

    @staticmethod
    def _equipment_control(equipment: Equipment[Any], command: EquipmentControlRequest):
        if isinstance(equipment, Door):
            if command.command is None:
                raise ValueError("door requires a command")
            return DoorControl(command.command)
        if isinstance(equipment, Btm):
            if command.data is None:
                raise ValueError("btm requires data")
            return BtmControl(command.data, command.cab_id)
        if isinstance(equipment, StcsAtpBase):
            if command.command is None:
                raise ValueError("stcs_atp requires a command")
            return StcsAtpControl(command.command)
        if isinstance(equipment, DrivingSystem):
            if command.mode is None and command.direction is None and command.acceleration is None:
                raise ValueError("driving system requires mode, direction, or acceleration")
            return DrivingSystemControl(
                mode=command.mode,
                direction=command.direction,
                acceleration=command.acceleration,
            )
        raise ValueError(f"equipment '{command.key}' does not expose settable state")

    def step(self, dt: float) -> None:
        """Integrate reversible motion over one fixed step (§3.4).

        Equipment intents are collected before integration and resolved by
        the aggregate. An engaged driving system (or an asserted ATP
        protection brake) overwrites the legacy drive-demand lever for the
        step; with no driver intents the lever applies unchanged.
        """

        self._resolve_equipment_intents(self._collect_equipment_intents())
        if self._driver_inputs:
            traction = max(-1.0, min(1.0, sum(d.traction for d in self._driver_inputs)))
            brake = max(0.0, min(1.0, sum(d.brake for d in self._driver_inputs)))
            accel = resolve_driver_acceleration(
                traction, brake, speed=self._speed, limits=self._config
            )
        else:
            accel = resolve_acceleration(self._drive_demand, self._config, speed=self._speed)
        self._position, self._speed, self._acceleration = integrate(
            position=self._position,
            speed=self._speed,
            acceleration=accel,
            dt=dt,
        )
        for equipment in self._equipment.values():
            on_step = getattr(equipment, "step", None)
            if on_step is not None:
                on_step(dt, self._speed)

    def _collect_equipment_intents(self) -> tuple[EquipmentIntent, ...]:
        """Collect cross-component requests without sharing equipment refs."""
        return tuple(
            intent for equipment in self._equipment.values() for intent in equipment.emit_intents()
        )

    def _resolve_equipment_intents(self, intents: tuple[EquipmentIntent, ...]) -> None:
        """Route intents without embedding equipment-specific action logic.

        Driver intents are state assertions: the aggregate rebuilds its
        driver-input set from the current batch, so an input absent from the
        batch (mode back to ``off``, brake released) lapses automatically.
        """

        driver_inputs: list[DriverControl] = []
        for intent in intents:
            if intent.target == "train":
                if isinstance(intent.control, TrainControl):
                    self.apply_control(intent.control)
                elif isinstance(intent.control, DriverControl):
                    driver_inputs.append(intent.control)
                continue
            target = self._equipment.get(intent.target)
            if target is not None:
                target.apply_control(intent.control)
            elif intent.target == "all_equipments" and isinstance(intent.control, CabStateControl):
                for equipment in self._equipment.values():
                    observe_cab_state = getattr(equipment, "observe_cab_state", None)
                    if observe_cab_state is not None:
                        observe_cab_state(intent.control)
            elif intent.target == "stcs_atp":
                for equipment in self._equipment.values():
                    if isinstance(equipment, StcsAtpBase):
                        equipment.apply_control(intent.control)
            elif intent.target.startswith("stcs_atp_cab_"):
                cab_id = int(intent.target.removeprefix("stcs_atp_cab_"))
                for equipment in self._equipment.values():
                    if isinstance(equipment, StcsAtpBase) and equipment.cab_id == cab_id:
                        equipment.apply_control(intent.control)
        self._driver_inputs = tuple(driver_inputs)

    def _equipment_snapshot(self) -> tuple[EquipmentSnapshot, ...]:
        """Group per-instance snapshots by type key for the train snapshot.

        Train-level singletons (empty slot, one instance) expose a single
        snapshot; slotted equipment (per-cab BTM, multiple doors) exposes a
        tuple with one entry per instance, §3.5.
        """

        return tuple(
            EquipmentSnapshot(type=equipment.type, key=key, state=equipment.read_state())
            for key, equipment in self._equipment.items()
        )

    def get_snapshot(self) -> TrainSnapshot:
        equipment = self._equipment_snapshot()
        facings = self._config.cab_facings
        return TrainSnapshot(
            train_id=self._config.train_id,
            cabs=tuple(
                CabSnapshot(
                    cab_id=cab_id,
                    active=active,
                    key=self._cab_key[cab_id],
                    facing="forward" if facings[cab_id] == 1 else "backward",
                )
                for cab_id, active in self._cab_active.items()
            ),
            speed=self._speed,
            acceleration=self._acceleration,
            position=self._position,
            direction=(
                "forward" if self._speed > 0.0 else "backward" if self._speed < 0.0 else "stopped"
            ),
            drive_demand=self._drive_demand,
            equipment=equipment,
        )

    def reset(self) -> None:
        """Restore configured physical state and clear control/equipment state."""

        self._position = self._config.initial_position
        self._speed = self._config.initial_speed
        self._acceleration = 0.0
        self._drive_demand = 0.0
        self._cab_active = {
            cab_id: cab_id == self._config.initial_active_cab for cab_id in self._config.cab_ids
        }
        self._cab_key = {cab_id: False for cab_id in self._config.cab_ids}
        for eq in self._equipment.values():
            eq.reset()
        self._resolve_all_intents()
