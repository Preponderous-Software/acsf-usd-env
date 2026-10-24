"""The viewer's side of Strata: plain draw records and a thin engine over the official binding.

Strata's C ABI (``strata.h``) is reached through Strata's official Python binding (the
``strata-engine`` package, ``bindings/python`` in the Strata repository, installed by the
``[view]`` extra and pinned to the release ``strata.lock`` names). The binding declares every
function and struct, and checks the ABI major version and every struct layout against the loaded
library, so a layout drift is reported as a clear error instead of a crash.

This module keeps what the viewer itself needs: frozen draw records (:class:`Prim`,
:class:`Line`, :class:`Glow`, :class:`Label`, :class:`Panel`) that the scene builder produces
without touching ``ctypes``, and an :class:`Engine` that turns them into the binding's structs.
The binding is imported only when a library is loaded, so ``acsf-usd export`` and the scene
tests never import it. Nothing here is specific to ACSF.

The library itself is never bundled: :mod:`acsf_usd.view.fetch` downloads the release asset
pinned in ``strata.lock`` and checks its SHA-256 first.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ABI_MAJOR = 1
ABI_MINOR_REQUIRED = 5  # line, point and text overlays
ABI_MINOR_RECTS = 11  # filled screen rectangles (the text panel's backing)

# strata.h enum values the scene builder and the input handling use (stable within ABI 1.x).
PRIMITIVE_CUBE = 0
PRIMITIVE_CYLINDER = 2
PRIMITIVE_SPHERE = 3

EVENT_QUIT = 1
EVENT_KEY_DOWN = 2
EVENT_KEY_UP = 3
EVENT_MOUSE_MOTION = 4
EVENT_MOUSE_BUTTON_DOWN = 5
EVENT_MOUSE_BUTTON_UP = 6
EVENT_MOUSE_WHEEL = 7
EVENT_RESIZED = 8

Vec3 = tuple[float, float, float]
RGBA = tuple[float, float, float, float]


class StrataError(Exception):
    """A Strata call failed, or the library cannot be used."""


class StrataUnavailable(StrataError):
    """The binding or library is missing, does not load, or is too old for the viewer."""


def _binding() -> Any:
    try:
        import strata
    except ImportError:
        raise StrataUnavailable(
            "the Strata Python binding is not installed: pip install 'acsf-usd-env[view]'"
        ) from None
    return strata


class Library:
    """A loaded ``libstrata``, checked by the official binding."""

    def __init__(self, path: Path) -> None:
        strata = _binding()
        try:
            self.binding: Any = strata.load(path)
        except strata.StrataLoadError as exc:
            raise StrataUnavailable(str(exc)) from None
        except strata.StrataAbiError as exc:
            raise StrataUnavailable(f"{path}: {exc}") from None
        major, minor = int(self.binding.abi_version), int(self.binding.abi_minor)
        if minor < ABI_MINOR_REQUIRED:
            raise StrataUnavailable(
                f"{path} has Strata ABI {major}.{minor}; the viewer needs "
                f"{ABI_MAJOR}.{ABI_MINOR_REQUIRED} or a later {ABI_MAJOR}.x"
            )
        self.module = strata
        self.dll: Any = self.binding.raw
        self.path = path
        self.abi = (major, minor)
        self.version = str(self.binding.version)
        self.has_rects = bool(self.binding.has("strata_submit_rects"))

    def last_error(self) -> str:
        return str(self.binding.last_error())


@dataclass(frozen=True)
class Prim:
    kind: int
    position: Vec3
    scale: Vec3
    color: RGBA


@dataclass(frozen=True)
class Line:
    a: Vec3
    b: Vec3
    color: RGBA
    color_b: RGBA | None = None


@dataclass(frozen=True)
class Glow:
    position: Vec3
    size_pixels: float
    world_diameter: float
    color: RGBA
    soft: bool = True


@dataclass(frozen=True)
class Label:
    x: float
    y: float
    size: float
    text: str
    color: RGBA
    shadow: bool = True


@dataclass(frozen=True)
class Panel:
    """A filled, alpha-blended screen rectangle drawn after the scene and before any text."""

    x: float
    y: float
    width: float
    height: float
    color: RGBA


@dataclass(frozen=True)
class InputEvent:
    type: int
    scancode: int = 0
    modifiers: int = 0
    repeat: bool = False
    button: int = 0
    x: float = 0.0
    y: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    width: int = 0
    height: int = 0


def ascii_bytes(text: str) -> bytes:
    """Strata's font covers printable ASCII; callers fold text first, this is the last guard."""
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text).encode("ascii")


def _set(arr: Any, values: Sequence[float]) -> None:
    for i, v in enumerate(values):
        arr[i] = v


class Engine:
    """One Strata engine: a window, or a headless offscreen target."""

    def __init__(
        self,
        lib: Library,
        width: int,
        height: int,
        *,
        headless: bool,
        title: str = "acsf-usd view",
        vsync: bool = True,
    ) -> None:
        self.lib = lib
        s = self._s = lib.module
        surface, flags = s.SURFACE_WINDOW, 0
        if headless:
            # Windows has no EGL: a hidden SDL3 window stands in for the headless surface.
            if sys.platform == "win32":
                flags |= s.FLAG_HIDDEN_WINDOW
            else:
                surface = s.SURFACE_HEADLESS
        elif vsync:
            flags |= s.FLAG_VSYNC
        try:
            self._engine: Any = s.Engine(
                lib.binding, width, height, surface=surface, flags=flags, title=title
            )
        except s.StrataError as exc:
            raise StrataUnavailable(f"strata_create failed: {exc}") from None

    # -- lifecycle -----------------------------------------------------------------------------

    def close(self) -> None:
        self._engine.close()

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _call(self, fn: Any, *args: Any) -> Any:
        try:
            return fn(*args)
        except self._s.StrataError as exc:
            raise StrataError(str(exc)) from None

    def gpu_description(self) -> str:
        return str(self._call(self._engine.gpu_description))

    def drawable_size(self) -> tuple[int, int]:
        w, h = self._call(self._engine.drawable_size)
        return int(w), int(h)

    # -- state ---------------------------------------------------------------------------------

    def set_camera(
        self, view: Sequence[float], projection: Sequence[float], eye: Vec3, near: float, far: float
    ) -> None:
        cam = self._s.Camera()
        _set(cam.view, view)
        _set(cam.projection, projection)
        _set(cam.eye, eye)
        cam.near_plane, cam.far_plane = near, far
        self._call(self._engine.set_camera, cam)

    def set_legacy_lighting(self, direction: Vec3, ambient: float) -> None:
        light = self._call(self.lib.binding.lighting_defaults, self._s.LIGHTING_LEGACY)
        _set(light.legacy_direction, direction)
        light.legacy_ambient = ambient
        self._call(self._engine.set_lighting, light)

    # -- frames --------------------------------------------------------------------------------

    def begin_frame(self, clear: RGBA) -> None:
        self._call(self._engine.begin_frame, clear)

    def submit_primitives(self, prims: Sequence[Prim]) -> None:
        if not prims:
            return
        s = self._s
        arr = (s.RenderItem * len(prims))()
        for item, p in zip(arr, prims, strict=True):
            item.kind = p.kind
            _set(item.position, p.position)
            _set(item.scale, p.scale)
            _set(item.color, p.color)
        self._call(self._engine.submit_primitives, arr)

    def submit_lines(self, lines: Sequence[Line]) -> None:
        if not lines:
            return
        arr = (self._s.LineVertex * (2 * len(lines)))()
        for i, ln in enumerate(lines):
            _set(arr[2 * i].position, ln.a)
            _set(arr[2 * i].color, ln.color)
            _set(arr[2 * i + 1].position, ln.b)
            _set(arr[2 * i + 1].color, ln.color_b or ln.color)
        self._call(self._engine.submit_lines, arr)

    def submit_points(self, points: Sequence[Glow]) -> None:
        if not points:
            return
        s = self._s
        arr = (s.Point * len(points))()
        for pt, p in zip(arr, points, strict=True):
            _set(pt.position, p.position)
            pt.size_pixels = p.size_pixels
            pt.world_diameter = p.world_diameter
            _set(pt.color, p.color)
            pt.flags = s.POINT_SOFT if p.soft else 0
        self._call(self._engine.submit_points, arr)

    def submit_panels(self, panels: Sequence[Panel]) -> None:
        """Filled rectangles (ABI 1.11); skipped on an older library, which draws text bare."""
        if not panels or not self.lib.has_rects:
            return
        arr = (self._s.Rect * len(panels))()
        for r, p in zip(arr, panels, strict=True):
            r.x, r.y, r.width, r.height = p.x, p.y, p.width, p.height
            _set(r.color, p.color)
        self._call(self._engine.submit_rects, arr)

    def submit_text(self, labels: Sequence[Label]) -> None:
        if not labels:
            return
        s = self._s
        keep = [ascii_bytes(lb.text) for lb in labels]  # alive until the call returns
        arr = (s.Text * len(labels))()
        for t, lb, raw in zip(arr, labels, keep, strict=True):
            t.x, t.y, t.pixel_size = lb.x, lb.y, lb.size
            t.flags = s.TEXT_SHADOW if lb.shadow else 0
            _set(t.color, lb.color)
            t.utf8 = raw
        self._call(self._engine.submit_text, arr)
        del keep

    def end_frame(self) -> None:
        self._call(self._engine.end_frame)

    def present(self) -> None:
        self._call(self._engine.present)

    def read_pixels(self) -> tuple[int, int, bytes]:
        """The last frame as tightly packed RGBA8, top row first."""
        data, w, h = self._call(self._engine.read_pixels)
        return int(w), int(h), bytes(data)

    def save_png(self, path: Path) -> None:
        self._call(self._engine.save_screenshot_png, path)

    def poll_events(self) -> Iterator[InputEvent]:
        while (ev := self._call(self._engine.poll_event)) is not None:
            yield InputEvent(
                type=int(ev.type),
                scancode=int(ev.scancode),
                modifiers=int(ev.modifiers),
                repeat=bool(ev.repeat),
                button=int(ev.button),
                x=float(ev.x),
                y=float(ev.y),
                dx=float(ev.dx),
                dy=float(ev.dy),
                width=int(ev.width),
                height=int(ev.height),
            )
