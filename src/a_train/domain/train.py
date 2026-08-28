"""Train aggregate and stable per-step update entry point (§3).

The train model owns mutable physical and control state and converts accepted
control commands into physical motion. It does not know whether a command came
from the browser, an ATP process, or a test; adapters identify the train and
cab and submit a transport-neutral command to the simulation core, which calls
the small aggregate API:

    apply_control(control)   validate + update control state (no time advance)
    step(dt)                 update equipment, resolve dynamics, integrate
    get_snapshot()           construct an immutable view
    reset()                  restore configured physical state, clear runtime

The aggregate coordinates its equipment in a documented, stable order (§3.6):
apply accepted controls, update equipment, resolve dynamics, then construct the
snapshot. Physics lives in ``physics.py``; it never mutates aggregate state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .equipment import (
    BtmDelivery,
    BtmEquipment,
    Cab,
    DigitalIo,
    Door,
    DoorCommand,
    IoConfig,
    IoNamed,
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

    Each field is optional so a single request can update any subset of the
    control state. ``emergency_brake`` is a latch: ``True`` applies it, ``False``
    requests release, ``None`` leaves it unchanged.
    """

    cab_id: int
    traction_demand: float | None = None
    service_brake_demand: float | None = None
    emergency_brake: bool | None = None
    door: str | None = None


@dataclass(frozen=True)
class ControlResult:
    """Structured result of applying a control at the aggregate boundary.

    Invalid input is reported here rather than raised, so adapter exceptions
    never enter the simulation loop.
    """

    ok: bool = True
    error: str | None = None


@dataclass(frozen=True, kw_only=True)
class TrainConfig:
    """Frozen per-train configuration (§3.2).

    Acceleration limits are positive and finite. Emergency-brake deceleration
    must be at least the service-brake deceleration. This version models
    forward-only movement: initial speed is non-negative.
    """

    train_id: str
    cab_ids: tuple[int, ...]
    initial_active_cab: int
    max_traction_accel: float
    max_service_brake_decel: float
    max_emergency_brake_decel: float
    initial_position: float = 0.0
    initial_speed: float = 0.0
    initial_door_state: str = "closed"
    io_config: IoConfig = field(default_factory=IoConfig)

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
        if not is_positive_finite(self.max_service_brake_decel):
            raise ValueError("max_service_brake_decel must be a positive finite number")
        if not is_positive_finite(self.max_emergency_brake_decel):
            raise ValueError("max_emergency_brake_decel must be a positive finite number")
        if self.max_emergency_brake_decel < self.max_service_brake_decel:
            raise ValueError("max_emergency_brake_decel must be at least max_service_brake_decel")
        if self.initial_door_state not in ("open", "closed"):
            raise ValueError("initial_door_state must be 'open' or 'closed'")
        if not isinstance(self.io_config.train_to_atp, IoMapping) or not isinstance(
            self.io_config.atp_to_train, IoMapping
        ):
            raise ValueError("io_config must contain IoMapping values")


@dataclass(frozen=True)
class _ControlView:
    """Adapter so ``physics.resolve_acceleration`` reads aggregate control state."""

    emergency_brake: bool
    service_brake_demand: float
    traction_demand: float
    doors_closed: bool


class Train:
    """The aggregate that owns one train's mutable physical and control state."""

    def __init__(self, config: TrainConfig) -> None:
        self._config = config
        self._cabs = tuple(
            Cab(cid, initial_active=(cid == config.initial_active_cab)) for cid in config.cab_ids
        )
        self._door = Door(initial_state=config.initial_door_state)
        self._btm = tuple(BtmEquipment(cid) for cid in config.cab_ids)
        self._io = DigitalIo(config.io_config)

        self._position = config.initial_position
        self._speed = config.initial_speed
        self._acceleration = 0.0

        self._traction_demand = 0.0
        self._service_brake_demand = 0.0
        self._emergency_brake = False
        self._active_cab = config.initial_active_cab

    @property
    def train_id(self) -> str:
        return self._config.train_id

    @property
    def active_cab(self) -> int:
        return self._active_cab

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
        if control.traction_demand is not None and not is_normalized(control.traction_demand):
            return ControlResult(ok=False, error="traction_demand must be in [0.0, 1.0]")
        if control.service_brake_demand is not None and not is_normalized(
            control.service_brake_demand
        ):
            return ControlResult(ok=False, error="service_brake_demand must be in [0.0, 1.0]")
        if control.door is not None and control.door not in ("open", "close"):
            return ControlResult(ok=False, error="door must be 'open' or 'close'")

        if control.traction_demand is not None:
            self._traction_demand = control.traction_demand
        if control.service_brake_demand is not None:
            self._service_brake_demand = control.service_brake_demand
        if control.emergency_brake is not None:
            self._emergency_brake = control.emergency_brake
        if control.door is not None:
            self._door.receive(DoorCommand(command=control.door))
        return ControlResult()

    def receive_btm(self, cab_id: int, data: bytes) -> ControlResult:
        """Deliver an opaque BTM byte array to a cab's equipment (§4.6)."""

        if cab_id not in self._config.cab_ids:
            return ControlResult(
                ok=False,
                error=f"cab {cab_id} is not configured on {self._config.train_id}",
            )
        for btm in self._btm:
            if btm.cab_id == cab_id:
                btm.receive(BtmDelivery(cab_id=cab_id, data=data))
                return ControlResult()
        return ControlResult(ok=False, error=f"no BTM equipment for cab {cab_id}")

    def step(self, dt: float) -> None:
        """Advance equipment and physics over one fixed step (§3.4, §3.6)."""

        # 1. Apply accepted controls (already applied via apply_control).
        # 2. Update equipment in stable order.
        for cab in self._cabs:
            cab.step(dt)
        self._door.step(dt)
        for btm in self._btm:
            btm.step(dt)
        self._io.step(dt)
        # The aggregate feeds derived state into its train-to-ATP signals.
        self._io.receive(
            IoNamed(
                direction="train_to_atp",
                values={"cab_active": True, "doors_closed": self._door.closed},
            )
        )
        # 3. Resolve dynamics and integrate forward-only motion.
        control = _ControlView(
            emergency_brake=self._emergency_brake,
            service_brake_demand=self._service_brake_demand,
            traction_demand=self._traction_demand,
            doors_closed=self._door.closed,
        )
        accel = resolve_acceleration(control, self._config)
        new_position, new_speed, applied = integrate_forward(
            position=self._position,
            speed=self._speed,
            acceleration=accel,
            dt=dt,
        )
        self._position, self._speed, self._acceleration = new_position, new_speed, applied
        # 4. Snapshot is constructed by get_snapshot().

    def get_snapshot(self) -> TrainSnapshot:
        return TrainSnapshot(
            train_id=self._config.train_id,
            cab_ids=self._config.cab_ids,
            active_cab=self._active_cab,
            speed=self._speed,
            acceleration=self._acceleration,
            position=self._position,
            direction="forward",
            traction_demand=self._traction_demand,
            service_brake_demand=self._service_brake_demand,
            emergency_brake=self._emergency_brake,
            door_state=self._door.get_snapshot().state,
            cab=tuple(c.get_snapshot() for c in self._cabs),
            doors=self._door.get_snapshot(),
            btm=tuple(b.get_snapshot() for b in self._btm),
            io=self._io.get_snapshot(),
        )

    def reset(self) -> None:
        """Restore configured physical state and clear control/equipment state."""

        self._position = self._config.initial_position
        self._speed = self._config.initial_speed
        self._acceleration = 0.0
        self._traction_demand = 0.0
        self._service_brake_demand = 0.0
        self._emergency_brake = False
        self._active_cab = self._config.initial_active_cab
        for cab in self._cabs:
            cab.reset()
        self._door.reset()
        for btm in self._btm:
            btm.reset()
        self._io.reset()
