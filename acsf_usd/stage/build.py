"""Build one instance's stage from the run model and write it to disk."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pxr import Sdf, Usd, UsdGeom

from acsf_usd import __version__
from acsf_usd.model import InstanceRun
from acsf_usd.stage.common import INSTANCES, WORLD, instance_path, set_attr
from acsf_usd.stage.snapshot import author_snapshot

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExportOptions:
    time_codes_per_second: float = 1.0


def build_instance_stage(run: InstanceRun, options: ExportOptions | None = None) -> Usd.Stage:
    """Author one instance into a new in-memory stage."""
    stage = Usd.Stage.CreateInMemory()
    world = UsdGeom.Xform.Define(stage, WORLD)
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, INSTANCES)

    path = instance_path(run.name)
    instance = UsdGeom.Xform.Define(stage, path).GetPrim()
    set_attr(instance, "acsf:name", Sdf.ValueTypeNames.String, run.name)
    if run.snapshot is not None:
        author_snapshot(stage, path, run.snapshot)
    else:
        log.warning(
            "%s: no state.json snapshot; Identity/Mood/Attention/Workspace omitted", run.name
        )

    stage.GetRootLayer().customLayerData = layer_metadata(run)
    return stage


def layer_metadata(run: InstanceRun) -> dict[str, object]:
    return {
        "acsf:exporterVersion": __version__,
        "acsf:cycleIndexing": run.journal.indexing,
        "acsf:layout:synthetic": True,
    }


def write_stage(stage: Usd.Stage, output: Path) -> None:
    """Export the stage's root layer; the format follows the output extension."""
    if not stage.GetRootLayer().Export(str(output)):
        raise OSError(f"could not write {output}")
