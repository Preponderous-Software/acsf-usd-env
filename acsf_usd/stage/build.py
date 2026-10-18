"""Build one instance's stage from the run model and write it to disk."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pxr import Sdf, Usd, UsdGeom

from acsf_usd import __version__
from acsf_usd.model import InstanceRun
from acsf_usd.stage.channels import ChannelSummary, author_channels, journaled_mood_dims
from acsf_usd.stage.common import INSTANCES, WORLD, instance_path, set_attr, unique_names
from acsf_usd.stage.layout import author_layout
from acsf_usd.stage.snapshot import author_snapshot

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExportOptions:
    time_codes_per_second: float = 1.0
    source_commit: str | None = None


def build_instance_stage(
    run: InstanceRun, options: ExportOptions | None = None, prim: str | None = None
) -> Usd.Stage:
    """Author one instance into a new in-memory stage.

    ``prim`` overrides the instance prim name (multi-instance export disambiguates names);
    ``acsf:name`` always keeps the run's own name.
    """
    options = options or ExportOptions()
    stage = Usd.Stage.CreateInMemory()
    world = UsdGeom.Xform.Define(stage, WORLD)
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, INSTANCES)

    path = instance_path(prim if prim is not None else run.name)
    instance = UsdGeom.Xform.Define(stage, path).GetPrim()
    set_attr(instance, "acsf:name", Sdf.ValueTypeNames.String, run.name)
    # One mood-dimension naming for the snapshot and the journaled samples.
    dims = set(journaled_mood_dims(run.journal))
    if run.snapshot is not None:
        dims |= set(run.snapshot.mood) | set(run.snapshot.initial_mood)
    mood_names = unique_names(dims)
    if run.snapshot is not None:
        author_snapshot(stage, path, run.snapshot, mood_names)
    else:
        log.warning(
            "%s: no state.json snapshot; Identity/Mood/Attention/Workspace omitted", run.name
        )
    summary = author_channels(stage, path, run.journal, mood_names)
    author_layout(stage, path, summary)

    stage.SetStartTimeCode(summary.first_cycle)
    stage.SetEndTimeCode(summary.last_cycle)
    stage.SetTimeCodesPerSecond(options.time_codes_per_second)
    stage.GetRootLayer().customLayerData = layer_metadata(run, summary, options)
    return stage


def layer_metadata(
    run: InstanceRun, summary: ChannelSummary, options: ExportOptions
) -> dict[str, object]:
    data: dict[str, object] = {
        "acsf:exporterVersion": __version__,
        "acsf:cycleIndexing": run.journal.indexing,
        "acsf:layout:synthetic": True,
        "acsf:moodSampling": "final" if summary.mood_first_cycle is None else "journaled",
    }
    if summary.mood_first_cycle is not None:
        data["acsf:moodFirstCycle"] = summary.mood_first_cycle
    if options.source_commit:
        data["acsf:sourceCommit"] = options.source_commit
    return data


def write_stage(stage: Usd.Stage, output: Path, binary: bool = False) -> None:
    """Export the stage's root layer as text (``usda``) or binary crate (``usdc``)."""
    args = {"format": "usdc" if binary else "usda"}
    if not stage.GetRootLayer().Export(str(output), "", args):
        raise OSError(f"could not write {output}")
