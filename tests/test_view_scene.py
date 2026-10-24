"""Frame content for ``acsf-usd view``, with no Strata involved."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from acsf_usd.schematic import CAPTION_ASCII, spiral_point
from acsf_usd.view.model import load_run
from acsf_usd.view.scene import (
    Layout,
    OrbitCamera,
    ViewState,
    build_frame,
    clip_lines,
    fold_ascii,
    frame_camera,
    panel_text,
    pick,
    ray_sphere,
    wrap,
)
from acsf_usd.view.strata import PRIMITIVE_CUBE, PRIMITIVE_SPHERE

W, H = 1280, 720


def test_fold_ascii() -> None:
    assert fold_ascii("a — b – c") == "a - b - c"
    assert fold_ascii("“quoted” it’s…") == '"quoted" it\'s...'
    assert fold_ascii("café naïve") == "cafe naive"
    assert fold_ascii("漢字 ok") == "?? ok"
    assert fold_ascii("tab\there\nnew") == "tab here new"


def test_wrap_at_panel_width_over_pixel_size() -> None:
    lines = wrap("word " * 100, 1248, 16)
    assert all(len(line) <= 78 for line in lines)
    assert len(lines) > 5
    clipped = clip_lines(lines, 3)
    assert len(clipped) == 3 and clipped[-1].endswith("...")
    assert clip_lines(["a"], 3) == ["a"]


def test_camera_project_and_ray_agree() -> None:
    cam = OrbitCamera(target=(0.0, -2.0, -1.4), distance=16.5, yaw=20.0, pitch=44.0)
    for p in [(0.0, 0.0, 0.0), (3.0, -0.4, 2.0), (-4.0, 1.5, -3.0)]:
        px = cam.project(p, W, H)
        assert px is not None
        origin, d = cam.ray(px[0], px[1], W, H)
        # the ray through the projected pixel passes through the point
        v = (p[0] - origin[0], p[1] - origin[1], p[2] - origin[2])
        t = sum(a * b for a, b in zip(v, d, strict=True))
        closest = tuple(o + t * k for o, k in zip(origin, d, strict=True))
        assert math.dist(closest, p) < 1e-6
    assert cam.project(cam.eye, W, H) is None


def test_view_matrix_maps_eye_to_origin_and_target_ahead() -> None:
    cam = OrbitCamera(target=(1.0, 2.0, 3.0), distance=10.0, yaw=33.0, pitch=21.0)
    m = cam.view_matrix()

    def apply(p: tuple[float, float, float]) -> tuple[float, float, float]:
        x, y, z = p
        return (
            m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14],
        )

    assert apply(cam.eye) == pytest.approx((0, 0, 0), abs=1e-9)
    assert apply(cam.target) == pytest.approx((0, 0, -10.0))
    assert cam.orbit(0, 500).pitch == 85.0 and cam.zoom(1e-6).distance == 2.0


def test_ray_sphere() -> None:
    assert ray_sphere((0, 0, 5), (0, 0, -1), (0, 0, 0), 1.0) == pytest.approx(4.0)
    assert ray_sphere((0, 0, 5), (0, 0, 1), (0, 0, 0), 1.0) is None
    assert ray_sphere((0, 3, 5), (0, 0, -1), (0, 0, 0), 1.0) is None
    assert ray_sphere((0, 0, 0), (0, 0, -1), (0, 0, 0), 1.0) == pytest.approx(1.0)


@pytest.fixture(scope="module")
def echo(view_stages: dict[str, Path]):  # type: ignore[no-untyped-def]
    return load_run(view_stages["echo"])


def _labels(frame) -> str:  # type: ignore[no-untyped-def]
    return "\n".join(lb.text for lb in frame.labels)


def test_only_thoughts_up_to_the_cycle_are_nodes(echo) -> None:  # type: ignore[no-untyped-def]
    layout = Layout.of(echo)
    cam = frame_camera(echo)
    frame = build_frame(echo, layout, ViewState(cycle=116), cam, W, H)
    inst = echo.instances[0]
    nodes = [p for p in frame.prims if p.kind == PRIMITIVE_SPHERE and p.position != inst.stimulus]
    assert len(nodes) == sum(1 for c in inst.thoughts if c <= 116)
    # R1: node positions are the shared spiral, in cycle order
    assert nodes[0].position == spiral_point(0, 200, inst.origin)
    # R3: one ring (24 segments) per reflection so far, two for the current cycle's
    rings = sum(1 for c in inst.reflections if c <= 116) + (1 if 116.0 in inst.reflections else 0)
    ring_lines = [ln for ln in frame.lines if ln.color[:3] == (1.0, 0.55, 0.12)]
    assert len(ring_lines) == 24 * rings
    text = _labels(frame)
    assert CAPTION_ASCII in text
    assert "cycle: not journaled in this run" in text  # R4: absent channel says so
    assert "mood (final value only)" in text
    assert "cycle 116 / 200" in text


def test_stimulus_lights_only_on_perception_cycles(echo) -> None:  # type: ignore[no-untyped-def]
    inst = echo.instances[0]
    layout = Layout.of(echo)
    cam = frame_camera(echo)
    on = min(inst.perceptions)
    off = next(c for c in inst.thoughts if c not in inst.perceptions)
    for cycle, lit in ((on, True), (off, False)):
        frame = build_frame(echo, layout, ViewState(cycle=int(cycle)), cam, W, H)
        (stim,) = [p for p in frame.prims if p.position == inst.stimulus]
        assert (stim.color[1] > 0.5) is lit
        assert any(ln.a == inst.stimulus for ln in frame.lines) is lit


def test_panel_and_pick(echo) -> None:  # type: ignore[no-untyped-def]
    inst = echo.instances[0]
    heading, body = panel_text(inst, 116.0, "thought")
    assert heading == "thought at cycle 116" and body == inst.thoughts[116.0]
    assert panel_text(inst, 2.0, "reflection")[1].startswith("(no reflection")
    layout = Layout.of(echo)
    cam = frame_camera(echo)
    target = layout.positions[0][50.0]
    px = cam.project(target, W, H)
    assert px is not None
    assert pick(echo, layout, cam, px[0], px[1], W, H, 116.0) == (0, 50.0)
    # nodes after the shown cycle cannot be picked
    assert pick(echo, layout, cam, px[0], px[1], W, H, 10.0) != (0, 50.0)
    assert pick(echo, layout, cam, 2.0, 2.0, W, H, 200.0) is None


def test_replies_and_per_cycle_channels(view_stages: dict[str, Path]) -> None:
    run = load_run(view_stages["pair"])
    layout = Layout.of(run)
    cam = frame_camera(run)
    frame = build_frame(run, layout, ViewState(cycle=2, instance=1, panel="reply"), cam, W, H)
    nova = run.instances[1]
    lit = [p for p in frame.prims if p.kind == PRIMITIVE_CUBE and p.scale == (0.5, 0.5, 0.5)]
    assert any(p.color[2] > 0.9 for p in lit)
    text = _labels(frame)
    assert "reply to" in text and "HOT-2 metacognitive-surprised" in text
    assert "to " + nova.interlocutors[0].sender in text or "to Dana" in text
    assert "mood (per cycle)" in text  # Lumen journaled mood per cycle
    assert "Lumen" in text and "Nova" in text


def test_text_panel_has_one_backing_behind_its_text(echo) -> None:  # type: ignore[no-untyped-def]
    frame = build_frame(echo, Layout.of(echo), ViewState(cycle=116), frame_camera(echo), W, H)
    (panel,) = frame.panels
    assert 0 <= panel.x and panel.x + panel.width <= W
    assert 0 <= panel.y and panel.y + panel.height <= H
    assert panel.color[3] < 1.0  # alpha blended over the scene
    heading = next(lb for lb in frame.labels if lb.text.startswith("thought at cycle 116"))
    below = [lb for lb in frame.labels if lb.y >= heading.y]
    assert below and all(
        panel.x <= lb.x and panel.y <= lb.y < panel.y + panel.height for lb in below
    )
