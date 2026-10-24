"""Viewer controls and ``--watch``, driven with synthetic events (no window, no Strata)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from acsf_usd.view import app
from acsf_usd.view.app import Controller, StageWatcher, clamp_cycle, instance_index
from acsf_usd.view.model import RunView, StageError, load_run
from acsf_usd.view.scene import Layout, ViewState, frame_camera
from acsf_usd.view.strata import (
    EVENT_KEY_DOWN,
    EVENT_MOUSE_BUTTON_DOWN,
    EVENT_MOUSE_BUTTON_UP,
    EVENT_MOUSE_MOTION,
    EVENT_MOUSE_WHEEL,
    EVENT_QUIT,
    EVENT_RESIZED,
    InputEvent,
)

W, H = 1280, 720


def key(code: int) -> InputEvent:
    return InputEvent(EVENT_KEY_DOWN, scancode=code)


@pytest.fixture
def ctl(view_stages: dict[str, Path]) -> Controller:
    run = load_run(view_stages["golden"])
    return Controller(
        run, Layout.of(run), ViewState(cycle=100, interactive=True), frame_camera(run), W, H
    )


def test_step_home_end_and_speed(ctl: Controller) -> None:
    ctl.handle(key(app.KEY_RIGHT))
    assert ctl.state.cycle == 101 and ctl.state.since_change == 0.0
    ctl.handle(key(app.KEY_LEFT))
    ctl.handle(key(app.KEY_LEFT))
    assert ctl.state.cycle == 99
    ctl.handle(key(app.KEY_HOME))
    assert ctl.state.cycle == 1
    ctl.handle(key(app.KEY_LEFT))
    assert ctl.state.cycle == 1  # clamped
    ctl.handle(key(app.KEY_END))
    assert ctl.state.cycle == 200
    ctl.handle(key(app.KEY_RIGHTBRACKET))
    assert ctl.state.speed == 2.0
    for _ in range(10):
        ctl.handle(key(app.KEY_LEFTBRACKET))
    assert ctl.state.speed == 0.25


def test_play_advances_one_cycle_per_second_and_stops_at_the_end(ctl: Controller) -> None:
    ctl.handle(key(app.KEY_SPACE))
    assert ctl.state.playing
    ctl.tick(0.5)
    assert ctl.state.cycle == 100
    ctl.tick(0.6)
    assert ctl.state.cycle == 101
    ctl.state.speed = 16.0
    ctl.tick(100.0)
    assert ctl.state.cycle == 200 and not ctl.state.playing
    ctl.handle(key(app.KEY_SPACE))  # play again from the start
    assert ctl.state.playing and ctl.state.cycle == 1


def test_tab_text_follow_reset_quit(ctl: Controller) -> None:
    ctl.handle(key(app.KEY_TAB))
    assert ctl.state.instance == 1
    for _ in range(3):
        ctl.handle(key(app.KEY_TAB))
    assert ctl.state.instance == 0
    ctl.handle(key(app.KEY_T))
    assert ctl.state.panel == "reflection"
    ctl.handle(key(app.KEY_F))
    assert ctl.state.follow and ctl.state.cycle == 200
    ctl.camera = ctl.camera.orbit(40, 10)
    ctl.handle(key(app.KEY_R))
    assert ctl.camera == frame_camera(ctl.run)
    ctl.handle(InputEvent(EVENT_RESIZED, width=640, height=360))
    assert (ctl.width, ctl.height) == (640, 360)
    ctl.handle(InputEvent(EVENT_QUIT))
    assert ctl.quit


def test_click_selects_a_node_and_drag_orbits(ctl: Controller) -> None:
    target = ctl.layout.positions[2][40.0]  # Sage, cycle 40
    px = ctl.camera.project(target, W, H)
    assert px is not None
    ctl.handle(InputEvent(EVENT_MOUSE_BUTTON_DOWN, button=1, x=px[0], y=px[1]))
    ctl.handle(InputEvent(EVENT_MOUSE_BUTTON_UP, button=1, x=px[0], y=px[1]))
    assert (ctl.state.instance, ctl.state.cycle) == (2, 40)
    yaw = ctl.camera.yaw
    ctl.handle(InputEvent(EVENT_MOUSE_BUTTON_DOWN, button=1, x=10, y=10))
    ctl.handle(InputEvent(EVENT_MOUSE_MOTION, x=60, y=10, dx=50, dy=0))
    ctl.handle(InputEvent(EVENT_MOUSE_BUTTON_UP, button=1, x=60, y=10))
    assert ctl.camera.yaw != yaw and ctl.state.cycle == 40  # a drag is not a click
    d = ctl.camera.distance
    ctl.handle(InputEvent(EVENT_MOUSE_WHEEL, y=1.0))
    assert ctl.camera.distance < d


def test_replace_run_follows_the_newest_cycle(
    ctl: Controller, view_stages: dict[str, Path]
) -> None:
    echo = load_run(view_stages["echo"])
    ctl.state.instance = 3
    ctl.state.follow = True
    ctl.replace_run(echo)
    assert ctl.state.instance == 0 and ctl.state.cycle == 200


def test_watcher_reloads_on_change_and_keeps_the_last_good_stage(
    tmp_path: Path, view_stages: dict[str, Path]
) -> None:
    stage = tmp_path / "live.usda"
    shutil.copy(view_stages["echo"], stage)
    calls: list[Path] = []
    fail = {"on": False}

    def loader(path: Path) -> RunView:
        calls.append(path)
        if fail["on"]:
            raise StageError("half written")
        return load_run(path)

    w = StageWatcher(stage, loader=loader, interval=0.0)
    w.prime()
    assert w.poll(1.0) is None and not calls  # unchanged
    os.utime(stage, ns=(1, 2_000_000_000))
    assert w.poll(2.0) is not None and not w.waiting
    fail["on"] = True
    os.utime(stage, ns=(1, 3_000_000_000))
    assert w.poll(3.0) is None and w.waiting
    fail["on"] = False
    assert w.poll(4.0) is not None and not w.waiting  # retried without a further change
    stage.unlink()
    assert w.poll(5.0) is None and w.waiting  # vanished: wait, keep the last good one


def test_helpers(view_stages: dict[str, Path]) -> None:
    run = load_run(view_stages["golden"])
    assert (
        clamp_cycle(run, None) == 200 and clamp_cycle(run, -5) == 1 and clamp_cycle(run, 999) == 200
    )
    assert instance_index(run, "Sage") == 2 and instance_index(run, None) == 0
    with pytest.raises(StageError, match="Echo, Rafael, Sage, Wren"):
        instance_index(run, "Nobody")
