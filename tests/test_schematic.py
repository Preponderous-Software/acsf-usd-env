"""The shared spiral: the film and the viewer place every node identically."""

from __future__ import annotations

import math

import pytest

from acsf_usd.schematic import node_radius, spiral_arc, spiral_point, spiral_positions


def _film_reference(i: int, n: int, origin: tuple[float, float, float]) -> tuple[float, ...]:
    """The inline formula scripts/render_stage.py used before the spiral moved to a module."""
    r0, turns, r_max = 0.7, 4.0, 4.0
    b = (r_max - r0) / (2 * math.pi * turns)
    theta_max = 2 * math.pi * turns
    s = i / max(n - 1, 1)
    l_target = s * (r0 * theta_max + b * theta_max**2 / 2)
    theta = (-r0 + math.sqrt(r0 * r0 + 2 * b * l_target)) / b
    r = r0 + b * theta
    ox, oy, oz = origin
    return (ox + r * math.cos(theta), oy - 0.12 * r, oz + r * math.sin(theta))


@pytest.mark.parametrize("n", [1, 2, 3, 83, 200])
def test_spiral_matches_the_film_bit_for_bit(n: int) -> None:
    origin = (10.0, 0.0, -2.5)
    for i in range(n):
        assert spiral_point(i, n, origin) == _film_reference(i, n, origin)


def test_spiral_is_cycle_order_only() -> None:
    cycles = [1.0, 2.0, 5.0, 9.0]
    pos = spiral_positions(cycles)
    # positions depend on the order index only, not on how far apart the cycle numbers are
    assert list(pos.values()) == list(spiral_positions([3.0, 4.0, 10.0, 11.0]).values())
    radii = [math.hypot(p[0], p[2]) for p in pos.values()]
    assert radii == sorted(radii)
    assert radii[0] == pytest.approx(0.7) and radii[-1] == pytest.approx(4.0)


def test_equal_arc_spacing() -> None:
    pts = [spiral_point(i, 200) for i in range(200)]
    steps = [math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False)]
    # chords along a spiral are close to the (equal) arc steps
    assert max(steps) / min(steps) < 1.25


def test_node_radius_and_bad_index() -> None:
    assert node_radius(0, 100) == pytest.approx(0.045)
    assert node_radius(100, 100) == pytest.approx(0.105)
    assert node_radius(5, 0) > 0
    with pytest.raises(ValueError):
        spiral_point(3, 3)
    with pytest.raises(ValueError):
        spiral_point(0, 0)


def test_arc_runs_from_node_to_node_along_the_curve() -> None:
    arc = spiral_arc(2, 9, (1.0, 0.0, 0.0), steps=12)
    assert arc[0] == spiral_point(2, 9, (1.0, 0.0, 0.0))
    assert arc[-1] == spiral_point(3, 9, (1.0, 0.0, 0.0))
    assert len(arc) == 13
    radii = [math.hypot(p[0] - 1.0, p[2]) for p in arc]
    assert radii == sorted(radii)
    with pytest.raises(ValueError):
        spiral_arc(8, 9)
