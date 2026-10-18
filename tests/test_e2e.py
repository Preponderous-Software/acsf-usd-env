"""Milestone 7: validate command and golden-run end-to-end tests.

Covers MVP acceptance criteria 2 (golden Echo counts), 3 (byte-for-byte fidelity, checked by
`acsf-usd validate`), 5 (corrupt lines never abort), 6 (input untouched), 7 (determinism).
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from pxr import Usd

from acsf_usd.cli import main
from acsf_usd.reader import read_instance
from tests.conftest import GOLDEN, INSTANCES

# Event counts in ACSF's committed golden journals at 64486d9 (tests/fixtures/golden/README.md).
GOLDEN_COUNTS = {
    "Echo": {"thought": 200, "perception": 67, "reflection": 34},
    "Rafael": {"thought": 83, "perception": 0, "reflection": 13},
    "Sage": {"thought": 76, "perception": 25, "reflection": 9},
    "Wren": {"thought": 200, "perception": 66, "reflection": 23},
}


def _snapshot(root: Path) -> dict[str, tuple[str, int, int]]:
    """sha256, size and mtime (ns) of every file under ``root``."""
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _non_empty(stage: Usd.Stage, path: str, name: str) -> int:
    attr = stage.GetPrimAtPath(path).GetAttribute(name)
    return sum(1 for t in attr.GetTimeSamples() if attr.Get(t))


@pytest.mark.parametrize("name", sorted(GOLDEN_COUNTS))
def test_golden_run_exports_and_validates(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / f"{name}.usda"
    assert main(["export", str(GOLDEN / name), "-o", str(out)]) == 0
    stage = Usd.Stage.Open(str(out))
    assert stage is not None
    counts = GOLDEN_COUNTS[name]
    prim = f"/World/Instances/{name}"
    thought = stage.GetPrimAtPath(f"{prim}/Stream").GetAttribute("acsf:thought")
    assert len(thought.GetTimeSamples()) == counts["thought"]
    assert _non_empty(stage, f"{prim}/Stream", "acsf:thought") == counts["thought"]
    assert _non_empty(stage, f"{prim}/Stimulus", "acsf:perception") == counts["perception"]
    assert _non_empty(stage, f"{prim}/Stream", "acsf:reflection") == counts["reflection"]
    assert stage.GetRootLayer().customLayerData["acsf:cycleIndexing"] == "counted"
    capsys.readouterr()
    assert main(["validate", str(out), str(GOLDEN / name)]) == 0
    assert "validate: OK" in capsys.readouterr().out


def test_validate_reads_binary_and_composed_stages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "golden.usdc"
    assert main(["export", str(GOLDEN), "-o", str(root), "--binary"]) == 0
    assert main(["validate", str(root), str(GOLDEN)]) == 0
    assert "validate: OK — 4 instance(s)" in capsys.readouterr().out
    assert main(["validate", str(tmp_path / "Wren.usdc"), str(GOLDEN / "Wren")]) == 0


def test_validate_checks_utterances_and_journaled_indexing(tmp_path: Path) -> None:
    out = tmp_path / "nova.usda"
    assert main(["export", str(INSTANCES / "Nova"), "-o", str(out)]) == 0
    assert main(["validate", str(out), str(INSTANCES / "Nova")]) == 0


def test_validate_fails_on_an_altered_stage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "echo.usda"
    assert main(["export", str(GOLDEN / "Echo"), "-o", str(out)]) == 0
    original = read_instance(GOLDEN / "Echo").journal.events
    target = next(i for i in original if i.event.type == "thought" and i.cycle == 7)
    text = out.read_text(encoding="utf-8")
    needle = target.event.content[:40]
    assert needle in text
    out.write_text(text.replace(needle, "TAMPERED " + needle[9:], 1), encoding="utf-8")
    capsys.readouterr()
    assert main(["validate", str(out), str(GOLDEN / "Echo")]) == 1
    report = capsys.readouterr().out
    assert "Stream.acsf:thought cycle 7" in report
    assert "validate: FAIL" in report


def test_validate_fails_when_validated_against_the_wrong_journal(tmp_path: Path) -> None:
    out = tmp_path / "wren.usda"
    assert main(["export", str(GOLDEN / "Wren"), "-o", str(out)]) == 0
    impostor = tmp_path / "Wren"
    shutil.copytree(GOLDEN / "Echo", impostor)
    assert main(["validate", str(out), str(impostor)]) == 1


def test_validate_fails_on_wrong_cycle_indexing(tmp_path: Path) -> None:
    out = tmp_path / "nova.usda"
    assert main(["export", str(INSTANCES / "Nova"), "-o", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    out.write_text(
        text.replace('"acsf:cycleIndexing" = "journaled"', '"acsf:cycleIndexing" = "counted"'),
        encoding="utf-8",
    )
    assert main(["validate", str(out), str(INSTANCES / "Nova")]) == 1


def test_corrupt_lines_never_abort_the_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "nova.usda"
    assert main(["export", str(INSTANCES / "Nova"), "-o", str(out)]) == 0
    captured = capsys.readouterr()
    assert "skipping corrupted line" in captured.err
    assert "skipping non-object line" in captured.err
    assert "2 warnings" in captured.out


@pytest.mark.parametrize("binary", [False, True])
def test_input_directory_is_untouched(binary: bool, tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    shutil.copytree(GOLDEN, inputs / "golden")
    shutil.copytree(INSTANCES, inputs / "instances")
    before = _snapshot(inputs)
    flag = ["--binary"] if binary else []
    ext = "usdc" if binary else "usda"
    for name in GOLDEN_COUNTS:
        out = tmp_path / "out" / f"{name}.{ext}"
        out.parent.mkdir(exist_ok=True)
        assert main(["export", str(inputs / "golden" / name), "-o", str(out), *flag]) == 0
    assert main(["export", str(inputs / "instances"), "-o", str(tmp_path / f"r.{ext}"), *flag]) == 0
    assert main(["validate", str(tmp_path / f"r.{ext}"), str(inputs / "instances")]) == 0
    assert _snapshot(inputs) == before  # acceptance criterion 6


@pytest.mark.parametrize("binary", [False, True])
def test_two_exports_are_byte_identical(binary: bool, tmp_path: Path) -> None:
    flag = ["--binary"] if binary else []
    ext = "usdc" if binary else "usda"
    for name in [*GOLDEN_COUNTS, "Nova"]:
        source = GOLDEN / name if name in GOLDEN_COUNTS else INSTANCES / name
        a, b = tmp_path / f"a.{ext}", tmp_path / f"b.{ext}"
        assert main(["export", str(source), "-o", str(a), *flag]) == 0
        assert main(["export", str(source), "-o", str(b), *flag]) == 0
        assert a.read_bytes() == b.read_bytes(), name  # acceptance criterion 7
    for run in ("1", "2"):
        assert main(["export", str(GOLDEN), "-o", str(tmp_path / run / f"run.{ext}"), *flag]) == 0
    for path in (tmp_path / "1").iterdir():
        assert path.read_bytes() == (tmp_path / "2" / path.name).read_bytes()


def test_describe_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["describe", str(GOLDEN / "Echo"), str(INSTANCES / "Lumen")]) == 0
    out = capsys.readouterr().out
    assert "Echo: 301 events, cycles 1–200, counted indexing" in out
    assert "200 thought" in out and "67 perception" in out
    assert "mood: journaled per cycle" in out
    assert list(tmp_path.iterdir()) == []
