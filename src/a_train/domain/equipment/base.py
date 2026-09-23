"""Shared protocols and values for train-facing equipment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from ..controls import Control, EquipmentControl

EquipmentControlT = TypeVar("EquipmentControlT", bound=EquipmentControl)


@dataclass(frozen=True)
class EquipmentIntent:
    """A reference-free request for the train aggregate to resolve."""

    source: str
    target: str
    control: Control


@runtime_checkable
class Equipment(Protocol[EquipmentControlT]):
    """Common lifecycle interface for pluggable equipment instances."""

    @property
    def type(self) -> str: ...

    @property
    def key(self) -> str: ...

    def apply_control(
        self, control: EquipmentControlT, *, received_at: float | None = None
    ) -> None:
        """Apply one control. ``received_at`` is the wall-clock time (POSIX
        seconds) at which the core applied an external command; ``None`` marks
        internal intent-driven applications, which carry no arrival time.
        Equipment that records command arrival uses it, e.g. STCS ATP's
        ``last_command_time``."""
        ...

    def read_state(self) -> Any: ...

    def reset(self) -> None: ...

    def emit_intents(self) -> tuple[EquipmentIntent, ...]: ...


@dataclass(frozen=True, kw_only=True)
class EquipmentContext:
    """Train-scope configuration shared with equipment factories."""

    initial_door_state: str
    cab_facings: dict[int, int] = field(default_factory=dict)
