"""Train-world rules, independent of the time loop and external transports.

The ``domain`` package never imports ``simulation``, ``adapters``, or ``web``
(§7.2). It owns the train aggregate, physics, equipment, linear signals, and
the immutable snapshot types the aggregate publishes.
"""

from __future__ import annotations

from .equipment import (
    Btm,
    Door,
    EquipmentContext,
)
from .physics import (
    integrate_forward,
    is_finite,
    is_normalized,
    is_positive_finite,
    resolve_acceleration,
)
from .snapshots import (
    BtmSnapshot,
    CabSnapshot,
    DoorSnapshot,
    TrainSnapshot,
)
from .train import ControlResult, EquipmentSet, Train, TrainConfig, TrainControl

__all__ = [
    "Btm",
    "BtmSnapshot",
    "CabSnapshot",
    "ControlResult",
    "Door",
    "DoorSnapshot",
    "EquipmentContext",
    "EquipmentSet",
    "Train",
    "TrainConfig",
    "TrainControl",
    "TrainSnapshot",
    "integrate_forward",
    "is_finite",
    "is_normalized",
    "is_positive_finite",
    "resolve_acceleration",
]
