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

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .equipment import (
    EQUIPMENT_REGISTRY,
    BtmEquipmentSet,
    CabEquipmentSet,
    DigitalIo,
    Door,
    Equipment,
    IoConfig,
)
from .io import IoMapping
from .physics import (
    integrate_forward,
    is_finite,
    is_normalized,
    is_positive_finite,
    resolve_acceleration,
)
from .snapshots import TrainSnapshot


@dataclass(frozen=True, kw_only=True)
class TrainControl:
    """A normalized train-control request routed to the active cab (§3.3).

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
    - ``cab``: ``cab_id`` plus ``command`` ``"activate"`` (transfers authority)
      or ``"deactivate"``.
    - ``btm``: ``cab_id`` plus opaque ``data`` bytes.
    - ``io``: ``direction`` plus either ``bits`` or ``values``.
    """

    key: str
    command: str | None = None
    cab_id: int | None = None
    data: bytes | None = None
    direction: str | None = None
    bits: str | None = None
    values: Mapping[str, bool] | None = None


@dataclass(frozen=True, kw_only=True)
class EquipmentConfig:
    """Configuration for a single pluggable addon equipment instance."""

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
    io_config: IoConfig = field(default_factory=IoConfig)
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
        if not isinstance(self.io_config.train_to_atp, IoMapping) or not isinstance(
            self.io_config.atp_to_train, IoMapping
        ):
            raise ValueError("io_config must contain IoMapping values")


@dataclass(frozen=True)
class _ControlView:
    """Adapter so ``physics.resolve_acceleration`` reads aggregate control state."""

    drive_demand: float
    doors_closed: bool


class Train:
    """The aggregate that owns one train's mutable physical and control state."""

    def __init__(self, config: TrainConfig) -> None:
        self._config = config

        # Create addon equipment from config, falling back to defaults
        # (Cab + Door + BTM + IO). Cab authority stays with the aggregate.
        self._addon_equipment: list[Equipment] = []
        if config.equipment_configs:
            for eq_cfg in config.equipment_configs:
                factory = EQUIPMENT_REGISTRY[eq_cfg.key]
                self._addon_equipment.append(
                    factory(
                        cab_ids=config.cab_ids,
                        io_config=config.io_config,
                        initial_door_state=config.initial_door_state,
                        initial_active_cab=config.initial_active_cab,
                        **eq_cfg.params,
                    )
                )
        else:
            # Default: Cab + Door + BTM per-cab + IO with configured mappings.
            self._addon_equipment.append(CabEquipmentSet(config.cab_ids, config.initial_active_cab))
            self._addon_equipment.append(Door(initial_state=config.initial_door_state))
            self._addon_equipment.append(BtmEquipmentSet(config.cab_ids))
            self._addon_equipment.append(DigitalIo(config.io_config))

        self._position = config.initial_position
        self._speed = config.initial_speed
        self._acceleration = 0.0

        self._drive_demand = 0.0
        self._active_cab = config.initial_active_cab

    def _find_equipment(self, key: str) -> Equipment | None:
        """Find addon equipment by key, or None if not present."""
        for eq in self._addon_equipment:
            if eq.key == key:
                return eq
        return None

    @property
    def train_id(self) -> str:
        return self._config.train_id

    def apply_control(self, control: TrainControl) -> ControlResult:
        """Validate a control request and update control state (no time advance).

        Validation is all-or-nothing: if any field is invalid, nothing is applied
        and the state is unchanged.
        """

        if control.cab_id != self._active_cab:
            return ControlResult(
                ok=False,
                error=(
                    f"cab {control.cab_id} is not the active cab of "
                    f"{self._config.train_id} (active: {self._active_cab})"
                ),
            )
        if control.drive_demand is not None and not is_normalized(control.drive_demand):
            return ControlResult(ok=False, error="drive_demand must be in [-1.0, 1.0]")

        if control.drive_demand is not None:
            self._drive_demand = control.drive_demand
        return ControlResult()

    def set_equipment(self, command: EquipmentSet) -> ControlResult:
        """Apply an equipment-set command immediately (no time advance).

        Cab ``activate`` transfers cab authority to the named cab; the other
        cabs' equipment flags follow. All other equipment state changes are
        routed to the component and validated at the aggregate boundary.
        """

        equipment = self._find_equipment(command.key)
        if equipment is None:
            return ControlResult(
                ok=False,
                error=f"no '{command.key}' equipment on {self._config.train_id}",
            )

        if command.key == "door":
            if not isinstance(equipment, Door):
                return ControlResult(
                    ok=False, error="'door' equipment does not accept door commands"
                )
            if command.command not in ("open", "close"):
                return ControlResult(ok=False, error="door command must be 'open' or 'close'")
            equipment.apply_control(command.command)
            return ControlResult()

        if command.key == "cab":
            if command.cab_id not in self._config.cab_ids:
                return ControlResult(
                    ok=False,
                    error=f"cab {command.cab_id} is not configured on {self._config.train_id}",
                )
            if command.command == "activate":
                self._active_cab = command.cab_id
                if isinstance(equipment, CabEquipmentSet):
                    equipment.set_active(command.cab_id)
                return ControlResult()
            if command.command == "deactivate":
                if command.cab_id == self._active_cab:
                    return ControlResult(
                        ok=False,
                        error=(
                            f"cannot deactivate the active cab {command.cab_id}; "
                            "activate another cab first"
                        ),
                    )
                if isinstance(equipment, CabEquipmentSet):
                    equipment.apply_control(command.cab_id, "deactivate")
                return ControlResult()
            return ControlResult(ok=False, error="cab command must be 'activate' or 'deactivate'")

        if command.key == "btm":
            if command.cab_id is None or command.data is None:
                return ControlResult(ok=False, error="btm requires cab_id and data")
            if not isinstance(equipment, BtmEquipmentSet):
                return ControlResult(ok=False, error=f"no BTM equipment on {self._config.train_id}")
            try:
                equipment.deliver(command.cab_id, command.data)
            except ValueError as exc:
                return ControlResult(ok=False, error=str(exc))
            return ControlResult()

        if command.key == "io":
            if not isinstance(equipment, DigitalIo):
                return ControlResult(ok=False, error="'io' equipment does not accept I/O updates")
            if (command.bits is None) == (command.values is None):
                return ControlResult(ok=False, error="io requires exactly one of bits or values")
            try:
                if command.bits is not None:
                    equipment.update_bits(command.direction or "", command.bits)
                else:
                    equipment.update_named(command.direction or "", command.values or {})
            except ValueError as exc:
                return ControlResult(ok=False, error=str(exc))
            return ControlResult()

        return ControlResult(
            ok=False,
            error=f"equipment '{command.key}' does not expose settable state",
        )

    def step(self, dt: float) -> None:
        """Resolve dynamics and integrate over one fixed step (§3.4)."""

        # 1. Apply accepted controls (already applied via apply_control).
        # 2. Resolve dynamics and integrate forward-only motion.
        # The aggregate derives equipment state into its train-to-ATP signals.
        door = self._find_equipment("door")
        doors_closed = door.closed if isinstance(door, Door) else True
        for eq in self._addon_equipment:
            if isinstance(eq, DigitalIo):
                eq.update_named(
                    "train_to_atp",
                    {"cab_active": True, "doors_closed": doors_closed},
                )
        control = _ControlView(
            drive_demand=self._drive_demand,
            doors_closed=doors_closed,
        )
        accel = resolve_acceleration(control, self._config)
        new_position, new_speed, applied = integrate_forward(
            position=self._position,
            speed=self._speed,
            acceleration=accel,
            dt=dt,
        )
        self._position, self._speed, self._acceleration = new_position, new_speed, applied
        # 3. Snapshot is constructed by get_snapshot().

    def get_snapshot(self) -> TrainSnapshot:
        return TrainSnapshot(
            train_id=self._config.train_id,
            cab_ids=self._config.cab_ids,
            active_cab=self._active_cab,
            speed=self._speed,
            acceleration=self._acceleration,
            position=self._position,
            direction="forward",
            drive_demand=self._drive_demand,
            equipment={eq.key: eq.read_state() for eq in self._addon_equipment},
        )

    def reset(self) -> None:
        """Restore configured physical state and clear control/equipment state."""

        self._position = self._config.initial_position
        self._speed = self._config.initial_speed
        self._acceleration = 0.0
        self._drive_demand = 0.0
        self._active_cab = self._config.initial_active_cab
        for eq in self._addon_equipment:
            eq.reset()
