"""In-memory run model shared by the readers, the stage writers and follow mode.

Nothing here holds long-term memory text: a ``cycle`` event's ``retrieved`` summaries
are reduced to a count when the journal is parsed (ARCHITECTURE.md § Decisions, 3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CycleIndexing = Literal["journaled", "counted"]


@dataclass(frozen=True)
class CycleTrace:
    """The structured fields of an ACSF ``cycle`` event (ACSF #196) that the stage uses."""

    thought_count: int
    metacognitive_label: str | None = None
    prediction_error: float | None = None
    reflection_effective: float | None = None
    reflection_fired: bool | None = None
    short_term_size: int | None = None
    retrieved_count: int | None = None
    attention_focus: str | None = None
    attention_theme: str | None = None
    attention_salience: float | None = None
    mood: Mapping[str, float] | None = None


@dataclass(frozen=True)
class Event:
    """One journal line that parsed as a JSON object with a string ``type``."""

    line_no: int
    type: str
    timestamp: str
    content: str
    extras: Mapping[str, Any] = field(default_factory=dict)
    cycle: CycleTrace | None = None


@dataclass(frozen=True)
class IndexedEvent:
    """An event with the ACSF thought cycle it belongs to."""

    event: Event
    cycle: int
    background: bool = False


@dataclass(frozen=True)
class WorkspaceItem:
    """One short-term workspace entry from ``state.json``."""

    kind: str
    content: str
    timestamp: str
    importance: float
    seq: int | None = None


@dataclass(frozen=True)
class Snapshot:
    """The ``state.json`` snapshot: identity, mood, attention schema and workspace."""

    name: str | None = None
    origin_story: str | None = None
    purpose: str | None = None
    self_concept: str | None = None
    values: tuple[str, ...] = ()
    personality_traits: tuple[str, ...] = ()
    amendments: tuple[str, ...] = ()
    mood: Mapping[str, float] = field(default_factory=dict)
    initial_mood: Mapping[str, float] = field(default_factory=dict)
    attention_focus: str | None = None
    attention_theme: str | None = None
    attention_salience: float | None = None
    short_term: tuple[WorkspaceItem, ...] = ()
    thought_count: int | None = None


@dataclass(frozen=True)
class IndexedJournal:
    """Every event of a journal with its cycle, and which indexing rule produced them."""

    events: tuple[IndexedEvent, ...]
    indexing: CycleIndexing

    @property
    def cycles(self) -> tuple[int, ...]:
        """Sorted distinct cycle numbers that carry at least one event."""
        return tuple(sorted({e.cycle for e in self.events}))


@dataclass(frozen=True)
class InstanceRun:
    """One ACSF instance as read from disk: its journal, indexed, and its snapshot."""

    name: str
    source_dir: Path
    journal: IndexedJournal
    snapshot: Snapshot | None
    warnings: tuple[str, ...] = ()
