"""Resolve ``acsf-usd`` input arguments to ACSF instance directories and read them.

An input is an instance directory, an experiment run directory or a golden-run directory
(each holds ``journal.jsonl`` and/or ``state.json``), or the name of an instance under
``CONSCIOUSNESS_HOME`` (default ``~/.consciousness``), resolved as ACSF's experiment runner
resolves ``resume_from`` (``experiments/runner.py`` at 64486d9): an existing directory wins
over a name. A directory holding neither file but containing instance directories — a whole
``CONSCIOUSNESS_HOME``, or an experiment run with ``replicate-<i>/`` subdirectories — is a
*collection* and stands for all of them. Nothing is ever written to a resolved directory.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from acsf_usd.model import InstanceRun
from acsf_usd.reader.cycles import index_events
from acsf_usd.reader.journal import read_journal
from acsf_usd.reader.state import read_state

log = logging.getLogger(__name__)

JOURNAL = "journal.jsonl"
STATE = "state.json"


class InputError(Exception):
    """The input argument does not name a readable ACSF instance."""


def consciousness_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("CONSCIOUSNESS_HOME", "~/.consciousness")))


def is_instance_dir(path: Path) -> bool:
    return path.is_dir() and ((path / JOURNAL).is_file() or (path / STATE).is_file())


def resolve_instance_dir(arg: str, home: Path | None = None) -> Path:
    candidate = Path(arg)
    if candidate.is_dir():
        if is_instance_dir(candidate):
            return candidate
        raise InputError(f"{arg}: directory has neither {JOURNAL} nor {STATE}")
    if candidate.exists():
        raise InputError(f"{arg}: not a directory")
    if os.sep in arg or (os.altsep is not None and os.altsep in arg):
        raise InputError(f"{arg}: no such directory")
    root = home if home is not None else consciousness_home()
    named = root / arg
    if is_instance_dir(named):
        return named
    raise InputError(
        f"{arg}: not a directory, and no instance named {arg!r} under {root} "
        f"(set CONSCIOUSNESS_HOME to change where instances are looked up)"
    )


def instance_children(path: Path) -> list[Path]:
    """Instance directories directly inside ``path``, sorted by name."""
    return sorted((child for child in path.iterdir() if is_instance_dir(child)), key=str)


def resolve_inputs(args: list[str], home: Path | None = None) -> tuple[list[Path], bool]:
    """Resolve every argument; return the instance directories and whether any argument was a
    collection (so the export is multi-instance even when it yields one instance)."""
    directories: list[Path] = []
    collection = False
    for arg in args:
        candidate = Path(arg)
        if candidate.is_dir() and not is_instance_dir(candidate):
            children = instance_children(candidate)
            if not children:
                raise InputError(
                    f"{arg}: directory has neither {JOURNAL} nor {STATE}, "
                    "and no instance directories inside it"
                )
            directories.extend(children)
            collection = True
        else:
            directories.append(resolve_instance_dir(arg, home))
    seen: set[Path] = set()
    unique: list[Path] = []
    for directory in directories:
        key = directory.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(directory)
    return unique, collection


def read_instance(directory: Path, name: str | None = None) -> InstanceRun:
    """Read one instance directory into the run model (read-only)."""
    parsed = read_journal(directory / JOURNAL)
    snapshot, state_warning = read_state(directory / STATE)
    warnings = list(parsed.warnings)
    if state_warning is not None:
        log.warning("%s", state_warning)
        warnings.append(state_warning)
    return InstanceRun(
        name=name if name is not None else directory.resolve().name,
        source_dir=directory,
        journal=index_events(parsed.events),
        snapshot=snapshot,
        warnings=tuple(warnings),
    )
