"""The synthetic schematic encoding shared by every 3D picture of a stage.

ACSF has no spatial state. The only quantity drawn as a position is cycle order: one node per
cycle with a thought, laid along an equal-arc Archimedean spiral around the instance origin, in
the stage's XZ plane (Y up). Distance along the spiral means "later" and nothing else. Node size
follows the thought's length in characters.

Both ``scripts/render_stage.py`` (the Blender film) and ``acsf-usd view`` (Strata) import this
module, so a frame of the film and a frame of the viewer at the same cycle place every node in
the same spot. Nothing here is authored into the stage: the stage's own geometry (the
``Stimulus`` sphere and the interlocutor cubes) is unchanged.

This module has no dependencies outside the standard library, so it also runs inside Blender's
bundled Python.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Vec3 = tuple[float, float, float]

CAPTION = "synthetic schematic layout — not a body or a place"
CAPTION_ASCII = "synthetic schematic layout - not a body or a place"

# The spiral (drawing choices, in stage units).
SPIRAL_R0 = 0.7
SPIRAL_TURNS = 4.0
SPIRAL_R_MAX = 4.0
SPIRAL_DROP = 0.12  # nodes sink by this fraction of their radius, so the spiral reads as a bowl

# Node radius: NODE_MIN + NODE_GAIN * (thought length / longest thought in the run).
NODE_MIN = 0.045
NODE_GAIN = 0.06

# Colours (linear RGB): drawing choices only.
THOUGHT_RGB: Vec3 = (0.35, 0.75, 1.0)
PERCEPTION_RGB: Vec3 = (0.55, 1.0, 0.75)
REFLECTION_RGB: Vec3 = (1.0, 0.55, 0.12)
THREAD_RGB: Vec3 = (0.25, 0.45, 0.8)
REPLY_RGB: Vec3 = (0.3, 0.6, 0.9)
MOOD_RGB: dict[str, Vec3] = {
    "contentment": (1.0, 0.75, 0.3),
    "curiosity": (0.2, 0.85, 1.0),
    "wonder": (0.7, 0.4, 1.0),
    "melancholy": (0.2, 0.3, 0.9),
}


def spiral_at(s: float, origin: Sequence[float] = (0.0, 0.0, 0.0)) -> Vec3:
    """The point a fraction ``s`` (0..1) of the way along the spiral's arc length.

    Equal arc spacing: the arc length of r = r0 + b*theta is about r0*theta + b*theta^2/2,
    which is solved for theta at the fraction ``s`` of the full length.
    """
    theta_max = 2 * math.pi * SPIRAL_TURNS
    b = (SPIRAL_R_MAX - SPIRAL_R0) / theta_max
    target = s * (SPIRAL_R0 * theta_max + b * theta_max**2 / 2)
    theta = (-SPIRAL_R0 + math.sqrt(SPIRAL_R0 * SPIRAL_R0 + 2 * b * target)) / b
    r = SPIRAL_R0 + b * theta
    ox, oy, oz = (float(v) for v in origin)
    return (ox + r * math.cos(theta), oy - SPIRAL_DROP * r, oz + r * math.sin(theta))


def spiral_point(index: int, count: int, origin: Sequence[float] = (0.0, 0.0, 0.0)) -> Vec3:
    """Position (USD frame, Y up) of node ``index`` of ``count`` around ``origin``."""
    if count < 1 or not 0 <= index < count:
        raise ValueError(f"node {index} of {count}")
    return spiral_at(index / max(count - 1, 1), origin)


def spiral_arc(
    index: int, count: int, origin: Sequence[float] = (0.0, 0.0, 0.0), steps: int = 8
) -> list[Vec3]:
    """Points along the spiral from node ``index`` to node ``index + 1`` (both included), so a
    thread between two nodes can follow the curve instead of cutting across it."""
    if not 0 <= index < count - 1:
        raise ValueError(f"no arc after node {index} of {count}")
    span = 1 / max(count - 1, 1)
    out = [spiral_at((index + k / steps) * span, origin) for k in range(steps)]
    out.append(spiral_point(index + 1, count, origin))
    out[0] = spiral_point(index, count, origin)
    return out


def spiral_positions(
    cycles: Sequence[float], origin: Sequence[float] = (0.0, 0.0, 0.0)
) -> dict[float, Vec3]:
    """One spiral position per cycle, in the order given (callers pass cycle order)."""
    return {c: spiral_point(i, len(cycles), origin) for i, c in enumerate(cycles)}


def node_radius(length: int, longest: int) -> float:
    """Radius of a thought node: its thought's length relative to the run's longest."""
    return NODE_MIN + NODE_GAIN * length / max(longest, 1)
