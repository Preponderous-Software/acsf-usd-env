"""``acsf-usd validate``: re-read a stage and check it against its source journal(s).

The expectations are computed here from the indexed journal, independently of the channel
writer: for every cycle of the timeline (first to last journaled cycle), each text channel
must hold exactly the journal ``content`` of that cycle's events of its type (joined with a
newline when there are several), byte-for-byte, and ``""`` on cycles without one. The stage's
``acsf:cycleIndexing`` must name the rule the indexer applies to the journal.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pxr import Sdf, Usd

from acsf_usd.model import InstanceRun
from acsf_usd.stage.common import instance_path, prim_name
from acsf_usd.stage.compose import disambiguate

# Journal event type -> (child prim, attribute) holding its content.
TEXT_CHANNELS: dict[str, tuple[str, str]] = {
    "thought": ("Stream", "acsf:thought"),
    "reflection": ("Stream", "acsf:reflection"),
    "perception": ("Stimulus", "acsf:perception"),
}


@dataclass
class InstanceReport:
    name: str
    prim: str
    cycles: int = 0
    checked: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def validate_instance(stage: Usd.Stage, run: InstanceRun, prim_name_: str) -> InstanceReport:
    report = InstanceReport(name=run.name, prim=prim_name_)
    path = instance_path(prim_name_)
    instance = stage.GetPrimAtPath(path)
    if not instance.IsValid():
        report.problems.append(f"{path}: instance prim not found")
        return report

    indexing = _layer_value(instance, "acsf:cycleIndexing")
    if indexing != run.journal.indexing:
        report.problems.append(
            f"acsf:cycleIndexing is {indexing!r}, the journal needs {run.journal.indexing!r}"
        )

    expected: dict[tuple[str, str], dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for indexed in run.journal.events:
        event = indexed.event
        if event.type in TEXT_CHANNELS:
            expected[TEXT_CHANNELS[event.type]][indexed.cycle].append(event.content)
        elif event.type == "utterance":
            to = event.extras.get("to")
            sender = to if isinstance(to, str) and to else "unknown"
            expected[("Interlocutors", sender)][indexed.cycle].append(event.content)
            report.checked["utterance"] = report.checked.get("utterance", 0) + 1
        if event.type in TEXT_CHANNELS:
            report.checked[event.type] = report.checked.get(event.type, 0) + 1

    cycles = run.journal.cycles
    timeline = list(range(cycles[0], cycles[-1] + 1)) if cycles else []
    report.cycles = len(timeline)

    channels: list[tuple[str, Usd.Attribute | None, dict[int, list[str]]]] = []
    for child, attr_name in TEXT_CHANNELS.values():
        label = f"{child}.{attr_name}"
        channels.append(
            (
                label,
                _attribute(stage, path.AppendChild(child), attr_name),
                expected[(child, attr_name)],
            )
        )
    stage_senders = _interlocutors(stage, path)
    journal_senders = {key[1] for key in expected if key[0] == "Interlocutors"}
    # Recipients present only in the stage are checked against empty expectations, so a
    # reply with no source event is reported.
    for sender in sorted(journal_senders | set(stage_senders)):
        values = expected[("Interlocutors", sender)] if sender in journal_senders else {}
        channels.append(
            (f"Interlocutors[{sender}].acsf:utterance", stage_senders.get(sender), values)
        )

    for label, attr, values in channels:
        if attr is None:
            if timeline:
                report.problems.append(f"{label}: attribute missing")
            continue
        samples = set(attr.GetTimeSamples())
        for cycle in timeline:
            want = "\n".join(values.get(cycle, []))
            if float(cycle) not in samples:
                report.problems.append(f"{label} cycle {cycle}: no time sample")
                continue
            got = attr.Get(Usd.TimeCode(cycle))
            if got != want:
                report.problems.append(
                    f"{label} cycle {cycle}: stage has {_short(got)}, journal has {_short(want)}"
                )
        extra = sorted(t for t in samples if not timeline or t < timeline[0] or t > timeline[-1])
        if extra:
            report.problems.append(f"{label}: samples outside the journal's cycles: {extra[:5]}")
    return report


def _attribute(stage: Usd.Stage, prim_path: Sdf.Path, name: str) -> Usd.Attribute | None:
    """The attribute, or ``None`` when the prim or the attribute is absent."""
    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        return None
    attr = prim.GetAttribute(name)
    return attr if attr.IsValid() else None


def _interlocutors(stage: Usd.Stage, instance: Sdf.Path) -> dict[str, Usd.Attribute | None]:
    """Each interlocutor's ``acsf:utterance`` keyed by its ``acsf:sender`` (``None`` when the
    attribute is missing)."""
    result: dict[str, Usd.Attribute | None] = {}
    parent = stage.GetPrimAtPath(instance.AppendChild("Interlocutors"))
    if not parent.IsValid():
        return result
    for prim in parent.GetChildren():
        sender = prim.GetAttribute("acsf:sender")
        if sender.IsValid():
            result[str(sender.Get())] = _attribute(stage, prim.GetPath(), "acsf:utterance")
    return result


def _layer_value(prim: Usd.Prim, key: str) -> object:
    """A ``customLayerData`` value from the strongest layer that has a spec for ``prim`` and
    records ``key`` (the instance layer, in a composed stage)."""
    for spec in prim.GetPrimStack():
        data = spec.layer.customLayerData
        if key in data:
            return data[key]
    return None


def instance_prims(stage: Usd.Stage, runs: Sequence[InstanceRun]) -> list[str]:
    """The prim each run was exported to: the export's own naming, or — for a single run —
    the one instance prim whose ``acsf:name`` matches."""
    if len(runs) != 1:
        return disambiguate(runs)
    run = runs[0]
    name = prim_name(run.name)
    if stage.GetPrimAtPath(instance_path(name)).IsValid():
        return [name]
    parent = stage.GetPrimAtPath("/World/Instances")
    matches = [
        child.GetName()
        for child in (parent.GetChildren() if parent.IsValid() else [])
        if child.GetAttribute("acsf:name").Get() == run.name
    ]
    return [matches[0]] if len(matches) == 1 else [name]


def validate(stage_path: Path, runs: Sequence[InstanceRun]) -> list[InstanceReport]:
    stage = Usd.Stage.Open(str(stage_path))
    if stage is None:
        raise OSError(f"{stage_path}: not a USD stage")
    prims = instance_prims(stage, runs)
    return [validate_instance(stage, run, prim) for run, prim in zip(runs, prims, strict=True)]


def format_report(report: InstanceReport) -> list[str]:
    counts = ", ".join(f"{n} {kind}" for kind, n in sorted(report.checked.items()))
    status = "OK" if not report.problems else f"{len(report.problems)} problem(s)"
    lines = [f"{report.name} ({report.prim}): {status} — {report.cycles} cycles; {counts}"]
    lines.extend(f"  {problem}" for problem in report.problems)
    return lines
