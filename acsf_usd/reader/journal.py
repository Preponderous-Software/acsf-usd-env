"""Tolerant reader for ACSF's append-only ``journal.jsonl``.

Mirrors ACSF's own reader (``persistence/journal.py`` at 64486d9, L49–L80): blank lines are
ignored and corrupt or non-object lines are skipped with a warning; they never abort a read.
Unknown event types are kept. The file is only ever opened ``"rb"``.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acsf_usd.model import CycleTrace, Event

log = logging.getLogger(__name__)

# Fields every ACSF journal line carries; the rest are kept as ``extras``.
_CANONICAL = ("timestamp", "type", "content")


@dataclass(frozen=True)
class JournalParse:
    events: tuple[Event, ...]
    warnings: tuple[str, ...]


def read_journal(path: Path) -> JournalParse:
    """Stream a whole journal line by line. A missing file is an empty journal."""
    events: list[Event] = []
    warnings: list[str] = []
    try:
        with path.open("rb") as handle:
            for line_no, raw in enumerate(handle, start=1):
                _accept(raw, line_no, events, warnings)
    except FileNotFoundError:
        return JournalParse((), ())
    return JournalParse(tuple(events), tuple(warnings))


def split_lines(data: bytes) -> list[bytes]:
    """Split journal bytes into lines; a trailing newline does not add an empty line."""
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def parse_journal_bytes(data: bytes, first_line_no: int = 1) -> JournalParse:
    """Parse journal bytes already in memory (follow mode reads appended chunks)."""
    events: list[Event] = []
    warnings: list[str] = []
    for offset, raw in enumerate(split_lines(data)):
        _accept(raw, first_line_no + offset, events, warnings)
    return JournalParse(tuple(events), tuple(warnings))


def _accept(raw: bytes, line_no: int, events: list[Event], warnings: list[str]) -> None:
    event, warning = parse_line(raw, line_no)
    if warning is not None:
        log.warning("%s", warning)
        warnings.append(warning)
    if event is not None:
        events.append(event)


def parse_line(raw: bytes, line_no: int) -> tuple[Event | None, str | None]:
    """Parse one journal line into an event, or explain why it was skipped."""
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None, f"journal line {line_no}: skipping line that is not valid UTF-8"
    if not text:
        return None, None
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        return None, f"journal line {line_no}: skipping corrupted line: {text[:80]!r}"
    if not isinstance(payload, dict):
        return None, f"journal line {line_no}: skipping non-object line: {text[:80]!r}"
    event_type = payload.get("type")
    if not isinstance(event_type, str) or not event_type:
        return None, f"journal line {line_no}: skipping object without a string 'type'"
    content = _as_text(payload.get("content", ""))
    timestamp = payload.get("timestamp", "")
    extras = {k: v for k, v in payload.items() if k not in _CANONICAL}
    warning: str | None = None
    trace: CycleTrace | None = None
    if event_type == "cycle":
        # The retrieved memory summaries are long-term memory text: keep only their count.
        retrieved = extras.pop("retrieved", None)
        trace = _cycle_trace(extras, retrieved)
        if trace is None:
            warning = f"journal line {line_no}: 'cycle' event without an integer thought_count"
    return (
        Event(
            line_no=line_no,
            type=event_type,
            timestamp=timestamp if isinstance(timestamp, str) else str(timestamp),
            content=content,
            extras=extras,
            cycle=trace,
        ),
        warning,
    )


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _cycle_trace(extras: Mapping[str, Any], retrieved: Any) -> CycleTrace | None:
    thought_count = _int(extras.get("thought_count"))
    if thought_count is None:
        return None
    prediction = _mapping(extras.get("prediction"))
    reflection = _mapping(extras.get("reflection"))
    attention = _mapping(extras.get("attention"))
    fired = reflection.get("fired")
    mood_raw = extras.get("mood")
    mood: dict[str, float] | None = None
    if isinstance(mood_raw, dict):
        mood = {}
        for dim, value in mood_raw.items():
            number = _float(value)
            if isinstance(dim, str) and number is not None:
                mood[dim] = number
    return CycleTrace(
        thought_count=thought_count,
        metacognitive_label=_str(extras.get("metacognitive_label")),
        prediction_error=_float(prediction.get("error")),
        reflection_effective=_float(reflection.get("effective")),
        reflection_fired=fired if isinstance(fired, bool) else None,
        short_term_size=_int(extras.get("short_term_size")),
        retrieved_count=len(retrieved) if isinstance(retrieved, list) else None,
        attention_focus=_str(attention.get("focus")),
        attention_theme=_str(attention.get("theme")),
        attention_salience=_float(attention.get("salience")),
        mood=mood,
    )
