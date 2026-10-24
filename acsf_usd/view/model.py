"""Read an exported stage into what the viewer draws.

The viewer's only input is a stage written by ``acsf-usd export`` (a single instance, or a
multi-instance root layer). It never opens an ACSF instance directory, so memory text and
embeddings, which the exporter leaves out, are never within reach.

Values are kept as their time samples. :func:`held` resolves a channel at a cycle the way USD
does for strings: the latest sample at or before it, held until the next one, never blended.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar

from pxr import Sdf, Usd, UsdGeom

from acsf_usd.schematic import Vec3

KNOWN_EXPORTER_MAJORS = (0,)

T = TypeVar("T")


class StageError(Exception):
    """The file is not a stage the viewer can draw."""


@dataclass(frozen=True)
class Channel(Generic[T]):
    """One attribute: its time samples, and its default (unsampled) value."""

    samples: Mapping[float, T] = field(default_factory=dict)
    default: T | None = None

    @property
    def sampled(self) -> bool:
        return bool(self.samples)

    @property
    def present(self) -> bool:
        return self.sampled or self.default is not None

    def at(self, cycle: float) -> T | None:
        if self.samples:
            return held(self.samples, cycle)
        return self.default

    def exact(self, cycle: float) -> T | None:
        """The sample authored at exactly this cycle (event channels), else None."""
        return self.samples.get(cycle)


def held(samples: Mapping[float, T], cycle: float) -> T | None:
    """USD's held interpolation: the latest sample at or before ``cycle``; before the first
    sample, the first one (USD holds the first sample backwards in time)."""
    if not samples:
        return None
    times = sorted(samples)
    i = bisect.bisect_right(times, cycle) - 1
    return samples[times[max(i, 0)]]


@dataclass(frozen=True)
class Interlocutor:
    name: str
    sender: str
    position: Vec3  # the stage's own synthetic ring position, world space
    utterance: Channel[str]
    in_reply_to: Channel[str]


@dataclass(frozen=True)
class InstanceView:
    name: str
    prim_name: str
    origin: Vec3
    stimulus: Vec3
    stimulus_radius: float
    thoughts: dict[float, str]  # cycles with a non-empty thought
    reflections: dict[float, str]
    perceptions: dict[float, str]
    perception_source: Channel[str]
    interlocutors: list[Interlocutor]
    mood: dict[str, Channel[float]]
    mood_baseline: dict[str, float]
    mood_sampling: str  # "per-cycle" or "final"
    attention: dict[str, Channel[Any]]
    cycle: dict[str, Channel[Any]]
    memory: dict[str, Channel[int]]
    cycle_indexing: str | None

    @property
    def thought_cycles(self) -> list[float]:
        return sorted(self.thoughts)

    @property
    def longest_thought(self) -> int:
        return max((len(t) for t in self.thoughts.values()), default=1)


@dataclass(frozen=True)
class RunView:
    path: Path
    start: float
    end: float
    time_codes_per_second: float
    exporter_version: str
    instances: list[InstanceView]

    @property
    def cycles(self) -> list[int]:
        return list(range(int(self.start), int(self.end) + 1))


def _channel(prim: Usd.Prim | None, attr: str) -> Channel[Any]:
    if prim is None or not prim.IsValid() or not prim.HasAttribute(attr):
        return Channel()
    a = prim.GetAttribute(attr)
    samples = {float(t): _plain(a.Get(t)) for t in a.GetTimeSamples()}
    default = a.Get(Usd.TimeCode.Default()) if a.HasAuthoredValue() else None
    return Channel(samples, _plain(default) if default is not None else None)


def _plain(value: Any) -> Any:
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)


def _events(prim: Usd.Prim | None, attr: str) -> dict[float, str]:
    """Cycles at which a dense string channel holds a non-empty value."""
    ch = _channel(prim, attr)
    return {t: str(v) for t, v in ch.samples.items() if v}


def _layer_data(stage: Usd.Stage) -> dict[str, Any]:
    return dict(stage.GetRootLayer().customLayerData)


def _instance_layer_data(stage: Usd.Stage) -> dict[str, dict[str, Any]]:
    """customLayerData of each sublayer of a multi-instance root, keyed by instance prim name."""
    out: dict[str, dict[str, Any]] = {}
    root = stage.GetRootLayer()
    for sub in root.subLayerPaths:
        layer = Sdf.Layer.FindOrOpenRelativeToLayer(root, sub)
        if layer is None:
            continue
        inst = layer.GetPrimAtPath("/World/Instances")
        if inst is None:
            continue
        for child in inst.nameChildren:
            out[child.name] = dict(layer.customLayerData)
    return out


def check_metadata(data: Mapping[str, Any], path: Path) -> str:
    """Refuse stages acsf-usd-env did not write, or whose exporter major is unknown."""
    if not any(str(k).startswith("acsf:") for k in data):
        raise StageError(f"{path}: not an acsf-usd-env stage (no acsf:* layer metadata)")
    version = str(data.get("acsf:exporterVersion", ""))
    try:
        major = int(version.split(".")[0])
    except ValueError:
        raise StageError(f"{path}: unreadable acsf:exporterVersion {version!r}") from None
    if major not in KNOWN_EXPORTER_MAJORS:
        from acsf_usd import __version__

        raise StageError(
            f"{path}: written by acsf-usd-env {version}, which this viewer (acsf-usd-env "
            f"{__version__}) does not know; open it with a matching acsf-usd-env"
        )
    return version


def load_run(path: Path) -> RunView:
    if not path.is_file():
        raise StageError(f"{path}: no such file")
    try:
        stage = Usd.Stage.Open(str(path))
    except Exception as exc:  # pxr raises Tf.ErrorException for unreadable layers
        raise StageError(f"{path}: cannot be opened as a USD stage ({exc})") from None
    if stage is None:
        raise StageError(f"{path}: cannot be opened as a USD stage")
    data = _layer_data(stage)
    version = check_metadata(data, path)
    per_layer = _instance_layer_data(stage)
    parent = stage.GetPrimAtPath("/World/Instances")
    if not parent:
        raise StageError(f"{path}: no /World/Instances in the stage")
    xcache = UsdGeom.XformCache(Usd.TimeCode.Default())
    instances = []
    for prim in parent.GetChildren():
        layer_data = per_layer.get(prim.GetName(), data)
        instances.append(_read_instance(prim, xcache, layer_data))
    instances.sort(key=lambda i: (i.origin[0], i.name))
    if not instances:
        raise StageError(f"{path}: the stage holds no instances")
    return RunView(
        path=path,
        start=float(stage.GetStartTimeCode()),
        end=float(stage.GetEndTimeCode()),
        time_codes_per_second=float(stage.GetTimeCodesPerSecond() or 1.0),
        exporter_version=version,
        instances=instances,
    )


def _vec(v: Any) -> Vec3:
    return (float(v[0]), float(v[1]), float(v[2]))


def _read_instance(
    inst: Usd.Prim, xcache: UsdGeom.XformCache, layer_data: Mapping[str, Any]
) -> InstanceView:
    def child(rel: str) -> Usd.Prim | None:
        p = inst.GetPrimAtPath(rel)
        return p if p and p.IsValid() else None

    origin = _vec(xcache.GetLocalToWorldTransform(inst).ExtractTranslation())
    stream, stim = child("Stream"), child("Stimulus")
    sphere = child("Stimulus/Sphere")
    if sphere is not None:
        spos = _vec(xcache.GetLocalToWorldTransform(sphere).ExtractTranslation())
        srad = float(UsdGeom.Sphere(sphere).GetRadiusAttr().Get())
    else:
        spos, srad = (origin[0], origin[1] + 1.5, origin[2]), 0.4

    interlocutors = []
    group = child("Interlocutors")
    for p in group.GetChildren() if group is not None else []:
        sender = p.GetAttribute("acsf:sender").Get() if p.HasAttribute("acsf:sender") else None
        interlocutors.append(
            Interlocutor(
                name=p.GetName(),
                sender=str(sender or p.GetName()),
                position=_vec(xcache.GetLocalToWorldTransform(p).ExtractTranslation()),
                utterance=_channel(p, "acsf:utterance"),
                in_reply_to=_channel(p, "acsf:inReplyTo"),
            )
        )

    mood_prim = child("Mood")
    mood: dict[str, Channel[float]] = {}
    baseline: dict[str, float] = {}
    for a in mood_prim.GetAttributes() if mood_prim is not None else []:
        n = a.GetName()
        if n.startswith("acsf:mood:"):
            mood[n.removeprefix("acsf:mood:")] = _channel(mood_prim, n)
        elif n.startswith("acsf:moodBaseline:") and a.Get() is not None:
            baseline[n.removeprefix("acsf:moodBaseline:")] = float(a.Get())
    sampling = layer_data.get("acsf:moodSampling")
    if sampling is None:
        sampling = "per-cycle" if any(c.sampled for c in mood.values()) else "final"
    elif sampling != "final":
        sampling = "per-cycle"

    name_attr = inst.GetAttribute("acsf:name")
    indexing = layer_data.get("acsf:cycleIndexing")
    return InstanceView(
        name=str(name_attr.Get()) if name_attr and name_attr.Get() else inst.GetName(),
        prim_name=inst.GetName(),
        origin=origin,
        stimulus=spos,
        stimulus_radius=srad,
        thoughts=_events(stream, "acsf:thought"),
        reflections=_events(stream, "acsf:reflection"),
        perceptions=_events(stim, "acsf:perception"),
        perception_source=_channel(stim, "acsf:perceptionSource"),
        interlocutors=interlocutors,
        mood=dict(sorted(mood.items())),
        mood_baseline=baseline,
        mood_sampling=str(sampling),
        attention={
            k: _channel(child("Attention"), f"acsf:{k}") for k in ("focus", "theme", "salience")
        },
        cycle={
            k: _channel(child("Cycle"), f"acsf:{k}")
            for k in ("hot2Label", "predictionError", "reflectionP", "reflectionFired")
        },
        memory={
            k: _channel(child("Memory"), f"acsf:{k}")
            for k in ("longTermCount", "consolidationStored")
        },
        cycle_indexing=str(indexing) if indexing is not None else None,
    )
