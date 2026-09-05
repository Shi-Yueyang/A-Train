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

from .equipment import (
    EQUIPMENT_FACTORIES,
    Btm,
    Cab,
    Door,
    Equipment,
    EquipmentContext,
    StcsAtp,
)
from .physics import (
    integrate_forward,
    is_finite,
    is_normalized,
    is_positive_finite,
    resolve_acceleration,
)
from .snapshots import EquipmentSnapshot, TrainSnapshot


@dataclass(frozen=True, kw_only=True)
class TrainControl:
    """A normalized train-control request from any configured cab (§3.3).

    ``cab_id`` identifies the issuing cab for validation only; cabs carry no
    authority, and the same demand applied from either cab has the same effect.

    ``drive_demand`` is a signed, normalized lever in [-1.0, 1.0]: positive
    scales the traction limit, negative scales the deceleration limit. The
    model knows force and speed only; there is no separate brake state.
    """

    cab_id: int
    drive_demand: float | None = None


@dataclass(frozen=True)
class ControlResult:
    """Structured result of applying a control at the aggregate boundary.

    Invalid input is reported here rather than raised, so adapter exceptions
    never enter the simulation loop.
    """

    ok: bool = True
    error: str | None = None


@dataclass(frozen=True)
class EquipmentSet:
    """A transport-neutral equipment-set request (§3.5).

    The generic REST equipment endpoint maps its JSON body onto this command;
    the train dispatcher validates the fields each equipment type requires:

    - ``door``: ``command`` is ``"open"`` or ``"close"``.
    - ``cab``: ``cab_id`` plus ``command`` ``"activate"`` or ``"deactivate"``;
      sets that cab's local flag only, with no control-side effect.
    - ``btm``: ``cab_id`` plus opaque ``data`` bytes.
        - ``stcs_atp``: ``command`` is recorded as the last received command.
    """

    key: str
    command: str | None = None
    cab_id: int | None = None
    data: bytes | None = None


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


@dataclass(frozen=True, kw_only=True)
class TrainConfig:
    """Frozen per-train configuration (§3.2).

    Acceleration limits are positive and finite. This version models
    forward-only movement: initial speed is non-negative.
    """

    train_id: str
    cab_ids: tuple[int, ...]
    initial_active_cab: int
    max_traction_accel: float
    max_decel: float
    initial_position: float = 0.0
    initial_speed: float = 0.0
    initial_door_state: str = "closed"
    equipment_configs: tuple[EquipmentConfig, ...] = ()

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
        if not (is_finite(self.initial_speed) and self.initial_speed >= 0.0):
            raise ValueError("initial_speed must be a non-negative finite number")
        if not is_positive_finite(self.max_traction_accel):
            raise ValueError("max_traction_accel must be a positive finite number")
        if not is_positive_finite(self.max_decel):
            raise ValueError("max_decel must be a positive finite number")
        if self.initial_door_state not in ("open", "closed"):
            raise ValueError("initial_door_state must be 'open' or 'closed'")
        if not self.equipment_configs:
            # Standard fit: one Cab + one Btm per cab, one Door, one StcsAtp.
            object.__setattr__(
                self,
                "equipment_configs",
                tuple(
                    [EquipmentConfig("cab", f"cab_{cab_id}") for cab_id in self.cab_ids]
                    + [EquipmentConfig("door", "door_main")]
                    + [EquipmentConfig("btm", f"btm_{cab_id}") for cab_id in self.cab_ids]
                    + [EquipmentConfig("stcs_atp", "stcs_atp")]
                ),
            )
        keys = [eq_cfg.key for eq_cfg in self.equipment_configs]
        if len(set(keys)) != len(keys):
            raise ValueError("equipment keys must be unique")
        for eq_cfg in self.equipment_configs:
            if eq_cfg.type not in EQUIPMENT_FACTORIES:
                raise ValueError(f"unknown equipment type: {eq_cfg.type!r}")


class Train:
    """The aggregate that owns one train's mutable physical and control state."""

    def __init__(self, config: TrainConfig) -> None:
        self._config = config

        # Build every addon equipment instance through the factory registry;
        # TrainConfig guarantees a fully populated equipment_configs. The
        # context carries train-scope values; params override per instance.
        ctx = EquipmentContext(
            initial_door_state=config.initial_door_state,
            initial_active_cab=config.initial_active_cab,
        )
        self._equipment: dict[str, Equipment] = {
            eq_cfg.key: EQUIPMENT_FACTORIES[eq_cfg.type](eq_cfg.key, ctx, **eq_cfg.params)
            for eq_cfg in config.equipment_configs
        }

        self._position = config.initial_position
        self._speed = config.initial_speed
        self._acceleration = 0.0

        self._drive_demand = 0.0

    @property
    def train_id(self) -> str:
        return self._config.train_id

    def apply_control(self, control: TrainControl) -> ControlResult:
        """Validate a control request and update control state (no time advance).

        Validation is all-or-nothing: if any field is invalid, nothing is applied
        and the state is unchanged.
        """

        if control.cab_id not in self._config.cab_ids:
            return ControlResult(
                ok=False,
                error=f"cab {control.cab_id} is not configured on {self._config.train_id}",
            )
        if control.drive_demand is not None and not is_normalized(control.drive_demand):
            return ControlResult(ok=False, error="drive_demand must be in [-1.0, 1.0]")

        if control.drive_demand is not None:
            self._drive_demand = control.drive_demand
        return ControlResult()

    def set_equipment(self, command: EquipmentSet) -> ControlResult:
        """Apply an equipment-set command immediately (no time advance).

        Cab ``activate``/``deactivate`` set that cab's local flag and nothing
        else; cabs hold no authority, so control acceptance is unaffected.
        Every equipment state change is routed to the component and validated
        at the aggregate boundary.
        """

        equipment = self._equipment.get(command.key)
        if equipment is None:
            return ControlResult(ok=False, error=f"no '{command.key}' equipment on {self._config.train_id}")

        if isinstance(equipment, Door):
            if command.command not in ("open", "close"):
                return ControlResult(ok=False, error="door command must be 'open' or 'close'")
            equipment.apply_control(command.command)
            return ControlResult()

        if isinstance(equipment, Cab):
            if command.command not in ("activate", "deactivate"):
                return ControlResult(
                    ok=False, error="cab command must be 'activate' or 'deactivate'"
                )
            if command.cab_id is not None and command.cab_id != equipment.cab_id:
                return ControlResult(ok=False, error="cab_id does not match equipment key")
            equipment.apply_control(command.command)
            return ControlResult()

        if isinstance(equipment, Btm):
            if command.data is None:
                return ControlResult(ok=False, error="btm requires data")
            if command.cab_id is not None and command.cab_id != equipment.cab_id:
                return ControlResult(ok=False, error="cab_id does not match equipment key")
            equipment.accept(command.data)
            return ControlResult()

        if isinstance(equipment, StcsAtp):
            if command.command is None:
                return ControlResult(ok=False, error="stcs_atp requires a command")
            equipment.apply_control(command.command)
            return ControlResult()

        return ControlResult(
            ok=False,
            error=f"equipment '{command.key}' does not expose settable state",
        )

    def step(self, dt: float) -> None:
        """Integrate forward-only motion over one fixed step (§3.4).

        No equipment affects the dynamics; the drive demand is the only input.
        """

        accel = resolve_acceleration(self._drive_demand, self._config)
        self._position, self._speed, self._acceleration = integrate_forward(
            position=self._position,
            speed=self._speed,
            acceleration=accel,
            dt=dt,
        )
        for equipment in self._equipment.values():
            on_step = getattr(equipment, "step", None)
            if on_step is not None:
                on_step(dt, self._speed)

    def _equipment_snapshot(self) -> tuple[EquipmentSnapshot, ...]:
        """Group per-instance snapshots by type key for the train snapshot.

        Train-level singletons (empty slot, one instance) expose a single
        snapshot; slotted equipment (per-cab cabs, BTM, multiple doors)
        exposes a tuple with one entry per instance, §3.5.
        """

        return tuple(
            EquipmentSnapshot(type=equipment.type, key=key, state=equipment.read_state())
            for key, equipment in self._equipment.items()
        )

    def get_snapshot(self) -> TrainSnapshot:
        equipment = self._equipment_snapshot()
        return TrainSnapshot(
            train_id=self._config.train_id,
            cab_ids=self._config.cab_ids,
            speed=self._speed,
            acceleration=self._acceleration,
            position=self._position,
            direction="forward",
            drive_demand=self._drive_demand,
            equipment=equipment,
        )

    def reset(self) -> None:
        """Restore configured physical state and clear control/equipment state."""

        self._position = self._config.initial_position
        self._speed = self._config.initial_speed
        self._acceleration = 0.0
        self._drive_demand = 0.0
        for eq in self._equipment.values():
            eq.reset()
