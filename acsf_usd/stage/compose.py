"""Multi-instance composition: one layer per instance, a root layer that sublayers them.

Each instance layer is exactly what a single-instance export of that instance would write
(apart from a disambiguated prim name), so it opens on its own. The root layer adds what only
makes sense for the group: the shared timeline spanning every instance, and an X offset per
instance (authored as an ``over`` in the root, the strongest layer) so instances sit side by
side. The offset is part of the synthetic layout and means nothing to ACSF.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pxr import Gf, Sdf, Usd, UsdGeom

from acsf_usd import __version__
from acsf_usd.model import InstanceRun
from acsf_usd.stage.build import ExportOptions, build_instance_stage, write_stage
from acsf_usd.stage.common import WORLD, instance_path, prim_name

INSTANCE_SPACING = 10.0


@dataclass(frozen=True)
class ComposedExport:
    root: Path
    layers: tuple[Path, ...]
    prims: tuple[str, ...]
    runs: tuple[InstanceRun, ...]


def disambiguate(runs: Sequence[InstanceRun]) -> list[str]:
    """Distinct prim names for the runs, in the runs' order.

    Runs are ranked by (sanitised name, raw name, resolved source path); the first of each
    sanitised name keeps it and later ones get ``_2``, ``_3`` … — the same result whatever
    order the inputs were given in.
    """
    order = sorted(
        range(len(runs)),
        key=lambda i: (prim_name(runs[i].name), runs[i].name, str(runs[i].source_dir.resolve())),
    )
    used: set[str] = set()
    names = [""] * len(runs)
    for index in order:
        base = prim_name(runs[index].name)
        candidate, n = base, 1
        while candidate in used:
            n += 1
            candidate = f"{base}_{n}"
        used.add(candidate)
        names[index] = candidate
    return names


def export_composed(
    runs: Sequence[InstanceRun],
    root: Path,
    options: ExportOptions | None = None,
    binary: bool = False,
) -> ComposedExport:
    """Write one layer per instance next to ``root`` and a root layer sublayering them."""
    options = options or ExportOptions()
    extension = ".usdc" if binary else ".usda"
    prims = disambiguate(runs)
    ordered = sorted(zip(prims, runs, strict=True), key=lambda pair: pair[0])
    layers: list[Path] = []
    first: list[float] = []
    last: list[float] = []
    for prim, run in ordered:
        stage = build_instance_stage(run, options, prim=prim)
        path = root.parent / f"{prim}{extension}"
        if path.resolve() == root.resolve():
            raise ValueError(f"{root}: the root layer would overwrite instance layer {path.name}")
        write_stage(stage, path, binary=binary)
        layers.append(path)
        first.append(stage.GetStartTimeCode())
        last.append(stage.GetEndTimeCode())

    stage = Usd.Stage.CreateInMemory()
    stage.SetDefaultPrim(stage.OverridePrim(WORLD))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetStartTimeCode(min(first) if first else 0)
    stage.SetEndTimeCode(max(last) if last else 0)
    stage.SetTimeCodesPerSecond(options.time_codes_per_second)
    for index, (prim, _run) in enumerate(ordered):
        over = stage.OverridePrim(instance_path(prim))
        xform = UsdGeom.Xformable(over)
        xform.AddTranslateOp().Set(Gf.Vec3d(index * INSTANCE_SPACING, 0.0, 0.0))
    data: dict[str, object] = {
        "acsf:exporterVersion": __version__,
        "acsf:layout:synthetic": True,
        "acsf:instanceCount": len(ordered),
    }
    if options.source_commit:
        data["acsf:sourceCommit"] = options.source_commit
    stage.GetRootLayer().customLayerData = data
    # The sublayer paths are relative to the root file, so they are set on a stage-less copy
    # of the layer: an in-memory stage would try (and fail) to resolve them.
    layer = Sdf.Layer.CreateAnonymous()
    layer.TransferContent(stage.GetRootLayer())
    layer.subLayerPaths = [f"./{path.name}" for path in layers]
    if not layer.Export(str(root), "", {"format": "usdc" if binary else "usda"}):
        raise OSError(f"could not write {root}")
    return ComposedExport(
        root=root,
        layers=tuple(layers),
        prims=tuple(p for p, _ in ordered),
        runs=tuple(r for _, r in ordered),
    )
