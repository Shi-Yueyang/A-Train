"""Train-facing equipment components and their factory registry."""

from .base import Equipment, EquipmentContext, EquipmentIntent, TrainMotion
from .btm import Btm
from .door import Door
from .driving_law import (
    DRIVING_LAWS,
    DrivingLaw,
    DrivingTargets,
    HoldSpeedLaw,
    StoppingEnvelopeLaw,
)
from .driving_system import DrivingSystem
from .registry import EQUIPMENT_FACTORIES
from .stcs_atp import SignalDefinition, StcsAtpBase, StcsAtpDuo, StcsAtpSolo
from .switch_box import SwitchBox

__all__ = [
    "Btm",
    "DRIVING_LAWS",
    "Door",
    "DrivingLaw",
    "DrivingSystem",
    "DrivingTargets",
    "HoldSpeedLaw",
    "EQUIPMENT_FACTORIES",
    "Equipment",
    "EquipmentContext",
    "EquipmentIntent",
    "StoppingEnvelopeLaw",
    "TrainMotion",
    "StcsAtpBase",
    "StcsAtpDuo",
    "StcsAtpSolo",
    "SignalDefinition",
    "SwitchBox",
]
