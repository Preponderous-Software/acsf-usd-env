"""Author the time-sampled event channels of one instance.

USD time code = ACSF cycle number. String/token channels get a sample on **every** cycle of
the timeline — the cycle's value, or ``""`` when the channel has no event that cycle — so a
held string never carries over into a later cycle. Numeric channels (``Cycle``,
``Attention``, ``Memory.acsf:longTermCount``, ``Mood``) are sampled only on cycles whose
event carries them. Several events of one channel in one cycle are joined with ``"\\n"``.

Long-term memory appears as counts only (ARCHITECTURE.md § Decisions, 3).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from pxr import Sdf, Usd, UsdGeom

from acsf_usd.model import CycleTrace, IndexedEvent, IndexedJournal
from acsf_usd.stage.common import unique_names

T = Sdf.ValueTypeNames

KNOWN_TYPES = frozenset(
    {
        "perception",
        "thought",
        "cycle",
        "utterance",
        "reflection",
        "identity_shift",
        "memory",
        "consolidation",
    }
)
_SOURCE = re.compile(r"^\[([^:\]]*):")
UNKNOWN_SENDER = "unknown"


def perception_source(content: str) -> str:
    """The provider name ACSF prefixes a perception with (``"[<source>: <title>] ..."``,
    ``core/consciousness.py`` L954–L956 at 64486d9), or ``""``."""
    match = _SOURCE.match(content)
    return match.group(1).strip() if match else ""


def join(values: Iterable[str]) -> str:
    return "\n".join(values)


@dataclass
class CycleData:
    """Everything one cycle contributes to the channels."""

    thoughts: list[str] = field(default_factory=list)
    reflections: list[str] = field(default_factory=list)
    perceptions: list[str] = field(default_factory=list)
    first_timestamp: str = ""
    thought_timestamp: str = ""
    identity_shift: str | None = None
    trace: CycleTrace | None = None
    long_term_count: int | None = None
    consolidation_stored: int = 0
    utterances: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    unknown: dict[str, list[str]] = field(default_factory=dict)

    @property
    def timestamp(self) -> str:
        """The cycle's wall-clock time: its thought's timestamp, else its first event's."""
        return self.thought_timestamp or self.first_timestamp


def collect(events: Sequence[IndexedEvent]) -> dict[int, CycleData]:
    """Group indexed events by cycle, in journal order."""
    cycles: dict[int, CycleData] = defaultdict(CycleData)
    for indexed in events:
        event = indexed.event
        data = cycles[indexed.cycle]
        kind = event.type
        if not data.first_timestamp:
            data.first_timestamp = event.timestamp
        if kind == "thought":
            data.thoughts.append(event.content)
            if not data.thought_timestamp:
                data.thought_timestamp = event.timestamp
        elif kind == "reflection":
            data.reflections.append(event.content)
        elif kind == "perception":
            data.perceptions.append(event.content)
        elif kind == "identity_shift":
            data.identity_shift = event.content
        elif kind == "cycle":
            if event.cycle is not None:
                data.trace = event.cycle
        elif kind == "memory":
            count = _int(event.extras.get("long_term_count"))
            if count is not None:
                data.long_term_count = count
        elif kind == "consolidation":
            total = _int(event.extras.get("long_term_total"))
            if total is not None:
                data.long_term_count = total
            data.consolidation_stored += _int(event.extras.get("stored")) or 0
        elif kind == "utterance":
            to = event.extras.get("to")
            sender = to if isinstance(to, str) and to else UNKNOWN_SENDER
            reply = event.extras.get("in_reply_to")
            data.utterances.setdefault(sender, []).append(
                (event.content, reply if isinstance(reply, str) else "")
            )
        elif kind not in KNOWN_TYPES:
            data.unknown.setdefault(kind, []).append(event.content)
    return dict(cycles)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def sender_prim_names(senders: Iterable[str]) -> dict[str, str]:
    """Deterministic, collision-free prim names for interlocutors (sorted, ``_2``, ``_3`` …)."""
    return unique_names(senders)


def journaled_mood_dims(journal: IndexedJournal) -> set[str]:
    """Every mood dimension any ``cycle`` event carries."""
    return {
        dim
        for indexed in journal.events
        if indexed.event.cycle is not None and indexed.event.cycle.mood is not None
        for dim in indexed.event.cycle.mood
    }


@dataclass(frozen=True)
class ChannelSummary:
    first_cycle: int
    last_cycle: int
    mood_first_cycle: int | None
    interlocutors: dict[str, str]
    perception_cycles: tuple[int, ...]
    utterance_cycles: dict[str, tuple[int, ...]]
    timeline: tuple[int, ...]


def author_channels(
    stage: Usd.Stage,
    instance: Sdf.Path,
    journal: IndexedJournal,
    mood_names: dict[str, str] | None = None,
) -> ChannelSummary:
    cycles = collect(journal.events)
    timeline = tuple(sorted(cycles))
    senders = sender_prim_names(s for d in cycles.values() for s in d.utterances)
    summary = ChannelSummary(
        first_cycle=timeline[0] if timeline else 0,
        last_cycle=timeline[-1] if timeline else 0,
        mood_first_cycle=next(
            (c for c in timeline if (t := cycles[c].trace) is not None and t.mood is not None),
            None,
        ),
        interlocutors=senders,
        perception_cycles=tuple(c for c in timeline if cycles[c].perceptions),
        utterance_cycles={
            s: tuple(c for c in timeline if s in cycles[c].utterances) for s in senders
        },
        timeline=timeline,
    )
    if not timeline:
        return summary

    def dense(prim: Usd.Prim, name: str, type_name: object, values: dict[int, str]) -> None:
        attr = prim.CreateAttribute(name, type_name, True)
        for cycle in timeline:
            attr.Set(values.get(cycle, ""), Usd.TimeCode(cycle))

    def sparse(child: str, name: str, type_name: object, values: dict[int, object]) -> None:
        if not values:
            return
        prim = stage.DefinePrim(instance.AppendChild(child))
        attr = prim.CreateAttribute(name, type_name, True)
        for cycle in sorted(values):
            attr.Set(values[cycle], Usd.TimeCode(cycle))

    stream = stage.DefinePrim(instance.AppendChild("Stream"))
    dense(stream, "acsf:thought", T.String, {c: join(d.thoughts) for c, d in cycles.items()})
    dense(stream, "acsf:reflection", T.String, {c: join(d.reflections) for c, d in cycles.items()})
    dense(stream, "acsf:timestamp", T.String, {c: d.timestamp for c, d in cycles.items()})
    unknown_names = unique_names(t for d in cycles.values() for t in d.unknown)
    for kind in sorted(unknown_names):
        dense(
            stream,
            f"acsf:event:{unknown_names[kind]}",
            T.String,
            {c: join(d.unknown[kind]) for c, d in cycles.items() if kind in d.unknown},
        )

    stimulus = UsdGeom.Xform.Define(stage, instance.AppendChild("Stimulus")).GetPrim()
    dense(
        stimulus,
        "acsf:perception",
        T.String,
        {c: join(d.perceptions) for c, d in cycles.items()},
    )
    dense(
        stimulus,
        "acsf:perceptionSource",
        T.Token,
        {c: perception_source(d.perceptions[-1]) for c, d in cycles.items() if d.perceptions},
    )

    if senders:
        UsdGeom.Xform.Define(stage, instance.AppendChild("Interlocutors"))
    for sender, name in sorted(senders.items(), key=lambda item: item[1]):
        path = instance.AppendChild("Interlocutors").AppendChild(name)
        prim = UsdGeom.Xform.Define(stage, path).GetPrim()
        prim.CreateAttribute("acsf:sender", T.String, True).Set(sender)
        said = {c: d.utterances[sender] for c, d in cycles.items() if sender in d.utterances}
        dense(prim, "acsf:utterance", T.String, {c: join(u for u, _ in v) for c, v in said.items()})
        dense(prim, "acsf:inReplyTo", T.String, {c: join(r for _, r in v) for c, v in said.items()})

    traces = {c: d.trace for c, d in cycles.items() if d.trace is not None}
    for name, type_name, getter in (
        ("acsf:hot2Label", T.Token, lambda t: t.metacognitive_label),
        ("acsf:predictionError", T.Double, lambda t: t.prediction_error),
        ("acsf:reflectionP", T.Double, lambda t: t.reflection_effective),
        ("acsf:reflectionFired", T.Bool, lambda t: t.reflection_fired),
        ("acsf:workspaceSize", T.Int, lambda t: t.short_term_size),
        ("acsf:retrievedCount", T.Int, lambda t: t.retrieved_count),
    ):
        sparse("Cycle", name, type_name, _present(traces, getter))

    sparse("Attention", "acsf:focus", T.Token, _present(traces, lambda t: t.attention_focus))
    sparse("Attention", "acsf:theme", T.String, _present(traces, lambda t: t.attention_theme))
    sparse("Attention", "acsf:salience", T.Double, _present(traces, lambda t: t.attention_salience))

    moods = {c: t.mood for c, t in traces.items() if t.mood is not None}
    if moods:
        dims = mood_names or unique_names(d for m in moods.values() for d in m)
        for dim in sorted({d for m in moods.values() for d in m}):
            sparse(
                "Mood",
                f"acsf:mood:{dims[dim]}",
                T.Double,
                {c: m[dim] for c, m in moods.items() if dim in m},
            )

    sparse(
        "Memory",
        "acsf:longTermCount",
        T.Int,
        {c: d.long_term_count for c, d in cycles.items() if d.long_term_count is not None},
    )
    memory = stage.DefinePrim(instance.AppendChild("Memory"))
    stored = memory.CreateAttribute("acsf:consolidationStored", T.Int, True)
    for cycle in timeline:
        stored.Set(cycles[cycle].consolidation_stored, Usd.TimeCode(cycle))

    shifts = {c: d.identity_shift for c, d in cycles.items() if d.identity_shift is not None}
    if shifts:
        values: dict[int, object] = dict(shifts)
        # USD holds the first sample backwards in time; an empty value before the first
        # identity_shift keeps the shifted self-concept from appearing on earlier cycles.
        if timeline[0] not in values:
            values[timeline[0]] = ""
        sparse("Identity", "acsf:selfConcept", T.String, values)

    return summary


def _present(
    traces: dict[int, CycleTrace], getter: Callable[[CycleTrace], object]
) -> dict[int, object]:
    result: dict[int, object] = {}
    for cycle, trace in traces.items():
        value = getter(trace)
        if value is not None:
            result[cycle] = value
    return result
