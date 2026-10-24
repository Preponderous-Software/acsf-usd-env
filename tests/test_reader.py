"""Milestone 2: journal and state readers, cycle indexing and input resolution."""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from acsf_usd.model import Event
from acsf_usd.reader import (
    InputError,
    index_events,
    parse_journal_bytes,
    read_instance,
    read_journal,
    read_state,
    resolve_inputs,
    resolve_instance_dir,
)
from acsf_usd.reader.resolve import consciousness_home
from tests.conftest import INSTANCES


def _line(event_type: str, content: str = "", **extras: object) -> bytes:
    payload = {"timestamp": "t", "type": event_type, "content": content, **extras}
    return json.dumps(payload).encode() + b"\n"


def _events(*lines: bytes) -> tuple[Event, ...]:
    return parse_journal_bytes(b"".join(lines)).events


# --- reader.journal -------------------------------------------------------------------


def test_every_acsf_event_type_and_unknown_type_is_kept() -> None:
    parsed = read_journal(INSTANCES / "Nova" / "journal.jsonl")
    types = {e.type for e in parsed.events}
    assert types == {
        "perception",
        "thought",
        "cycle",
        "utterance",
        "reflection",
        "identity_shift",
        "memory",
        "consolidation",
        "custom_probe",
    }


def test_corrupt_non_object_and_blank_lines_are_skipped_with_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        parsed = read_journal(INSTANCES / "Nova" / "journal.jsonl")
    assert len(parsed.events) == 17
    assert len(parsed.warnings) == 2
    assert "corrupted" in parsed.warnings[0]
    assert "non-object" in parsed.warnings[1]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


@pytest.mark.parametrize(
    "raw",
    [b"\xff\xfe not utf-8\n", b'{"type": 5}\n', b'{"content": "no type"}\n', b"null\n"],
)
def test_malformed_lines_never_abort(raw: bytes) -> None:
    parsed = parse_journal_bytes(raw + _line("thought", "survivor"))
    assert [e.content for e in parsed.events] == ["survivor"]
    assert len(parsed.warnings) == 1


def test_missing_journal_is_empty() -> None:
    parsed = read_journal(Path("/nonexistent/journal.jsonl"))
    assert parsed.events == () and parsed.warnings == ()


def test_utterance_and_memory_extras_are_kept() -> None:
    events = read_journal(INSTANCES / "Nova" / "journal.jsonl").events
    utterance = next(e for e in events if e.type == "utterance")
    assert utterance.extras["to"] == "Dana"
    assert utterance.extras["in_reply_to"] == "Hello Nova, what are you thinking about?"
    memory = next(e for e in events if e.type == "memory")
    assert memory.extras["long_term_count"] == 4
    consolidation = next(e for e in events if e.type == "consolidation")
    assert consolidation.extras["long_term_total"] == 6
    assert consolidation.extras["stored"] == 2


def test_cycle_event_is_typed_and_keeps_only_a_retrieved_count() -> None:
    events = read_journal(INSTANCES / "Nova" / "journal.jsonl").events
    cycles = [e for e in events if e.type == "cycle"]
    assert [c.cycle.thought_count for c in cycles if c.cycle] == [1, 2, 4]
    second = cycles[1].cycle
    assert second is not None
    assert second.metacognitive_label == "metacognitive-surprised"
    assert second.prediction_error == 2.5
    assert second.reflection_effective == 0.6
    assert second.reflection_fired is True
    assert second.short_term_size == 4
    assert second.retrieved_count == 2
    assert (second.attention_focus, second.attention_theme) == ("perception", "greeting")
    assert second.attention_salience == 0.95
    assert second.mood is None
    # No long-term memory text survives anywhere in the parsed model.
    assert "SECRET-LTM" not in repr(events)


def test_cycle_event_with_and_without_mood_both_parse() -> None:
    events = read_journal(INSTANCES / "Lumen" / "journal.jsonl").events
    traces = [e.cycle for e in events if e.cycle is not None]
    assert [t.mood for t in traces] == [
        None,
        {"curiosity": 0.71, "wonder": 0.6},
        {"curiosity": 0.73, "wonder": 0.58},
    ]


def test_cycle_event_without_integer_thought_count_warns_and_is_not_authoritative() -> None:
    parsed = parse_journal_bytes(_line("thought", "a") + _line("cycle", "x", thought_count="7"))
    assert parsed.events[1].cycle is None
    assert "thought_count" in parsed.warnings[0]
    assert index_events(parsed.events).indexing == "counted"


def test_non_string_content_is_serialised_deterministically() -> None:
    (event,) = _events(_line("custom", content={"b": 1, "a": 2}))  # type: ignore[arg-type]
    assert event.content == '{"a": 2, "b": 1}'


# --- reader.cycles --------------------------------------------------------------------


def _cycle_map(events: tuple[Event, ...]) -> list[tuple[str, int, bool]]:
    return [(i.event.type, i.cycle, i.background) for i in index_events(events).events]


def test_rule_1_journaled_indexing_with_failed_cycle_gap() -> None:
    indexed = index_events(read_journal(INSTANCES / "Nova" / "journal.jsonl").events)
    assert indexed.indexing == "journaled"
    assert [(i.event.type, i.cycle, i.background) for i in indexed.events] == [
        ("perception", 1, False),
        ("thought", 1, False),
        ("cycle", 1, False),
        ("memory", 1, False),
        ("consolidation", 1, True),
        ("perception", 2, False),
        ("thought", 2, False),
        ("cycle", 2, False),
        ("utterance", 2, False),
        ("reflection", 2, False),
        ("identity_shift", 2, False),
        ("memory", 2, False),
        ("thought", 4, False),
        ("cycle", 4, False),
        ("custom_probe", 4, False),
        ("utterance", 4, False),
        ("memory", 4, False),
    ]
    assert indexed.cycles == (1, 2, 4)


def test_rule_2_counted_indexing() -> None:
    events = _events(
        _line("thought", "1"),
        _line("reflection", "r1"),
        _line("perception", "p2"),
        _line("thought", "2"),
        _line("thought", "3"),
        _line("reflection", "r3"),
        _line("custom", "c3"),
    )
    indexed = index_events(events)
    assert indexed.indexing == "counted"
    assert _cycle_map(events) == [
        ("thought", 1, False),
        ("reflection", 1, False),
        ("perception", 2, False),
        ("thought", 2, False),
        ("thought", 3, False),
        ("reflection", 3, False),
        ("custom", 3, False),
    ]


def test_counted_mode_cannot_see_failed_cycles() -> None:
    # Same thoughts as a journal with a failed cycle 2: counting yields 1, 2 (undercount).
    events = _events(_line("thought", "a"), _line("thought", "b"))
    assert [i.cycle for i in index_events(events).events] == [1, 2]


def test_journaled_mode_numbers_groups_without_cycle_events_after_the_previous_one() -> None:
    events = _events(
        _line("thought", "pre-196 a"),
        _line("thought", "pre-196 b"),
        _line("thought", "c"),
        _line("cycle", "c", thought_count=10),
        _line("thought", "d (cycle trace missing)"),
        _line("thought", "e"),
        _line("cycle", "e", thought_count=13),
    )
    assert [i.cycle for i in index_events(events).events if i.event.type == "thought"] == [
        1,
        2,
        10,
        11,
        13,
    ]


def test_cycle_trace_whose_thought_line_was_lost_still_bounds_its_cycle() -> None:
    # The thought of cycle 2 was corrupt and skipped: its trace must not merge into cycle 1.
    events = _events(
        _line("thought", "1"),
        _line("cycle", "c1", thought_count=1),
        _line("cycle", "c2", thought_count=2),
        _line("memory", "m2", long_term_count=1),
        _line("consolidation", "after a thought-less cycle"),
    )
    assert _cycle_map(events) == [
        ("thought", 1, False),
        ("cycle", 1, False),
        ("cycle", 2, False),
        ("memory", 2, False),
        ("consolidation", 2, True),
    ]


def test_thought_after_a_thought_less_cycle_starts_a_new_cycle() -> None:
    events = _events(
        _line("cycle", "c1", thought_count=1),
        _line("thought", "2"),
        _line("cycle", "c2", thought_count=2),
    )
    assert _cycle_map(events) == [("cycle", 1, False), ("thought", 2, False), ("cycle", 2, False)]


@pytest.mark.parametrize("second", [5, 3])
def test_journaled_thought_count_that_does_not_advance_is_kept_with_a_warning(
    second: int, caplog: pytest.LogCaptureFixture
) -> None:
    events = _events(
        _line("thought", "a"),
        _line("cycle", "a", thought_count=5),
        _line("thought", "b"),
        _line("cycle", "b", thought_count=second),
    )
    with caplog.at_level(logging.WARNING, logger="acsf_usd.reader.cycles"):
        indexed = index_events(events)
    # The journaled number stays authoritative even when it repeats or goes back.
    assert [i.cycle for i in indexed.events] == [5, 5, second, second]
    assert [r.getMessage() for r in caplog.records] == [
        f"cycle thought_count {second} does not advance past cycle 5"
    ]


def test_first_journaled_cycle_is_never_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    events = _events(_line("thought", "a"), _line("cycle", "a", thought_count=0))
    with caplog.at_level(logging.WARNING, logger="acsf_usd.reader.cycles"):
        assert [i.cycle for i in index_events(events).events] == [0, 0]
    assert caplog.records == []


def test_streamed_and_in_memory_parsing_agree() -> None:
    path = INSTANCES / "Nova" / "journal.jsonl"
    assert read_journal(path).events == parse_journal_bytes(path.read_bytes()).events
    assert (
        read_journal(path).warnings
        == parse_journal_bytes(path.read_bytes(), label=str(path)).warnings
    )


def test_background_consolidation_attaches_to_latest_cycle_or_first() -> None:
    events = _events(
        _line("consolidation", "early"),
        _line("perception", "p1"),
        _line("thought", "1"),
        _line("perception", "p2"),
        _line("consolidation", "between perception and thought"),
        _line("thought", "2"),
    )
    assert _cycle_map(events) == [
        ("consolidation", 1, True),
        ("perception", 1, False),
        ("thought", 1, False),
        ("perception", 2, False),
        ("consolidation", 1, True),
        ("thought", 2, False),
    ]


def test_only_background_events_use_cycle_zero() -> None:
    events = _events(_line("consolidation", "x"))
    assert _cycle_map(events) == [("consolidation", 0, True)]
    assert index_events(()).events == ()


def test_indexing_is_deterministic() -> None:
    events = read_journal(INSTANCES / "Nova" / "journal.jsonl").events
    assert index_events(events) == index_events(events)


# --- reader.state ---------------------------------------------------------------------


def test_state_snapshot_fields() -> None:
    snapshot, warning = read_state(INSTANCES / "Nova" / "state.json")
    assert warning is None and snapshot is not None
    assert snapshot.name == "Nova"
    assert snapshot.values == ("curiosity", "care")
    assert snapshot.amendments == ("I am someone who answers when spoken to",)
    assert snapshot.mood == {"curiosity": 0.75, "wonder": 0.5}
    assert snapshot.initial_mood == {"curiosity": 0.7, "wonder": 0.6}
    assert (snapshot.attention_focus, snapshot.attention_theme) == ("thought", "tides")
    assert snapshot.attention_salience == 0.8
    assert [i.seq for i in snapshot.short_term] == [7, 3]
    assert snapshot.short_term[1].importance == 2.0
    assert snapshot.thought_count == 4


def test_state_without_seq_keeps_items() -> None:
    snapshot, _ = read_state(INSTANCES / "Ash" / "state.json")
    assert snapshot is not None
    assert [i.seq for i in snapshot.short_term] == [None]


@pytest.mark.parametrize("content", [b"", b"{corrupt", b"[1, 2]", b"\xff"])
def test_bad_state_yields_no_snapshot(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "state.json"
    path.write_bytes(content)
    snapshot, warning = read_state(path)
    assert snapshot is None and warning is not None


def test_missing_state_yields_no_snapshot(tmp_path: Path) -> None:
    snapshot, warning = read_state(tmp_path / "state.json")
    assert snapshot is None and warning is not None and "not found" in warning


# --- input resolution -----------------------------------------------------------------


def test_resolve_instance_dir_by_path() -> None:
    assert resolve_instance_dir(str(INSTANCES / "Nova")) == INSTANCES / "Nova"


def test_resolve_instance_name_under_consciousness_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONSCIOUSNESS_HOME", str(INSTANCES))
    assert resolve_instance_dir("Ash") == INSTANCES / "Ash"
    assert resolve_instance_dir("Ash", home=INSTANCES) == INSTANCES / "Ash"


@pytest.mark.parametrize("arg", ["NoSuchInstance", "/no/such/dir"])
def test_resolve_errors(arg: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONSCIOUSNESS_HOME", str(INSTANCES))
    with pytest.raises(InputError):
        resolve_instance_dir(arg)


def test_resolve_directory_without_artifacts(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="neither"):
        resolve_instance_dir(str(tmp_path))


def test_resolve_existing_file_is_not_a_directory(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_bytes(b"")
    with pytest.raises(InputError, match=": not a directory$"):
        resolve_instance_dir(str(path))


def test_existing_directory_wins_over_a_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shutil.copytree(INSTANCES / "Ash", tmp_path / "Nova")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONSCIOUSNESS_HOME", str(INSTANCES))
    assert resolve_instance_dir("Nova") == Path("Nova")


def test_a_path_with_a_separator_is_never_looked_up_by_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    shutil.copytree(INSTANCES / "Ash", home / "sub" / "Ash")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(InputError, match="no such directory"):
        resolve_instance_dir(os.path.join("sub", "Ash"), home=home)


def test_a_name_under_home_without_artifacts_points_at_consciousness_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "home" / "Empty").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(InputError, match="set CONSCIOUSNESS_HOME"):
        resolve_instance_dir("Empty", home=tmp_path / "home")


def test_consciousness_home_default_and_tilde_expansion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CONSCIOUSNESS_HOME", raising=False)
    assert consciousness_home() == tmp_path / ".consciousness"
    monkeypatch.setenv("CONSCIOUSNESS_HOME", "~/elsewhere")
    assert consciousness_home() == tmp_path / "elsewhere"


def test_collection_skips_files_and_non_instance_children(tmp_path: Path) -> None:
    shutil.copytree(INSTANCES / "Ash", tmp_path / "run" / "replicate-1")
    (tmp_path / "run" / "logs").mkdir()
    (tmp_path / "run" / "journal.txt").write_text("not an instance\n")
    directories, collection = resolve_inputs([str(tmp_path / "run")])
    assert collection and directories == [tmp_path / "run" / "replicate-1"]


def test_resolve_inputs_dedupes_across_a_collection_and_a_path() -> None:
    directories, collection = resolve_inputs([str(INSTANCES / "Nova"), str(INSTANCES)])
    assert collection
    assert directories == [INSTANCES / "Nova", INSTANCES / "Ash", INSTANCES / "Lumen"]


def test_read_instance_name_override_and_missing_state_warning(
    instance_copy: Callable[[str], Path],
) -> None:
    directory = instance_copy("Ash")
    (directory / "state.json").unlink()
    run = read_instance(directory, name="Renamed")
    assert run.name == "Renamed" and run.source_dir == directory
    assert run.snapshot is None
    assert "state.json: not found; exporting without a snapshot" in run.warnings


def test_read_instance_builds_run_model() -> None:
    run = read_instance(INSTANCES / "Nova")
    assert run.name == "Nova"
    assert run.journal.indexing == "journaled"
    assert run.snapshot is not None
    assert len(run.warnings) == 2


def test_read_instance_opens_inputs_read_only(
    monkeypatch: pytest.MonkeyPatch, instance_copy: Callable[[str], Path]
) -> None:
    directory = instance_copy("Nova")
    modes: list[str] = []
    original_open = Path.open

    def recording_open(self: Path, mode: str = "r", *args: object, **kwargs: object) -> object:
        modes.append(mode)
        return original_open(self, mode, *args, **kwargs)  # type: ignore[call-overload]

    before = sorted((p.name, p.stat().st_mtime_ns, p.read_bytes()) for p in directory.iterdir())
    monkeypatch.setattr(Path, "open", recording_open)
    read_instance(directory)
    monkeypatch.undo()
    after = sorted((p.name, p.stat().st_mtime_ns, p.read_bytes()) for p in directory.iterdir())
    assert modes == ["rb", "rb"]
    assert before == after
