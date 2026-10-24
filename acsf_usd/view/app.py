"""Drive Strata: one headless frame to a PNG, or the interactive window (with ``--watch``)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from acsf_usd.view import PANELS
from acsf_usd.view.model import RunView, StageError, load_run
from acsf_usd.view.scene import (
    BACKGROUND,
    LIGHT_AMBIENT,
    LIGHT_DIRECTION,
    Layout,
    OrbitCamera,
    ViewState,
    build_frame,
    frame_camera,
    pick,
)
from acsf_usd.view.strata import (
    EVENT_KEY_DOWN,
    EVENT_MOUSE_BUTTON_DOWN,
    EVENT_MOUSE_BUTTON_UP,
    EVENT_MOUSE_MOTION,
    EVENT_MOUSE_WHEEL,
    EVENT_QUIT,
    EVENT_RESIZED,
    Engine,
    InputEvent,
    Library,
)

# SDL3 scancodes (USB HID usage ids), as strata.h documents for StrataEvent.scancode.
KEY_F, KEY_Q, KEY_R, KEY_T = 9, 20, 21, 23
KEY_ESCAPE, KEY_TAB, KEY_SPACE = 41, 43, 44
KEY_LEFTBRACKET, KEY_RIGHTBRACKET = 47, 48
KEY_HOME, KEY_END = 74, 77
KEY_RIGHT, KEY_LEFT = 79, 80

SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)


class Renderer:
    """One engine plus the stage it draws."""

    def __init__(self, engine: Engine, run: RunView) -> None:
        self.engine = engine
        self.run = run
        self.layout = Layout.of(run)
        engine.set_legacy_lighting(LIGHT_DIRECTION, LIGHT_AMBIENT)

    def set_run(self, run: RunView) -> None:
        self.run = run
        self.layout = Layout.of(run)

    def draw(self, state: ViewState, camera: OrbitCamera) -> None:
        w, h = self.engine.drawable_size()
        self.engine.set_camera(
            camera.view_matrix(), camera.projection_matrix(w / max(h, 1)), camera.eye,
            camera.near, camera.far,
        )  # fmt: skip
        frame = build_frame(self.run, self.layout, state, camera, w, h)
        self.engine.begin_frame(BACKGROUND)
        self.engine.submit_primitives(frame.prims)
        self.engine.submit_lines(frame.lines)
        self.engine.submit_points(frame.glows)
        self.engine.submit_panels(frame.panels)
        self.engine.submit_text(frame.labels)
        self.engine.end_frame()


def clamp_cycle(run: RunView, cycle: int | None) -> int:
    if cycle is None:
        return int(run.end)
    return max(int(run.start), min(int(run.end), cycle))


def instance_index(run: RunView, name: str | None) -> int:
    if name is None:
        return 0
    for i, inst in enumerate(run.instances):
        if name in (inst.name, inst.prim_name):
            return i
    names = ", ".join(i.name for i in run.instances)
    raise StageError(f"no instance {name!r} in the stage (it has: {names})")


def render_png(
    lib: Library,
    run: RunView,
    out: Path,
    *,
    cycles: Iterable[int | None],
    size: tuple[int, int],
    instance: str | None = None,
    panel: str = "thought",
) -> list[Path]:
    """Render one PNG per cycle with a headless engine. Several cycles write
    ``<stem>-<cycle>.png`` next to ``out``."""
    wanted = list(cycles)
    idx = instance_index(run, instance)
    written = []
    with Engine(lib, size[0], size[1], headless=True) as engine:
        renderer = Renderer(engine, run)
        camera = frame_camera(run)
        for c in wanted:
            cycle = clamp_cycle(run, c)
            renderer.draw(ViewState(cycle=cycle, instance=idx, panel=panel), camera)
            path = out if len(wanted) == 1 else out.with_name(f"{out.stem}-{cycle}{out.suffix}")
            path.parent.mkdir(parents=True, exist_ok=True)
            engine.save_png(path)
            written.append(path)
    return written


# ---------------------------------------------------------------------------------------------
# --watch
# ---------------------------------------------------------------------------------------------


@dataclass
class StageWatcher:
    """Reopen the stage when its modification time changes. ``export --follow`` replaces the file
    atomically, so a reader never sees half a layer; a failed read keeps the last good stage and
    is retried on the next poll."""

    path: Path
    loader: Callable[[Path], RunView] = load_run
    interval: float = 0.5
    _mtime: float | None = None
    _next: float = 0.0
    waiting: bool = False

    def prime(self) -> None:
        self._mtime = self._stat()

    def _stat(self) -> float | None:
        try:
            return os.stat(self.path).st_mtime_ns / 1e9
        except OSError:
            return None

    def poll(self, now: float) -> RunView | None:
        """A freshly loaded stage when the file changed, else None."""
        if now < self._next:
            return None
        self._next = now + self.interval
        mtime = self._stat()
        if mtime is None:
            self.waiting = True
            return None
        if mtime == self._mtime and not self.waiting:
            return None
        try:
            run = self.loader(self.path)
        except (StageError, OSError, RuntimeError):
            self.waiting = True
            return None
        self._mtime = mtime
        self.waiting = False
        return run


# ---------------------------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------------------------


@dataclass
class Controller:
    """Input handling, kept apart from the engine so it can be tested with synthetic events."""

    run: RunView
    layout: Layout
    state: ViewState
    camera: OrbitCamera
    width: int
    height: int
    _drag: tuple[float, float] | None = None
    _moved: float = 0.0
    _clock: float = 0.0
    quit: bool = False

    def set_cycle(self, cycle: int) -> None:
        cycle = clamp_cycle(self.run, cycle)
        if cycle != self.state.cycle:
            self.state.cycle = cycle
            self.state.since_change = 0.0

    def handle(self, ev: InputEvent) -> None:
        s = self.state
        if ev.type == EVENT_QUIT:
            self.quit = True
        elif ev.type == EVENT_RESIZED and ev.width > 0 and ev.height > 0:
            self.width, self.height = ev.width, ev.height
        elif ev.type == EVENT_KEY_DOWN:
            self._key(ev.scancode, ev.repeat)
        elif ev.type == EVENT_MOUSE_BUTTON_DOWN and ev.button == 1:
            self._drag, self._moved = (ev.x, ev.y), 0.0
        elif ev.type == EVENT_MOUSE_MOTION and self._drag is not None:
            self._moved += abs(ev.dx) + abs(ev.dy)
            self.camera = self.camera.orbit(-0.3 * ev.dx, 0.25 * ev.dy)
        elif ev.type == EVENT_MOUSE_BUTTON_UP and ev.button == 1 and self._drag is not None:
            if self._moved < 4:
                hit = pick(
                    self.run, self.layout, self.camera, ev.x, ev.y, self.width, self.height,
                    float(s.cycle),
                )  # fmt: skip
                if hit is not None:
                    s.instance = hit[0]
                    s.follow = False
                    self.set_cycle(int(hit[1]))
            self._drag = None
        elif ev.type == EVENT_MOUSE_WHEEL:
            self.camera = self.camera.zoom(0.9 ** (ev.y or 0.0))

    def _key(self, code: int, repeat: bool) -> None:
        s = self.state
        if code in (KEY_ESCAPE, KEY_Q):
            self.quit = True
        elif code == KEY_SPACE and not repeat:
            s.playing = not s.playing
            if s.playing and s.cycle >= int(self.run.end):
                self.set_cycle(int(self.run.start))
            self._clock = 0.0
        elif code == KEY_RIGHT:
            s.playing = s.follow = False
            self.set_cycle(s.cycle + 1)
        elif code == KEY_LEFT:
            s.playing = s.follow = False
            self.set_cycle(s.cycle - 1)
        elif code == KEY_HOME:
            s.follow = False
            self.set_cycle(int(self.run.start))
        elif code == KEY_END:
            self.set_cycle(int(self.run.end))
        elif code in (KEY_LEFTBRACKET, KEY_RIGHTBRACKET):
            i = SPEEDS.index(s.speed) if s.speed in SPEEDS else 2
            i = i - 1 if code == KEY_LEFTBRACKET else i + 1
            s.speed = SPEEDS[max(0, min(len(SPEEDS) - 1, i))]
        elif code == KEY_TAB and not repeat:
            s.instance = (s.instance + 1) % len(self.run.instances)
        elif code == KEY_T and not repeat:
            s.panel = PANELS[(PANELS.index(s.panel) + 1) % len(PANELS)]
        elif code == KEY_F and not repeat:
            s.follow = not s.follow
            if s.follow:
                s.playing = False
                self.set_cycle(int(self.run.end))
        elif code == KEY_R and not repeat:
            self.camera = frame_camera(self.run)

    def tick(self, dt: float) -> None:
        s = self.state
        s.since_change += dt
        if s.playing:
            self._clock += dt * s.speed * self.run.time_codes_per_second
            while self._clock >= 1.0:
                self._clock -= 1.0
                if s.cycle >= int(self.run.end):
                    s.playing = False
                    break
                self.set_cycle(s.cycle + 1)

    def replace_run(self, run: RunView) -> None:
        self.run = run
        self.layout = Layout.of(run)
        self.state.instance = min(self.state.instance, len(run.instances) - 1)
        if self.state.follow:
            self.set_cycle(int(run.end))
        else:
            self.state.cycle = clamp_cycle(run, self.state.cycle)


def run_window(
    lib: Library,
    run: RunView,
    *,
    size: tuple[int, int],
    cycle: int | None = None,
    instance: str | None = None,
    watch: StageWatcher | None = None,
    max_frames: int | None = None,
    hidden: bool = False,
) -> None:
    state = ViewState(
        cycle=clamp_cycle(run, cycle),
        instance=instance_index(run, instance),
        interactive=True,
        follow=watch is not None and cycle is None,
    )
    with Engine(
        lib, size[0], size[1], headless=hidden, title=f"acsf-usd view - {run.path.name}"
    ) as engine:
        renderer = Renderer(engine, run)
        w, h = engine.drawable_size()
        ctl = Controller(run, renderer.layout, state, frame_camera(run), w, h)
        if watch is not None:
            watch.prime()
        last = time.monotonic()
        frames = 0
        while not ctl.quit:
            for ev in engine.poll_events():
                ctl.handle(ev)
            now = time.monotonic()
            ctl.tick(now - last)
            last = now
            if watch is not None:
                fresh = watch.poll(now)
                if fresh is not None:
                    ctl.replace_run(fresh)
                    renderer.set_run(fresh)
                state.status = (
                    "waiting for stage (keeping the last good one)"
                    if watch.waiting
                    else (
                        "watching "
                        + run.path.name
                        + (" - following the newest cycle" if state.follow else "")
                    )
                )
            ctl.width, ctl.height = engine.drawable_size()
            renderer.draw(state, ctl.camera)
            engine.present()
            frames += 1
            if max_frames is not None and frames >= max_frames:
                break
            time.sleep(0.004)


def camera_for(run: RunView, yaw: float = 0.0, pitch: float | None = None) -> OrbitCamera:
    cam = frame_camera(run)
    return replace(cam, yaw=yaw, pitch=cam.pitch if pitch is None else pitch)
