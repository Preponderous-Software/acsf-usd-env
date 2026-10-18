"""Small helpers shared by the stage writers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from pxr import Sdf, Usd, Vt

NAMESPACE = "acsf"
WORLD = "/World"
INSTANCES = "/World/Instances"

_INVALID = re.compile(r"[^A-Za-z0-9_]")


def prim_name(name: str) -> str:
    """A valid USD prim name for an arbitrary string, deterministically.

    Characters outside ``[A-Za-z0-9_]`` become ``_`` and a leading digit (or an empty
    result) gets a ``_`` prefix. The original string is kept in an attribute.
    """
    cleaned = _INVALID.sub("_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def instance_path(name: str) -> Sdf.Path:
    return Sdf.Path(f"{INSTANCES}/{prim_name(name)}")


def set_attr(prim: Usd.Prim, name: str, type_name: Any, value: Any) -> Usd.Attribute:
    """Create a custom attribute and set its default (non-time-sampled) value."""
    attr = prim.CreateAttribute(name, type_name, True)
    attr.Set(value)
    return attr


def string_array(values: Iterable[str]) -> Vt.StringArray:
    return Vt.StringArray(list(values))
