"""Author the schematic, explicitly synthetic geometry of one instance.

ACSF has no spatial world (CONCEPT.md). This geometry exists only so the stage shows
something in an ordinary USD viewer: a sphere above the instance's origin that is visible on
cycles with a perception, and one cube per interlocutor, placed on a ring around the origin
and visible on cycles with a reply to that person. Positions encode nothing ACSF knows; the
stage records ``acsf:layout:synthetic = true`` and every geometry prim says so in its
documentation metadata.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable

from pxr import Gf, Sdf, Usd, UsdGeom, Vt

from acsf_usd.stage.channels import ChannelSummary

STIMULUS_HEIGHT = 1.5
STIMULUS_RADIUS = 0.4
RING_RADIUS = 3.0
CUBE_SIZE = 0.5
POSITION_DECIMALS = 6

SYNTHETIC_NOTE = (
    "Synthetic schematic geometry (acsf:layout:synthetic): a visual aid only. ACSF has no "
    "spatial world; this position and shape encode nothing the instance knows."
)


def ring_angle(sender: str) -> float:
    """Angle in radians from a stable hash of the sender name (not Python's salted hash)."""
    digest = hashlib.sha256(sender.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 * 2 * math.pi


def ring_position(sender: str) -> Gf.Vec3d:
    """A point on the ring around the instance origin (XZ plane, Y up), rounded so the
    authored value is identical across machines."""
    angle = ring_angle(sender)
    x = round(RING_RADIUS * math.cos(angle), POSITION_DECIMALS) + 0.0
    z = round(RING_RADIUS * math.sin(angle), POSITION_DECIMALS) + 0.0
    return Gf.Vec3d(x, 0.0, z)


def author_layout(stage: Usd.Stage, instance: Sdf.Path, summary: ChannelSummary) -> None:
    stimulus = UsdGeom.Xform.Define(stage, instance.AppendChild("Stimulus"))
    stimulus.AddTranslateOp().Set(Gf.Vec3d(0.0, STIMULUS_HEIGHT, 0.0))
    stimulus.GetPrim().SetDocumentation(SYNTHETIC_NOTE)
    sphere = UsdGeom.Sphere.Define(stage, stimulus.GetPath().AppendChild("Sphere"))
    sphere.CreateRadiusAttr(STIMULUS_RADIUS)
    sphere.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(0.95, 0.75, 0.2)]))
    _visibility(stimulus, summary.timeline, summary.perception_cycles)

    for sender, name in sorted(summary.interlocutors.items(), key=lambda item: item[1]):
        path = instance.AppendChild("Interlocutors").AppendChild(name)
        xform = UsdGeom.Xform.Define(stage, path)
        xform.AddTranslateOp().Set(ring_position(sender))
        xform.GetPrim().SetDocumentation(SYNTHETIC_NOTE)
        cube = UsdGeom.Cube.Define(stage, path.AppendChild("Cube"))
        cube.CreateSizeAttr(CUBE_SIZE)
        cube.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(0.3, 0.6, 0.9)]))
        _visibility(xform, summary.timeline, summary.utterance_cycles[sender])


def _visibility(
    imageable: UsdGeom.Imageable, timeline: Iterable[int], visible_cycles: Iterable[int]
) -> None:
    """``inherited`` on the given cycles and ``invisible`` on every other timeline cycle."""
    visible = set(visible_cycles)
    attr = imageable.CreateVisibilityAttr()
    cycles = list(timeline)
    if not cycles:
        attr.Set(UsdGeom.Tokens.invisible)
        return
    for cycle in cycles:
        token = UsdGeom.Tokens.inherited if cycle in visible else UsdGeom.Tokens.invisible
        attr.Set(token, Usd.TimeCode(cycle))
