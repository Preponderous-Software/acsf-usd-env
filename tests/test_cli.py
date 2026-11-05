"""`acsf-usd` command-line branches the export and end-to-end tests do not reach.

Pins the help printed with no command, `describe` on an instance with only a snapshot or only
a journal, warnings going to this invocation's stderr (and the handler being removed again),
`describe` on a missing input, `validate` on a stage that is missing or cannot be opened, and
`export` to a path USD cannot write.
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


def test_validate_missing_stage_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    stage = tmp_path / "absent.usda"
    assert main(["validate", str(stage), str(INSTANCES / "Ash")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"acsf-usd: error: {stage}: no such file" in captured.err


@pytest.mark.parametrize("name", ["bad.usda", "bad.usdc", "bad.txt"])
def test_validate_unreadable_stage_exits_1(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stage = tmp_path / name
    stage.write_text("not usd at all")
    assert main(["validate", str(stage), str(INSTANCES / "Ash")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"acsf-usd: error: {stage}: not a USD stage" in captured.err


@pytest.mark.parametrize("name", ["out.txt", "out.usdz", "out"])
@pytest.mark.parametrize(
    "inputs",
    [[str(INSTANCES / "Nova")], [str(INSTANCES / "Nova"), str(INSTANCES / "Ash")]],
    ids=["one", "several"],
)
def test_export_refuses_an_extension_usd_cannot_write(
    name: str, inputs: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / name
    with pytest.raises(SystemExit) as excinfo:
        main(["export", *inputs, "-o", str(output)])
    assert excinfo.value.code == 2
    assert f"acsf-usd: error: {output}: " in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "inputs",
    [[str(INSTANCES / "Nova")], [str(INSTANCES / "Nova"), str(INSTANCES / "Ash")]],
    ids=["one", "several"],
)
def test_export_to_a_path_that_cannot_be_written_exits_1(
    inputs: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the output's directory would have to be")
    assert main(["export", *inputs, "-o", str(blocker / "out.usda")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"acsf-usd: error: could not write {blocker}" in captured.err
