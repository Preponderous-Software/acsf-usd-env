"""Live mode: keep one instance's stage current while the instance runs.

``journal.jsonl`` is polled like ACSF's own dashboard tailer does
(``interfaces/web/journal_tail.py`` at 64486d9, L107–L164): its size is compared with an
in-memory byte offset, new bytes are read from a handle opened ``"rb"``, only complete lines
are consumed (a partial trailing line waits for the next poll), and a file that shrank is
re-read from offset 0. New events go through the same indexer as a batch export; the instance
layer is re-authored from the run model and written to a temporary file next to the output,
then moved over it with ``os.replace``, so a USD tool never sees a half-written file.

Strictly read-only against the instance directory: files there are only opened for reading;
no lock, sidecar, offset or temporary file is created in it. On SIGINT/SIGTERM a final write
is made — consuming a trailing line without a newline exactly as a batch read would — so the
final output is byte-identical to a batch export of the same ``journal.jsonl`` and
``state.json``.
"""

from __future__ import annotations

import logging
import os
import signal
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType

from acsf_usd.model import Event, InstanceRun, Snapshot
from acsf_usd.reader.cycles import index_events
from acsf_usd.reader.journal import parse_journal_bytes
from acsf_usd.reader.resolve import JOURNAL, STATE
from acsf_usd.reader.state import parse_state_bytes
from acsf_usd.stage.build import ExportOptions, build_instance_stage, write_stage

log = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 0.5


@dataclass
class JournalFollower:
    """Incremental, read-only reader of one ``journal.jsonl``."""

    path: Path
    offset: int = 0
    pending: bytes = b""
    lines: int = 0
    events: list[Event] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.offset, self.pending, self.lines = 0, b"", 0
        self.events.clear()
        self.warnings.clear()

    def poll(self, final: bool = False) -> bool:
        """Consume new complete lines; with ``final``, also a trailing partial line.

        Returns whether the parsed journal changed.
        """
        try:
            size = os.stat(self.path).st_size
        except FileNotFoundError:
            size = 0
        changed = False
        if size < self.offset:
            log.warning("%s shrank; re-reading it from the start", self.path)
            self.reset()
            changed = True
        data = b""
        if size > self.offset:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                data = handle.read(size - self.offset)
            self.offset += len(data)
        buffer = self.pending + data
        cut = buffer.rfind(b"\n") + 1
        complete, self.pending = buffer[:cut], buffer[cut:]
        if final and self.pending:
            complete, self.pending = complete + self.pending, b""
        if complete:
            parsed = parse_journal_bytes(complete, self.lines + 1, str(self.path))
            self.lines += complete.count(b"\n") + (0 if complete.endswith(b"\n") else 1)
            self.events.extend(parsed.events)
            self.warnings.extend(parsed.warnings)
            changed = True
        return changed


@dataclass
class StateFollower:
    """Re-reads ``state.json``; keeps the previous snapshot while it is missing or invalid."""

    path: Path
    raw: bytes | None = None
    snapshot: Snapshot | None = None

    def poll(self) -> bool:
        try:
            with self.path.open("rb") as handle:
                raw = handle.read()
        except FileNotFoundError:
            return False
        if raw == self.raw:
            return False
        snapshot, warning = parse_state_bytes(raw, str(self.path))
        if snapshot is None:
            log.warning("%s; keeping the previous snapshot", warning)
            return False
        self.raw, self.snapshot = raw, snapshot
        return True


def write_atomically(run: InstanceRun, output: Path, options: ExportOptions, binary: bool) -> None:
    """Author the instance layer to a temporary file next to ``output``, then replace it."""
    suffix = ".usdc" if binary else ".usda"
    fd, tmp_name = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.", suffix=suffix)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        write_stage(build_instance_stage(run, options), tmp, binary=binary)
        os.replace(tmp, output)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def follow(
    directory: Path,
    output: Path,
    options: ExportOptions | None = None,
    binary: bool = False,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    stop: threading.Event | None = None,
    on_write: Callable[[InstanceRun], None] | None = None,
) -> InstanceRun:
    """Follow one instance until ``stop`` is set (or SIGINT/SIGTERM when run in the main
    thread); return the run model of the final write."""
    options = options or ExportOptions()
    stop = stop or threading.Event()
    journal = JournalFollower(directory / JOURNAL)
    state = StateFollower(directory / STATE)
    name = directory.resolve().name
    restore = _install_signal_handlers(stop)
    try:
        first = True
        while True:
            stopping = stop.is_set()
            changed = journal.poll(final=stopping)
            changed = state.poll() or changed
            if changed or first or stopping:
                run = InstanceRun(
                    name=name,
                    source_dir=directory,
                    journal=index_events(journal.events),
                    snapshot=state.snapshot,
                    warnings=tuple(journal.warnings),
                )
                write_atomically(run, output, options, binary)
                if on_write is not None:
                    on_write(run)
                first = False
            if stopping:
                return run
            stop.wait(poll_interval)
    finally:
        restore()


def _install_signal_handlers(stop: threading.Event) -> Callable[[], None]:
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    def handler(signum: int, frame: FrameType | None) -> None:
        stop.set()

    previous = {sig: signal.signal(sig, handler) for sig in (signal.SIGINT, signal.SIGTERM)}

    def restore() -> None:
        for sig, old in previous.items():
            signal.signal(sig, old)

    return restore
