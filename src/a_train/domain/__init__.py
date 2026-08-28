"""Train-world rules, independent of the time loop and external transports.

The ``domain`` package never imports ``simulation``, ``scenario``, ``adapters``,
or ``web`` (§7.2). It owns the train aggregate, physics, equipment, linear
signals, named digital I/O, and the immutable snapshot types the aggregate
publishes.
"""

from __future__ import annotations

from .equipment import (
    BtmDelivery,
    BtmEquipment,
    Cab,
    CabState,
    DigitalIo,
    Door,
    DoorCommand,
    IoBits,
    IoConfig,
    IoNamed,
)
from .io import DEFAULT_ATP_TO_TRAIN, DEFAULT_TRAIN_TO_ATP, IoMapping, SignalDef
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
    IoSnapshot,
    TrainSnapshot,
)
from .train import ControlResult, Train, TrainConfig, TrainControl

__all__ = [
    "BtmDelivery",
    "BtmEquipment",
    "BtmSnapshot",
    "Cab",
    "CabSnapshot",
    "CabState",
    "ControlResult",
    "DEFAULT_ATP_TO_TRAIN",
    "DEFAULT_TRAIN_TO_ATP",
    "DigitalIo",
    "Door",
    "DoorCommand",
    "DoorSnapshot",
    "IoBits",
    "IoConfig",
    "IoMapping",
    "IoNamed",
    "IoSnapshot",
    "SignalDef",
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
