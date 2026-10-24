"""A small ``ctypes`` binding of the slice of Strata's C ABI (``strata.h``) the viewer uses.

Strata ships no Python binding yet, so this module declares the slice of ``strata.h`` the viewer
uses. Every struct is checked against the library's own ``strata_struct_size`` when the library
is loaded, the same guard Strata's C# binding uses, so a layout drift is reported as a clear
error instead of a crash. Nothing here is specific to ACSF.

The library itself is never bundled: :mod:`acsf_usd.view.fetch` downloads the release asset
pinned in ``strata.lock`` and checks its SHA-256 first.
"""

from __future__ import annotations

import ctypes as C
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ABI_MAJOR = 1
ABI_MINOR_REQUIRED = 5  # line, point and text overlays

SURFACE_WINDOW = 0
SURFACE_HEADLESS = 1

FLAG_VSYNC = 1 << 0
FLAG_HIDDEN_WINDOW = 1 << 2

PRIMITIVE_CUBE = 0
PRIMITIVE_CYLINDER = 2
PRIMITIVE_SPHERE = 3

LIGHTING_LEGACY = 0

CLEAR_COLOR = 1 << 0
CLEAR_DEPTH = 1 << 1

POINT_SOFT = 0x1
TEXT_SHADOW = 0x1

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

F3 = C.c_float * 3
F4 = C.c_float * 4
F16 = C.c_float * 16


class RenderItem(C.Structure):
    _fields_ = [("kind", C.c_int32), ("position", F3), ("scale", F3), ("color", F4)]


class EngineDesc(C.Structure):
    _fields_ = [
        ("struct_size", C.c_uint32),
        ("abi_version", C.c_uint32),
        ("surface", C.c_int32),
        ("width", C.c_int32),
        ("height", C.c_int32),
        ("flags", C.c_uint32),
        ("title", C.c_char_p),
        ("get_proc_address", C.c_void_p),
        ("get_proc_address_user", C.c_void_p),
    ]


class Camera(C.Structure):
    _fields_ = [
        ("view", F16),
        ("projection", F16),
        ("eye", F3),
        ("near_plane", C.c_float),
        ("far_plane", C.c_float),
    ]


class Lighting(C.Structure):
    _fields_ = [
        ("mode", C.c_int32),
        ("legacy_direction", F3),
        ("legacy_ambient", C.c_float),
        ("time_of_day", C.c_float),
        ("latitude_degrees", C.c_float),
        ("cascade_count", C.c_int32),
        ("shadow_map_size", C.c_int32),
        ("shadow_distance", C.c_float),
        ("split_lambda", C.c_float),
        ("fog", C.c_int32),
    ]


class FrameDesc(C.Structure):
    _fields_ = [("clear_flags", C.c_uint32), ("clear_color", F4)]


class Event(C.Structure):
    _fields_ = [
        ("type", C.c_int32),
        ("scancode", C.c_int32),
        ("modifiers", C.c_int32),
        ("repeat", C.c_int32),
        ("button", C.c_int32),
        ("width", C.c_int32),
        ("height", C.c_int32),
        ("x", C.c_float),
        ("y", C.c_float),
        ("dx", C.c_float),
        ("dy", C.c_float),
        ("text", C.c_char * 32),
    ]


class LineVertex(C.Structure):
    _fields_ = [("position", F3), ("color", F4)]


class Point(C.Structure):
    _fields_ = [
        ("position", F3),
        ("size_pixels", C.c_float),
        ("world_diameter", C.c_float),
        ("color", F4),
        ("flags", C.c_uint32),
    ]


class Text(C.Structure):
    _fields_ = [
        ("x", C.c_float),
        ("y", C.c_float),
        ("pixel_size", C.c_float),
        ("flags", C.c_uint32),
        ("color", F4),
        ("utf8", C.c_char_p),
    ]


# STRATA_STRUCT_* ids from strata.h, checked at load.
STRUCT_IDS: dict[int, type[C.Structure]] = {
    0: RenderItem,
    1: EngineDesc,
    2: Camera,
    3: Lighting,
    4: FrameDesc,
    6: Event,
    10: LineVertex,
    11: Point,
    12: Text,
}

_P = C.c_void_p
_I = C.c_int32
_S = C.c_int32  # StrataStatus
_SIGNATURES: dict[str, tuple[Any, list[Any]]] = {
    "strata_abi_version": (C.c_uint32, []),
    "strata_abi_minor": (C.c_uint32, []),
    "strata_version_string": (C.c_char_p, []),
    "strata_struct_size": (_I, [_I]),
    "strata_last_error": (C.c_char_p, []),
    "strata_create": (_S, [_P, C.POINTER(_P)]),
    "strata_destroy": (None, [_P]),
    "strata_gpu_description": (C.c_char_p, [_P]),
    "strata_poll_event": (_I, [_P, _P]),
    "strata_get_drawable_size": (_S, [_P, C.POINTER(_I), C.POINTER(_I)]),
    "strata_set_camera": (_S, [_P, _P]),
    "strata_lighting_defaults": (_S, [_I, _P]),
    "strata_set_lighting": (_S, [_P, _P]),
    "strata_begin_frame": (_S, [_P, _P]),
    "strata_submit_primitives": (_S, [_P, _P, _I]),
    "strata_submit_lines": (_S, [_P, _P, _I]),
    "strata_submit_points": (_S, [_P, _P, _I]),
    "strata_submit_text": (_S, [_P, _P, _I]),
    "strata_end_frame": (_S, [_P]),
    "strata_present": (_S, [_P]),
    "strata_read_pixels_rgba8": (_S, [_P, _P, _I, C.POINTER(_I), C.POINTER(_I)]),
    "strata_save_screenshot_png": (_S, [_P, C.c_char_p]),
}


class StrataError(Exception):
    """A Strata call failed, or the library cannot be used."""


class StrataUnavailable(StrataError):
    """The library is missing, does not load, or is too old for the viewer."""


class Library:
    """A loaded, checked ``libstrata``."""

    def __init__(self, path: Path) -> None:
        try:
            self.dll: Any = C.CDLL(str(path))
        except OSError as exc:
            raise StrataUnavailable(f"cannot load {path}: {exc}") from None
        for name, (restype, argtypes) in _SIGNATURES.items():
            try:
                fn = getattr(self.dll, name)
            except AttributeError:
                raise StrataUnavailable(f"{path} has no {name}: not a Strata library") from None
            fn.restype = restype
            fn.argtypes = argtypes
        major, minor = int(self.dll.strata_abi_version()), int(self.dll.strata_abi_minor())
        if major != ABI_MAJOR or minor < ABI_MINOR_REQUIRED:
            raise StrataUnavailable(
                f"{path} has Strata ABI {major}.{minor}; the viewer needs "
                f"{ABI_MAJOR}.{ABI_MINOR_REQUIRED} or a later {ABI_MAJOR}.x"
            )
        for struct_id, cls in STRUCT_IDS.items():
            size = int(self.dll.strata_struct_size(struct_id))
            if size != C.sizeof(cls):
                raise StrataUnavailable(
                    f"{path}: {cls.__name__} is {size} bytes in the library but "
                    f"{C.sizeof(cls)} in this binding (ABI drift)"
                )
        self.path = path
        self.abi = (major, minor)
        self.version = (self.dll.strata_version_string() or b"?").decode()

    def last_error(self) -> str:
        msg = self.dll.strata_last_error()
        return msg.decode(errors="replace") if msg else ""


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
        self._dll = lib.dll
        surface, flags = SURFACE_WINDOW, 0
        if headless:
            # Windows has no EGL: a hidden SDL3 window stands in for the headless surface.
            if sys.platform == "win32":
                flags |= FLAG_HIDDEN_WINDOW
            else:
                surface = SURFACE_HEADLESS
        elif vsync:
            flags |= FLAG_VSYNC
        self._title = title.encode()
        desc = EngineDesc(
            C.sizeof(EngineDesc), ABI_MAJOR, surface, width, height, flags, self._title, None, None
        )
        handle = C.c_void_p()
        status = self._dll.strata_create(C.byref(desc), C.byref(handle))
        if status != 0 or not handle.value:
            raise StrataUnavailable(f"strata_create failed ({status}): {lib.last_error()}")
        self._handle: C.c_void_p | None = handle

    # -- lifecycle -----------------------------------------------------------------------------

    def close(self) -> None:
        if self._handle is not None:
            self._dll.strata_destroy(self._handle)
            self._handle = None

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def handle(self) -> C.c_void_p:
        if self._handle is None:
            raise StrataError("engine is closed")
        return self._handle

    def _ok(self, status: int, what: str) -> None:
        if status != 0:
            raise StrataError(f"{what} failed ({status}): {self.lib.last_error()}")

    def gpu_description(self) -> str:
        desc = self._dll.strata_gpu_description(self.handle)
        return desc.decode(errors="replace") if desc else ""

    def drawable_size(self) -> tuple[int, int]:
        w, h = C.c_int32(), C.c_int32()
        self._ok(
            self._dll.strata_get_drawable_size(self.handle, C.byref(w), C.byref(h)),
            "get_drawable_size",
        )
        return int(w.value), int(h.value)

    # -- state ---------------------------------------------------------------------------------

    def set_camera(
        self, view: Sequence[float], projection: Sequence[float], eye: Vec3, near: float, far: float
    ) -> None:
        cam = Camera(F16(*view), F16(*projection), F3(*eye), near, far)
        self._ok(self._dll.strata_set_camera(self.handle, C.byref(cam)), "set_camera")

    def set_legacy_lighting(self, direction: Vec3, ambient: float) -> None:
        light = Lighting()
        self._ok(self._dll.strata_lighting_defaults(LIGHTING_LEGACY, C.byref(light)), "lighting")
        light.legacy_direction = F3(*direction)
        light.legacy_ambient = ambient
        self._ok(self._dll.strata_set_lighting(self.handle, C.byref(light)), "set_lighting")

    # -- frames --------------------------------------------------------------------------------

    def begin_frame(self, clear: RGBA) -> None:
        desc = FrameDesc(CLEAR_COLOR | CLEAR_DEPTH, F4(*clear))
        self._ok(self._dll.strata_begin_frame(self.handle, C.byref(desc)), "begin_frame")

    def submit_primitives(self, prims: Sequence[Prim]) -> None:
        if not prims:
            return
        arr = (RenderItem * len(prims))(
            *(RenderItem(p.kind, F3(*p.position), F3(*p.scale), F4(*p.color)) for p in prims)
        )
        self._ok(self._dll.strata_submit_primitives(self.handle, arr, len(prims)), "primitives")

    def submit_lines(self, lines: Sequence[Line]) -> None:
        if not lines:
            return
        arr = (LineVertex * (2 * len(lines)))()
        for i, ln in enumerate(lines):
            arr[2 * i] = LineVertex(F3(*ln.a), F4(*ln.color))
            arr[2 * i + 1] = LineVertex(F3(*ln.b), F4(*(ln.color_b or ln.color)))
        self._ok(self._dll.strata_submit_lines(self.handle, arr, len(arr)), "lines")

    def submit_points(self, points: Sequence[Glow]) -> None:
        if not points:
            return
        arr = (Point * len(points))(
            *(
                Point(
                    F3(*p.position),
                    p.size_pixels,
                    p.world_diameter,
                    F4(*p.color),
                    POINT_SOFT if p.soft else 0,
                )
                for p in points
            )
        )
        self._ok(self._dll.strata_submit_points(self.handle, arr, len(points)), "points")

    def submit_text(self, labels: Sequence[Label]) -> None:
        if not labels:
            return
        keep = [ascii_bytes(lb.text) for lb in labels]  # alive until the call returns
        arr = (Text * len(labels))(
            *(
                Text(lb.x, lb.y, lb.size, TEXT_SHADOW if lb.shadow else 0, F4(*lb.color), raw)
                for lb, raw in zip(labels, keep, strict=True)
            )
        )
        self._ok(self._dll.strata_submit_text(self.handle, arr, len(labels)), "text")

    def end_frame(self) -> None:
        self._ok(self._dll.strata_end_frame(self.handle), "end_frame")

    def present(self) -> None:
        self._ok(self._dll.strata_present(self.handle), "present")

    def read_pixels(self) -> tuple[int, int, bytes]:
        """The last frame as tightly packed RGBA8, top row first."""
        w, h = self.drawable_size()
        buf = (C.c_uint8 * (w * h * 4))()
        ow, oh = C.c_int32(), C.c_int32()
        self._ok(
            self._dll.strata_read_pixels_rgba8(
                self.handle, buf, len(buf), C.byref(ow), C.byref(oh)
            ),
            "read_pixels",
        )
        return int(ow.value), int(oh.value), bytes(buf)[: ow.value * oh.value * 4]

    def save_png(self, path: Path) -> None:
        self._ok(self._dll.strata_save_screenshot_png(self.handle, str(path).encode()), "save_png")

    def poll_events(self) -> Iterator[InputEvent]:
        ev = Event()
        while self._dll.strata_poll_event(self.handle, C.byref(ev)):
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
