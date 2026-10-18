"""Assign every journal event the ACSF thought cycle it belongs to.

USD time code = ACSF cycle number (``thought_count``). Rules (ARCHITECTURE.md § Cycle
indexing rules):

1. When the journal contains ``cycle`` events (ACSF #196), each one's ``thought_count`` is
   authoritative for the events of its cycle: the preceding ``perception`` and ``thought``
   and the following ``utterance``, ``reflection``, ``identity_shift`` and ``memory``.
2. Without ``cycle`` events, ``thought`` events are counted from 1: a ``perception`` belongs
   to the next ``thought``; everything after a ``thought`` up to the next
   ``perception``/``thought`` belongs to it. Failed cycles write no ``thought``, so counted
   indices can undercount.
3. ``consolidation`` events come from a background timer; they are attached to the most
   recent cycle and flagged ``background``.

Events are grouped by journal write order. A group starts at a ``perception`` or ``thought``
once the current group already has its ``thought``; any other event joins the current group.
In a journal that has ``cycle`` events, a group without one (for example a stretch written
before ACSF #196) is numbered one past the previous group.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from acsf_usd.model import CycleIndexing, Event, IndexedEvent, IndexedJournal

log = logging.getLogger(__name__)

_GROUP_STARTERS = frozenset({"perception", "thought"})


@dataclass
class _Group:
    events: list[Event] = field(default_factory=list)
    has_thought: bool = False
    journaled: int | None = None


def index_events(events: Sequence[Event]) -> IndexedJournal:
    """Index a whole journal. Pure: the same events always give the same result."""
    indexing: CycleIndexing = (
        "journaled" if any(e.type == "cycle" and e.cycle is not None for e in events) else "counted"
    )
    groups: list[_Group] = []
    # Each background event with the index of the group it attaches to (-1: no thought yet).
    background: list[tuple[Event, int]] = []
    # Journal order: ("ev", group index, event) or ("bg", background index, event).
    order: list[tuple[str, int, Event]] = []
    current = _Group()
    groups.append(current)
    last_with_thought = -1
    for event in events:
        if event.type == "consolidation":
            background.append((event, last_with_thought))
            order.append(("bg", len(background) - 1, event))
            continue
        if event.type in _GROUP_STARTERS and current.has_thought:
            current = _Group()
            groups.append(current)
        current.events.append(event)
        order.append(("ev", len(groups) - 1, event))
        if event.type == "thought":
            current.has_thought = True
            last_with_thought = len(groups) - 1
        elif event.type == "cycle" and event.cycle is not None and current.journaled is None:
            current.journaled = event.cycle.thought_count

    numbers = _number_groups(groups, indexing)
    # Background events seen before any thought attach to the first cycle; a journal with
    # only background events has no cycles and uses cycle 0.
    first_cycle = numbers[0] if groups[0].events else 0
    indexed: list[IndexedEvent] = []
    for kind, ref, event in order:
        if kind == "ev":
            indexed.append(IndexedEvent(event=event, cycle=numbers[ref]))
        else:
            attached = background[ref][1]
            cycle = numbers[attached] if attached >= 0 else first_cycle
            indexed.append(IndexedEvent(event=event, cycle=cycle, background=True))
    return IndexedJournal(events=tuple(indexed), indexing=indexing)


def _number_groups(groups: list[_Group], indexing: CycleIndexing) -> list[int]:
    numbers: list[int] = []
    previous = 0
    for group in groups:
        if indexing == "journaled" and group.journaled is not None:
            number = group.journaled
            if number <= previous and numbers:
                log.warning(
                    "cycle thought_count %d does not advance past cycle %d", number, previous
                )
        else:
            number = previous + 1
        numbers.append(number)
        previous = number
    return numbers
