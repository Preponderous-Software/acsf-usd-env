"""Reader for ACSF's ``state.json`` snapshot.

``state.json`` is overwritten on every save (ACSF ``core/consciousness.py`` at 64486d9,
L801–L808), so a missing, corrupt or non-object file yields "no snapshot" and a warning,
never an exception that aborts an export.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from acsf_usd.model import Snapshot, WorkspaceItem

log = logging.getLogger(__name__)


def read_state(path: Path) -> tuple[Snapshot | None, str | None]:
    """Return the snapshot, or ``None`` and the reason it could not be read."""
    try:
        with path.open("rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return None, f"{path.name}: not found; exporting without a snapshot"
    except OSError as exc:
        return None, f"{path.name}: unreadable ({exc}); exporting without a snapshot"
    return parse_state_bytes(data, path.name)


def parse_state_bytes(data: bytes, label: str = "state.json") -> tuple[Snapshot | None, str | None]:
    try:
        payload: Any = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, f"{label}: not valid JSON; exporting without a snapshot"
    if not isinstance(payload, dict):
        return None, f"{label}: not a JSON object; exporting without a snapshot"
    return snapshot_from_dict(payload), None


def snapshot_from_dict(payload: Mapping[str, Any]) -> Snapshot:
    identity = _mapping(payload.get("identity"))
    attention = _mapping(identity.get("attention_schema"))
    raw_items = payload.get("short_term")
    items = tuple(
        _workspace_item(item) for item in (raw_items if isinstance(raw_items, list) else [])
    )
    thought_count = payload.get("thought_count")
    return Snapshot(
        name=_str(identity.get("name")),
        origin_story=_str(identity.get("origin_story")),
        purpose=_str(identity.get("purpose")),
        self_concept=_str(identity.get("self_concept")),
        values=_str_tuple(identity.get("values")),
        personality_traits=_str_tuple(identity.get("personality_traits")),
        amendments=_str_tuple(identity.get("amendments")),
        mood=_float_map(identity.get("mood")),
        initial_mood=_float_map(identity.get("initial_mood")),
        attention_focus=_str(attention.get("focus")),
        attention_theme=_str(attention.get("theme")),
        attention_salience=_float(attention.get("salience")),
        short_term=tuple(item for item in items if item is not None),
        thought_count=thought_count
        if isinstance(thought_count, int) and not isinstance(thought_count, bool)
        else None,
    )


def _workspace_item(raw: Any) -> WorkspaceItem | None:
    if not isinstance(raw, dict):
        return None
    seq = raw.get("seq")
    importance = _float(raw.get("importance"))
    return WorkspaceItem(
        kind=_str(raw.get("kind")) or "",
        content=_str(raw.get("content")) or "",
        timestamp=_str(raw.get("timestamp")) or "",
        importance=importance if importance is not None else 0.0,
        seq=seq if isinstance(seq, int) and not isinstance(seq, bool) else None,
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _float_map(value: Any) -> dict[str, float]:
    result: dict[str, float] = {}
    if isinstance(value, dict):
        for key, raw in value.items():
            number = _float(raw)
            if isinstance(key, str) and number is not None:
                result[key] = number
    return result
