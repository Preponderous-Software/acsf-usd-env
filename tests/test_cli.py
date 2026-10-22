"""`acsf-usd` command-line branches the export and end-to-end tests do not reach.

Pins the help printed with no command, `describe` on an instance with only a snapshot or only
a journal, warnings going to this invocation's stderr (and the handler being removed again),
and `describe` on a missing input.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from acsf_usd.cli import main
from tests.conftest import INSTANCES


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: acsf-usd")
    for command in ("export", "validate", "describe"):
        assert command in out


def test_describe_snapshot_only_instance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "Solo"
    directory.mkdir()
    shutil.copy(INSTANCES / "Nova" / "state.json", directory / "state.json")
    assert main(["describe", str(directory)]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        f"{directory}: Solo: 0 events, no cycles, counted indexing, 0 warnings",
        "  events: ",
        "  mood: final value only",
        "  snapshot: state.json",
    ]
    assert captured.err == ""


def test_describe_journal_only_instance_warns_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "Nova"
    directory.mkdir()
    shutil.copy(INSTANCES / "Nova" / "journal.jsonl", directory / "journal.jsonl")
    assert main(["describe", str(directory)]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert "  mood: final value only" in lines
    assert "  snapshot: none" in lines
    assert "acsf-usd: warning: state.json: not found; exporting without a snapshot" in captured.err
    assert "skipping corrupted line" in captured.err
    assert list(directory.iterdir()) == [directory / "journal.jsonl"]


def test_warning_handler_is_scoped_to_one_invocation(capsys: pytest.CaptureFixture[str]) -> None:
    logger = logging.getLogger("acsf_usd")
    handlers = list(logger.handlers)
    assert main(["describe", str(INSTANCES / "Nova")]) == 0
    assert "acsf-usd: warning: " in capsys.readouterr().err
    assert logger.handlers == handlers
    assert logger.propagate is True


def test_describe_missing_input_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["describe", str(tmp_path / "absent")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "acsf-usd: error: " in captured.err
    assert "no such directory" in captured.err
