"""What one frame of the viewer shows, as plain data (no Strata, no OpenGL).

The encoding follows four rules:

* **R1** only cycle order is a position: one node per cycle with a thought, on the shared spiral
  (:mod:`acsf_usd.schematic`);
* **R2** scalars are labelled gauges (mood bars with their numbers) or text, never coordinates;
* **R3** event kinds are marks: a perception pulse from the ``Stimulus`` sphere, a ring per
  reflection, a lit interlocutor cube per reply;
* **R4** values are held, not invented: they are read at their sampled cycles and held, and an
  absent channel says "not journaled in this run".

The caption "synthetic schematic layout - not a body or a place" is part of every frame.
"""

from __future__ import annotations

import math
import textwrap
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from acsf_usd.schematic import (
    CAPTION_ASCII,
    MOOD_RGB,
    PERCEPTION_RGB,
    REFLECTION_RGB,
    REPLY_RGB,
    SPIRAL_R_MAX,
    THOUGHT_RGB,
    THREAD_RGB,
    Vec3,
    node_radius,
    spiral_arc,
    spiral_positions,
)
from acsf_usd.view import PANELS
from acsf_usd.view.model import Channel, InstanceView, RunView
from acsf_usd.view.strata import (
    PRIMITIVE_CUBE,
    PRIMITIVE_SPHERE,
    RGBA,
    Glow,
    Label,
    Line,
    Prim,
)

BACKGROUND: RGBA = (0.010, 0.013, 0.026, 1.0)
LIGHT_DIRECTION: Vec3 = (-0.35, -1.0, -0.45)
LIGHT_AMBIENT = 0.55

FONT = 16.0  # Strata's 8x8 font, doubled
SMALL = 8.0
LINE_H = 20.0
SMALL_H = 11.0
MARGIN = 16.0
PANEL_LINES = 6

GAUGE_HEIGHT = 1.1  # a mood of 1.0, in stage units
GAUGE_SPACING = 0.9
GAUGE_WIDTH = 0.32


_TYPOGRAPHY = {
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "…": "...",
    " ": " ",
    "•": "*",
    "·": "*",
}

_DIM = (0.55, 0.6, 0.7, 1.0)
_TEXT = (0.92, 0.94, 0.98, 1.0)
_SOFT = (0.72, 0.76, 0.86, 1.0)
_WARM = (1.0, 0.92, 0.62, 1.0)
_CURRENT = (1.0, 0.95, 0.6)


def fold_ascii(text: str) -> str:
    """Fold common typography to ASCII (Strata's font is printable ASCII); the rest is ``?``."""
    for k, v in _TYPOGRAPHY.items():
        text = text.replace(k, v)
    text = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    out = []
    for ch in text:
        if ch in "\t\r\n":
            out.append(" ")
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out)


def wrap(text: str, width_px: float, pixel_size: float) -> list[str]:
    """Wrap folded text at ``floor(width / pixel_size)`` characters."""
    cols = max(8, int(width_px // pixel_size))
    lines: list[str] = []
    for para in text.split("\n"):
        lines.extend(textwrap.wrap(fold_ascii(para), cols) or [""])
    return lines


def clip_lines(lines: list[str], limit: int) -> list[str]:
    if len(lines) <= limit:
        return lines
    kept = lines[:limit]
    kept[-1] = kept[-1][: max(0, len(kept[-1]) - 4)].rstrip() + " ..."
    return kept


# ---------------------------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------------------------


def _sub(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Sequence[float], s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Sequence[float]) -> Vec3:
    n = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


@dataclass(frozen=True)
class OrbitCamera:
    """A camera orbiting ``target``: yaw 0 looks north (-Z) from the south, pitch is elevation."""

    target: Vec3
    distance: float
    yaw: float = 0.0
    pitch: float = 38.0
    fov_y: float = 34.0
    near: float = 0.1
    far: float = 400.0

    @property
    def eye(self) -> Vec3:
        cp, sp = math.cos(math.radians(self.pitch)), math.sin(math.radians(self.pitch))
        cy, sy = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        return _add(
            self.target, (self.distance * cp * sy, self.distance * sp, self.distance * cp * cy)
        )

    def basis(self) -> tuple[Vec3, Vec3, Vec3]:
        """(forward, right, up), unit vectors in world space."""
        fwd = _norm(_sub(self.target, self.eye))
        right = _norm(_cross(fwd, (0.0, 1.0, 0.0)))
        up = _cross(right, fwd)
        return fwd, right, up

    def orbit(self, d_yaw: float, d_pitch: float) -> OrbitCamera:
        return replace(
            self, yaw=(self.yaw + d_yaw) % 360.0, pitch=min(85.0, max(5.0, self.pitch + d_pitch))
        )

    def zoom(self, factor: float) -> OrbitCamera:
        return replace(self, distance=min(200.0, max(2.0, self.distance * factor)))

    def view_matrix(self) -> list[float]:
        """World -> view, column-major (right-handed look-at)."""
        fwd, right, up = self.basis()
        e = self.eye
        return [
            right[0], up[0], -fwd[0], 0.0,
            right[1], up[1], -fwd[1], 0.0,
            right[2], up[2], -fwd[2], 0.0,
            -_dot(right, e), -_dot(up, e), _dot(fwd, e), 1.0,
        ]  # fmt: skip

    def projection_matrix(self, aspect: float) -> list[float]:
        """View -> clip, column-major, OpenGL depth (-1..1)."""
        f = 1.0 / math.tan(math.radians(self.fov_y) / 2)
        n, fa = self.near, self.far
        return [
            f / aspect, 0.0, 0.0, 0.0,
            0.0, f, 0.0, 0.0,
            0.0, 0.0, (fa + n) / (n - fa), -1.0,
            0.0, 0.0, 2 * fa * n / (n - fa), 0.0,
        ]  # fmt: skip

    def project(self, p: Sequence[float], width: int, height: int) -> tuple[float, float] | None:
        """Pixel position (from the top-left) of a world point, or None behind the camera."""
        fwd, right, up = self.basis()
        v = _sub(p, self.eye)
        z = _dot(v, fwd)
        if z <= self.near:
            return None
        t = math.tan(math.radians(self.fov_y) / 2)
        aspect = width / height
        nx = _dot(v, right) / (z * t * aspect)
        ny = _dot(v, up) / (z * t)
        return ((nx + 1) * 0.5 * width, (1 - ny) * 0.5 * height)

    def ray(self, px: float, py: float, width: int, height: int) -> tuple[Vec3, Vec3]:
        """World-space ray (origin, unit direction) through a pixel."""
        fwd, right, up = self.basis()
        t = math.tan(math.radians(self.fov_y) / 2)
        nx = 2 * px / width - 1
        ny = 1 - 2 * py / height
        d = _add(fwd, _add(_scale(right, nx * t * width / height), _scale(up, ny * t)))
        return self.eye, _norm(d)


def frame_camera(run: RunView) -> OrbitCamera:
    """The default framing: every instance, from the south and above, with the bottom band of
    the screen left clear for the text panel."""
    xs = [i.origin[0] for i in run.instances]
    span = max(xs) - min(xs)
    cx = (max(xs) + min(xs)) / 2
    oy = sum(i.origin[1] for i in run.instances) / len(run.instances)
    oz = sum(i.origin[2] for i in run.instances) / len(run.instances)
    return OrbitCamera(target=(cx, oy - 2.0, oz - 1.4), distance=16.5 + 0.95 * span, pitch=44.0)


def ray_sphere(origin: Vec3, direction: Vec3, centre: Vec3, radius: float) -> float | None:
    """Distance along a unit ray to a sphere, or None when it misses."""
    oc = _sub(origin, centre)
    b = _dot(oc, direction)
    c = _dot(oc, oc) - radius * radius
    disc = b * b - c
    if disc < 0:
        return None
    t = -b - math.sqrt(disc)
    if t < 0:
        t = -b + math.sqrt(disc)
    return t if t >= 0 else None


# ---------------------------------------------------------------------------------------------
# Frame content
# ---------------------------------------------------------------------------------------------


@dataclass
class ViewState:
    cycle: int
    instance: int = 0
    panel: str = "thought"
    playing: bool = False
    speed: float = 1.0
    follow: bool = False
    interactive: bool = False
    status: str = ""
    since_change: float = 1e9  # seconds since the cycle changed (window mode animation)


@dataclass
class Frame:
    prims: list[Prim] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    glows: list[Glow] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)


@dataclass(frozen=True)
class Layout:
    """Spiral positions per instance (computed once per loaded stage)."""

    positions: list[dict[float, Vec3]]

    @staticmethod
    def of(run: RunView) -> Layout:
        return Layout([spiral_positions(i.thought_cycles, i.origin) for i in run.instances])


def _rgba(rgb: Sequence[float], a: float = 1.0, k: float = 1.0) -> RGBA:
    return (min(1.0, rgb[0] * k), min(1.0, rgb[1] * k), min(1.0, rgb[2] * k), a)


def _ring(centre: Vec3, radius: float, color: RGBA, segments: int = 24) -> list[Line]:
    x, y, z = centre
    out = []
    for k in range(segments):
        a1, a2 = 2 * math.pi * k / segments, 2 * math.pi * (k + 1) / segments
        out.append(
            Line(
                (x + radius * math.cos(a1), y, z + radius * math.sin(a1)),
                (x + radius * math.cos(a2), y, z + radius * math.sin(a2)),
                color,
            )
        )
    return out


SOURCE_RGB = {
    "wikipedia": PERCEPTION_RGB,
    "speech": (1.0, 0.55, 0.85),
    "mock": (0.7, 0.7, 0.7),
}


def _source_rgb(source: str | None) -> Vec3:
    return SOURCE_RGB.get(source or "", PERCEPTION_RGB)


def _fmt(v: object) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.2f}"
    return fold_ascii(str(v))


def _channel_line(
    label: str, chans: Mapping[str, Channel[Any]], cycle: float, keys: dict[str, str]
) -> str:
    if not any(c.present for c in chans.values()):
        return f"{label}: not journaled in this run"
    sampled = any(c.sampled for c in chans.values())
    parts = []
    for key, name in keys.items():
        ch = chans.get(key)
        if ch is not None and ch.present:
            v = ch.at(cycle)
            if v is not None and v != "":
                parts.append(f"{name} {_fmt(v)}")
    suffix = "" if sampled else " (final value only, not per cycle)"
    return f"{label}: " + (", ".join(parts) or "-") + suffix


def build_frame(
    run: RunView,
    layout: Layout,
    state: ViewState,
    camera: OrbitCamera,
    width: int,
    height: int,
) -> Frame:
    frame = Frame()
    cycle = float(state.cycle)
    pulse_phase = min(1.0, state.since_change / 0.6)
    for idx, inst in enumerate(run.instances):
        _draw_instance(
            frame, inst, layout.positions[idx], cycle, idx == state.instance, pulse_phase
        )
        _draw_gauges(frame, inst, cycle, camera, width, height, idx == state.instance)
        below = camera.project(
            (inst.origin[0], inst.origin[1] - 0.6, inst.origin[2] + SPIRAL_R_MAX + 0.6),
            width,
            height,
        )
        if below is not None and len(run.instances) > 1:
            name = fold_ascii(inst.name)
            col = _TEXT if idx == state.instance else _DIM
            frame.labels.append(Label(below[0] - len(name) * FONT / 2, below[1], FONT, name, col))
    _draw_hud(frame, run, state, width, height)
    _draw_panel(frame, run.instances[state.instance], cycle, state, width, height)
    return frame


def _draw_instance(
    frame: Frame,
    inst: InstanceView,
    pos: dict[float, Vec3],
    cycle: float,
    selected: bool,
    pulse_phase: float,
) -> None:
    shown = [c for c in inst.thought_cycles if c <= cycle]
    longest = inst.longest_thought
    current = shown[-1] if shown and shown[-1] == cycle else None
    for c in shown:
        p = pos[c]
        r = node_radius(len(inst.thoughts[c]), longest)
        age = cycle - c
        if c == current:
            frame.prims.append(Prim(PRIMITIVE_SPHERE, p, (2.8 * r,) * 3, _rgba(_CURRENT)))
            frame.glows.append(Glow(p, 10.0, 14 * r, _rgba(_CURRENT, 0.55)))
            continue
        k = 1.0 if age <= 8 else 0.62
        frame.prims.append(Prim(PRIMITIVE_SPHERE, p, (2 * r,) * 3, _rgba(THOUGHT_RGB, 1.0, k)))
        frame.glows.append(Glow(p, 4.0, 7 * r, _rgba(THOUGHT_RGB, 0.30 if age <= 8 else 0.16)))
    # the thread follows the spiral between consecutive nodes (cycle order, not causation)
    count = len(inst.thought_cycles)
    steps = max(1, min(24, 160 // max(count, 1)))
    thread = _rgba(THREAD_RGB, 0.75, 1.15)
    for i in range(len(shown) - 1):
        arc = (
            spiral_arc(i, count, inst.origin, steps)
            if steps > 1
            else [pos[shown[i]], pos[shown[i + 1]]]
        )
        for a, b in zip(arc, arc[1:], strict=False):
            frame.lines.append(Line(a, b, thread))

    for c, _ in inst.reflections.items():
        if c > cycle:
            continue
        p = pos.get(c, inst.origin)
        if c == cycle:
            frame.lines += _ring(p, 0.34, _rgba(REFLECTION_RGB, 1.0))
            frame.lines += _ring(p, 0.26, _rgba(REFLECTION_RGB, 0.6))
        else:
            frame.lines += _ring(p, 0.2, _rgba(REFLECTION_RGB, 0.55))

    # The stage's own Stimulus sphere: lit on a perception cycle, a faint ghost otherwise.
    lit = cycle in inst.perceptions
    src = inst.perception_source.exact(cycle) if lit else None
    rgb = _source_rgb(src)
    size = (2 * inst.stimulus_radius,) * 3
    if lit:
        frame.prims.append(Prim(PRIMITIVE_SPHERE, inst.stimulus, size, _rgba(rgb)))
        frame.glows.append(Glow(inst.stimulus, 12.0, 4 * inst.stimulus_radius, _rgba(rgb, 0.35)))
        target = pos.get(cycle)
        if target is not None:
            frame.lines.append(Line(inst.stimulus, target, _rgba(rgb, 0.95)))
            # a comet along the pulse: one bright head (moving in window mode) and a tail
            head = 0.35 + 0.65 * pulse_phase
            for k in range(6):
                t = max(0.0, head - 0.07 * k)
                q = _add(inst.stimulus, _scale(_sub(target, inst.stimulus), t))
                frame.glows.append(Glow(q, 9.0 - k, 0.0, _rgba(rgb, 0.9 - 0.13 * k)))
    else:
        frame.prims.append(Prim(PRIMITIVE_SPHERE, inst.stimulus, size, (0.07, 0.09, 0.11, 1.0)))

    for person in inst.interlocutors:
        said = person.utterance.exact(cycle)
        if said:
            frame.prims.append(
                Prim(PRIMITIVE_CUBE, person.position, (0.5,) * 3, _rgba(REPLY_RGB, 1, 1.3))
            )
            frame.glows.append(Glow(person.position, 10.0, 1.4, _rgba(REPLY_RGB, 0.35)))
            tgt = pos.get(cycle)
            if tgt is not None:
                frame.lines.append(Line(tgt, person.position, _rgba(REPLY_RGB, 0.8)))
        else:
            frame.prims.append(
                Prim(PRIMITIVE_CUBE, person.position, (0.5,) * 3, (0.08, 0.11, 0.15, 1.0))
            )


def _gauge_rgb(dim: str) -> Vec3:
    if dim in MOOD_RGB:
        return MOOD_RGB[dim]
    h = sum(ord(ch) for ch in dim) % 360 / 360.0
    return tuple(0.5 + 0.5 * math.cos(2 * math.pi * (h + k / 3)) for k in range(3))  # type: ignore[return-value]


def _draw_gauges(
    frame: Frame,
    inst: InstanceView,
    cycle: float,
    camera: OrbitCamera,
    width: int,
    height: int,
    selected: bool,
) -> None:
    """One labelled bar per mood dimension, standing behind the spiral (R2: a gauge, never a
    position). The bar's frame is a mood of 1.0; the white tick is the instance's baseline."""
    dims = list(inst.mood)
    if not dims:
        return
    ox, oy, oz = inst.origin
    z = oz - SPIRAL_R_MAX - 0.7
    x0 = ox - GAUGE_SPACING * (len(dims) - 1) / 2
    base = oy - 0.25
    compact: list[str] = []
    for i, dim in enumerate(dims):
        x = x0 + i * GAUGE_SPACING
        value = inst.mood[dim].at(cycle)
        rgb = _gauge_rgb(dim)
        w = GAUGE_WIDTH / 2
        frame_col = _rgba(rgb, 0.45)
        corners = [
            (x - w, base),
            (x + w, base),
            (x + w, base + GAUGE_HEIGHT),
            (x - w, base + GAUGE_HEIGHT),
        ]
        for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1], strict=True):
            frame.lines.append(Line((ax, ay, z), (bx, by, z), frame_col))
        if value is not None:
            h = max(0.02, min(1.0, float(value))) * GAUGE_HEIGHT
            frame.prims.append(
                Prim(PRIMITIVE_CUBE, (x, base + h / 2, z), (GAUGE_WIDTH * 0.8, h, 0.12), _rgba(rgb))
            )
        b = inst.mood_baseline.get(dim)
        if b is not None:
            y = base + min(1.0, b) * GAUGE_HEIGHT
            frame.lines.append(
                Line((x - w - 0.08, y, z + 0.08), (x + w + 0.08, y, z + 0.08), (1, 1, 1, 0.9))
            )
        p = camera.project((x, base, z), width, height)
        q = camera.project((x + GAUGE_SPACING, base, z), width, height)
        if p is not None:
            roomy = q is None or abs(q[0] - p[0]) >= 6 * SMALL
            val = "-" if value is None else f"{float(value):.2f}"
            col = _TEXT if selected else _DIM
            short = fold_ascii(dim)[:5] if roomy else fold_ascii(dim)[:1]
            frame.labels.append(Label(p[0] - len(short) * SMALL / 2, p[1] + 4, SMALL, short, col))
            if roomy:
                frame.labels.append(
                    Label(p[0] - len(val) * SMALL / 2, p[1] + 4 + SMALL_H, SMALL, val, col)
                )
            else:
                compact.append(f"{fold_ascii(dim)[:3]} {val}")
    p = camera.project((ox, base, z), width, height)
    if p is not None and compact:
        edge = camera.project((ox + 0.8 * SPIRAL_R_MAX, base, z), width, height)
        room = 2 * abs(edge[0] - p[0]) if edge is not None else 30 * SMALL
        col = _TEXT if selected else _DIM
        rows = textwrap.wrap("  ".join(compact), max(10, int(room // SMALL)))
        for k, text in enumerate(rows):
            y = p[1] + 4 + SMALL_H * (k + 1)
            frame.labels.append(Label(p[0] - len(text) * SMALL / 2, y, SMALL, text, col))
    p = camera.project((ox, base + GAUGE_HEIGHT, z), width, height)
    if p is not None:
        note = "mood (final value only)" if inst.mood_sampling == "final" else "mood (per cycle)"
        frame.labels.append(Label(p[0] - len(note) * SMALL / 2, p[1] - 14, SMALL, note, _SOFT))


def _draw_hud(frame: Frame, run: RunView, state: ViewState, width: int, height: int) -> None:
    inst = run.instances[state.instance]
    cycle = float(state.cycle)
    y = MARGIN
    indexing = f"{inst.cycle_indexing} indexing" if inst.cycle_indexing else "indexing unknown"
    which = f"  [{state.instance + 1}/{len(run.instances)}]" if len(run.instances) > 1 else ""
    title = f"{fold_ascii(inst.name)}{which}  cycle {state.cycle} / {int(run.end)}  ({indexing})"
    frame.labels.append(Label(MARGIN, y, FONT, title, _TEXT))
    y += LINE_H + 2
    frame.labels.append(Label(MARGIN, y, SMALL * 1.0, CAPTION_ASCII, _SOFT))
    y += SMALL_H + 6

    events = []
    if cycle in inst.thoughts:
        events.append("thought")
    if cycle in inst.perceptions:
        src = inst.perception_source.exact(cycle)
        events.append(f"perception ({src})" if src else "perception")
    if cycle in inst.reflections:
        events.append("reflection")
    for person in inst.interlocutors:
        if person.utterance.exact(cycle):
            events.append(f"reply to {fold_ascii(person.sender)}")
    lines = [
        "this cycle: " + (", ".join(events) if events else "nothing journaled"),
        _channel_line(
            "attention",
            inst.attention,
            cycle,
            {"focus": "focus", "theme": "theme", "salience": "salience"},
        ),
        _channel_line(
            "cycle",
            inst.cycle,
            cycle,
            {
                "hot2Label": "HOT-2",
                "predictionError": "prediction error",
                "reflectionP": "reflection p",
                "reflectionFired": "fired",
            },
        ),
        _channel_line(
            "memory",
            inst.memory,
            cycle,
            {"longTermCount": "long-term", "consolidationStored": "consolidated"},
        ),
    ]
    # legend, then the journaled channels as a column down the left edge (clear of the spiral)
    x = MARGIN
    for name, rgb in (
        ("thought", THOUGHT_RGB),
        ("perception", PERCEPTION_RGB),
        ("reflection", REFLECTION_RGB),
        ("reply", REPLY_RGB),
    ):
        frame.labels.append(Label(x, y, SMALL, "o " + name, _rgba(rgb)))
        x += (len(name) + 4) * SMALL
    y += SMALL_H + 10
    if state.status:
        frame.labels.append(Label(MARGIN, y, SMALL, fold_ascii(state.status), _WARM))
        y += SMALL_H + 6
    cols = max(30, int(0.3 * width / SMALL))
    for text in lines:
        for k, part in enumerate(textwrap.wrap(text, cols, subsequent_indent="  ")):
            frame.labels.append(Label(MARGIN, y, SMALL, part, _TEXT if k == 0 else _SOFT))
            y += SMALL_H
        y += 4
    if state.interactive:
        hints = [
            ("space", "play" if not state.playing else "pause"),
            ("<- ->", "step"),
            ("[ ]", f"speed x{state.speed:g}"),
            ("drag", "orbit"),
            ("wheel", "zoom"),
            ("click", "select"),
            ("tab", "instance"),
            ("t", "text"),
            ("f", "follow " + ("on" if state.follow else "off")),
        ]
        for i, (key, what) in enumerate(hints):
            text = f"{key:>5} {what}"
            frame.labels.append(
                Label(width - MARGIN - 18 * SMALL, MARGIN + i * SMALL_H, SMALL, text, _DIM)
            )


def panel_text(inst: InstanceView, cycle: float, panel: str) -> tuple[str, str]:
    """(heading, body) for the text panel."""
    c = int(cycle)
    if panel == "reflection":
        body = inst.reflections.get(cycle, "")
        return f"reflection at cycle {c}", body or "(no reflection journaled at this cycle)"
    if panel == "perception":
        body = inst.perceptions.get(cycle, "")
        return f"perception at cycle {c}", body or "(no perception journaled at this cycle)"
    if panel == "reply":
        said = [
            f"to {p.sender}: {p.utterance.exact(cycle)}"
            for p in inst.interlocutors
            if p.utterance.exact(cycle)
        ]
        return f"replies at cycle {c}", "  ".join(said) or "(no reply journaled at this cycle)"
    return f"thought at cycle {c}", inst.thoughts.get(
        cycle, ""
    ) or "(no thought journaled at this cycle)"


def _draw_panel(
    frame: Frame, inst: InstanceView, cycle: float, state: ViewState, width: int, height: int
) -> None:
    heading, body = panel_text(inst, cycle, state.panel)
    lines = clip_lines(wrap(body, width - 2 * MARGIN, FONT), PANEL_LINES)
    y = height - MARGIN - LINE_H * len(lines)
    others = [p for p in PANELS if p != state.panel and panel_text(inst, cycle, p)[1][:1] != "("]
    hint = (
        f"   (also: {', '.join(others)}{'; t to switch' if state.interactive else ''})"
        if others
        else ""
    )
    frame.labels.append(Label(MARGIN, y - SMALL_H - 6, SMALL, heading + hint, _DIM))
    col = _WARM if state.panel == "thought" else _TEXT
    for i, line in enumerate(lines):
        frame.labels.append(Label(MARGIN, y + i * LINE_H, FONT, line, col))


def pick(
    run: RunView,
    layout: Layout,
    camera: OrbitCamera,
    px: float,
    py: float,
    width: int,
    height: int,
    cycle: float,
) -> tuple[int, float] | None:
    """The (instance, cycle) of the drawn node under a pixel, by a ray-sphere test.

    Strata's ``strata_raycast_meshes`` hits static meshes only, and nodes are primitives, so the
    viewer tests the spheres itself (each is a known centre and radius, inflated a little so a
    small node is still easy to click)."""
    origin, direction = camera.ray(px, py, width, height)
    best: tuple[float, int, float] | None = None
    for idx, inst in enumerate(run.instances):
        longest = inst.longest_thought
        for c, p in layout.positions[idx].items():
            if c > cycle:
                continue
            r = max(0.12, 1.4 * node_radius(len(inst.thoughts[c]), longest))
            t = ray_sphere(origin, direction, p, r)
            if t is not None and (best is None or t < best[0]):
                best = (t, idx, c)
    return None if best is None else (best[1], best[2])
