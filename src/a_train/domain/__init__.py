"""Train-world rules, independent of the time loop and external transports.

The ``domain`` package never imports ``simulation``, ``adapters``, or ``web``
(§7.2). It owns the train aggregate, physics, equipment, linear signals, and
the immutable snapshot types the aggregate publishes.
"""

from __future__ import annotations

from .equipment import (
    Btm,
    Door,
    DrivingSystem,
    EquipmentContext,
    StcsAtpDuo,
    StcsAtpSolo,
)
from .physics import (
    integrate,
    is_finite,
    is_normalized,
    is_positive_finite,
    resolve_acceleration,
    resolve_driver_acceleration,
)
from .snapshots import (
    BtmSnapshot,
    CabSnapshot,
    DoorSnapshot,
    DrivingSystemSnapshot,
    TrainSnapshot,
)
from .train import ControlResult, EquipmentControlRequest, Train, TrainConfig, TrainControl

__all__ = [
    "Btm",
    "BtmSnapshot",
    "CabSnapshot",
    "ControlResult",
    "Door",
    "DoorSnapshot",
    "DrivingSystem",
    "DrivingSystemSnapshot",
    "EquipmentContext",
    "EquipmentControlRequest",
    "StcsAtpDuo",
    "StcsAtpSolo",
    "Train",
    "TrainConfig",
    "TrainControl",
    "TrainSnapshot",
    "integrate",
    "is_finite",
    "is_normalized",
    "is_positive_finite",
    "resolve_acceleration",
    "resolve_driver_acceleration",
]
