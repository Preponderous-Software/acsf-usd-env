"""Milestone 9: live mode (``acsf-usd export --follow``) — MVP acceptance criterion 11."""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pxr import Sdf, Usd

from acsf_usd import follow as follow_module
from acsf_usd.cli import main
from acsf_usd.follow import JournalFollower, StateFollower, follow, write_atomically
from acsf_usd.reader import read_instance, read_journal
from acsf_usd.stage.build import ExportOptions
from tests.conftest import INSTANCES

POLL = 0.05


def _line(event_type: str, content: str) -> bytes:
    return json.dumps({"timestamp": "t", "type": event_type, "content": content}).encode() + b"\n"


def _wait(condition: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def _thoughts(output: Path, name: str) -> list[str]:
    """Non-empty thought samples, read from a fresh anonymous copy (no layer cache)."""
    if not output.exists():
        return []
    layer = Sdf.Layer.OpenAsAnonymous(str(output))
    stage = Usd.Stage.Open(layer)
    attr = stage.GetPrimAtPath(f"/World/Instances/{name}/Stream").GetAttribute("acsf:thought")
    if not attr.IsValid():
        return []
    return [v for v in (attr.Get(t) for t in attr.GetTimeSamples()) if v]


class Writer:
    """Appends to an instance the way ACSF does, and remembers exactly what it wrote."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True)
        self.files: dict[str, bytes] = {"journal.jsonl": b""}
        (directory / "journal.jsonl").write_bytes(b"")

    def append(self, data: bytes) -> None:
        with (self.directory / "journal.jsonl").open("ab") as handle:
            handle.write(data)
            handle.flush()
        self.files["journal.jsonl"] += data

    def state(self, data: bytes) -> None:
        (self.directory / "state.json").write_bytes(data)
        self.files["state.json"] = data

    def assert_untouched(self) -> None:
        present = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        assert present == self.files


@pytest.mark.parametrize(
    ("binary", "stop_signal"), [(False, signal.SIGINT), (True, signal.SIGTERM)]
)
def test_follow_tracks_complete_lines_and_ends_byte_identical_to_batch(
    tmp_path: Path, binary: bool, stop_signal: signal.Signals
) -> None:
    name = "Kite"
    writer = Writer(tmp_path / "home" / name)
    writer.append(_line("thought", "one") + _line("reflection", "r1"))
    writer.state(json.dumps({"identity": {"name": name, "mood": {"calm": 0.5}}}).encode())
    ext = "usdc" if binary else "usda"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    output = out_dir / f"kite.{ext}"
    cmd = [sys.executable, "-m", "acsf_usd", "export", str(writer.directory), "-o", str(output)]
    cmd += ["--follow", "--poll-interval", str(POLL)] + (["--binary"] if binary else [])
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait(lambda: _thoughts(output, name) == ["one"])

        writer.append(_line("perception", "[mock: Kite] A kite flies.") + _line("thought", "two"))
        _wait(lambda: _thoughts(output, name) == ["one", "two"])

        # One line flushed in two writes: nothing appears until the line is complete.
        third = _line("thought", "three")
        writer.append(third[:20])
        time.sleep(POLL * 8)
        assert _thoughts(output, name) == ["one", "two"]
        writer.append(third[20:])
        _wait(lambda: _thoughts(output, name) == ["one", "two", "three"])

        # ACSF overwrites state.json every cycle; a moment where it is unparseable is survived.
        writer.state(b"{not json")
        time.sleep(POLL * 4)
        writer.state(json.dumps({"identity": {"name": name, "mood": {"calm": 0.7}}}).encode())
        # A last line without a trailing newline, consumed on the final write as batch would.
        writer.append(_line("thought", "four").rstrip(b"\n"))
        time.sleep(POLL * 4)
        assert _thoughts(output, name) == ["one", "two", "three"]

        proc.send_signal(stop_signal)
        stdout, stderr = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, stderr
    assert "final write" in stdout
    assert _thoughts(output, name) == ["one", "two", "three", "four"]

    # Byte-identical to a batch export of the same journal.jsonl and state.json.
    batch = tmp_path / f"batch.{ext}"
    assert (
        main(["export", str(writer.directory), "-o", str(batch)] + (["--binary"] if binary else []))
        == 0
    )
    assert output.read_bytes() == batch.read_bytes()
    assert main(["validate", str(output), str(writer.directory)]) == 0
    # The instance directory holds exactly what the test wrote; no temporary files remain.
    writer.assert_untouched()
    assert [p.name for p in out_dir.iterdir()] == [output.name]


def test_follow_in_process_stops_on_event(tmp_path: Path) -> None:
    directory = tmp_path / "Nova"
    shutil.copytree(INSTANCES / "Nova", directory)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    output = tmp_path / "nova.usda"
    stop = threading.Event()
    writes: list[int] = []
    thread = threading.Thread(
        target=follow,
        args=(directory, output),
        kwargs={"poll_interval": POLL, "stop": stop, "on_write": lambda r: writes.append(1)},
    )
    thread.start()
    _wait(lambda: output.exists())
    time.sleep(POLL * 5)
    stop.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    # One initial write, nothing changed while idle, one final write.
    assert len(writes) == 2
    batch = tmp_path / "batch.usda"
    assert main(["export", str(directory), "-o", str(batch)]) == 0
    assert output.read_bytes() == batch.read_bytes()
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


# --- JournalFollower / StateFollower --------------------------------------------------------


def test_journal_follower_consumes_complete_lines_only(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_bytes(_line("thought", "a") + b'{"type": "thou')
    follower = JournalFollower(path)
    assert follower.poll() is True
    assert [e.content for e in follower.events] == ["a"]
    assert follower.poll() is False
    with path.open("ab") as handle:
        handle.write(b'ght", "content": "b"}\n')
    assert follower.poll() is True
    assert [e.content for e in follower.events] == ["a", "b"]
    assert follower.events == list(read_journal(path).events)


def test_journal_follower_final_poll_matches_batch_trailing_line(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_bytes(_line("thought", "a") + _line("thought", "b").rstrip(b"\n"))
    follower = JournalFollower(path)
    follower.poll()
    assert len(follower.events) == 1
    follower.poll(final=True)
    assert follower.events == list(read_journal(path).events)


def test_journal_follower_rereads_a_shrunken_file(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_bytes(_line("thought", "a") + _line("thought", "b"))
    follower = JournalFollower(path)
    follower.poll()
    path.write_bytes(_line("thought", "c"))
    assert follower.poll() is True
    assert [e.content for e in follower.events] == ["c"]


def test_journal_follower_missing_file_is_empty(tmp_path: Path) -> None:
    follower = JournalFollower(tmp_path / "journal.jsonl")
    assert follower.poll() is False and follower.events == []


def test_journal_follower_counts_lines_like_batch(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_bytes(_line("thought", "a") + b"\n" + b"{bad\n")
    follower = JournalFollower(path)
    follower.poll()
    with path.open("ab") as handle:
        handle.write(b"{also bad\n")
    follower.poll()
    assert follower.warnings == list(read_journal(path).warnings)


def test_state_follower_keeps_previous_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    follower = StateFollower(path)
    assert follower.poll() is False and follower.snapshot is None
    path.write_text(json.dumps({"identity": {"name": "S"}}))
    assert follower.poll() is True and follower.snapshot is not None
    good = follower.snapshot
    path.write_text("{truncated")
    assert follower.poll() is False and follower.snapshot is good
    path.unlink()
    assert follower.poll() is False and follower.snapshot is good


def test_write_atomically_leaves_no_temporary_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = read_instance(INSTANCES / "Ash")
    output = tmp_path / "ash.usda"
    output.write_text("previous")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(follow_module, "write_stage", boom)
    with pytest.raises(OSError):
        write_atomically(run, output, ExportOptions(), binary=False)
    assert [p.name for p in tmp_path.iterdir()] == ["ash.usda"]
    assert output.read_text() == "previous"


# --- CLI usage ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "inputs", [[str(INSTANCES / "Nova"), str(INSTANCES / "Ash")], [str(INSTANCES)]]
)
def test_follow_takes_exactly_one_instance(inputs: list[str], tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["export", *inputs, "-o", str(tmp_path / "x.usda"), "--follow"])
    assert excinfo.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_follow_refuses_output_inside_the_instance(tmp_path: Path) -> None:
    directory = tmp_path / "Nova"
    shutil.copytree(INSTANCES / "Nova", directory)
    with pytest.raises(SystemExit) as excinfo:
        main(["export", str(directory), "-o", str(directory / "x.usda"), "--follow"])
    assert excinfo.value.code == 2
    assert sorted(p.name for p in directory.iterdir()) == ["journal.jsonl", "state.json"]


@pytest.mark.parametrize("value", ["0", "-1", "inf", "nan"])
def test_poll_interval_must_be_positive_and_finite(value: str, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["export", str(INSTANCES / "Nova"), "--follow", "--poll-interval", value])
    assert excinfo.value.code == 2
