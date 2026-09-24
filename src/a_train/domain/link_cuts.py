"""Physical link cuts: equipment-wire fault state owned by the train aggregate (§3.7).

A cut drops every intent delivery whose concrete physical wire matches:
the intent source (an equipment key or a ``cab_<id>`` broadcast source)
to one concrete recipient instance (an equipment key or ``train``).
A cut wire is stale, not zeroed: the recipient keeps its last delivered
value until the wire is restored, and because every intent re-asserts
current state, restores self-heal on the next delivery over that wire.
"""

from __future__ import annotations

from collections.abc import Iterable

from .snapshots import LinkCutSnapshot


class LinkCuts:
    """Mutable ``source -> target`` cut set with deterministic snapshots."""

    def __init__(self) -> None:
        self._cuts: set[tuple[str, str]] = set()

    def is_cut(self, source: str, target: str) -> bool:
        return (source, target) in self._cuts

    def replace(self, pairs: Iterable[tuple[str, str]]) -> None:
        self._cuts = set(pairs)

    def add(self, pairs: Iterable[tuple[str, str]]) -> None:
        self._cuts.update(pairs)

    def remove(self, pairs: Iterable[tuple[str, str]]) -> None:
        self._cuts.difference_update(pairs)

    def clear(self) -> None:
        self._cuts.clear()

    def snapshot(self) -> tuple[LinkCutSnapshot, ...]:
        return tuple(
            LinkCutSnapshot(source=source, target=target) for source, target in sorted(self._cuts)
        )
