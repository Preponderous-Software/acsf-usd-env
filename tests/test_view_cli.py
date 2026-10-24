"""``acsf-usd view`` / ``fetch-strata`` exit codes when Strata is not usable."""

from __future__ import annotations

from pathlib import Path

import pytest

from acsf_usd.cli import main
from acsf_usd.view import fetch as F


def test_view_without_strata_exits_2_with_one_line(
    view_stages: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(F.ENV_LIB, str(tmp_path / "nope.so"))
    assert main(["view", str(view_stages["echo"]), "--png", str(tmp_path / "x.png")]) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "Strata unavailable" in err[0]


def test_view_with_a_file_that_is_not_a_library(
    view_stages: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = tmp_path / "libstrata.so"
    fake.write_bytes(b"not a shared object")
    monkeypatch.setenv(F.ENV_LIB, str(fake))
    assert main(["view", str(view_stages["echo"])]) == 2
    assert "cannot load" in capsys.readouterr().err


def test_view_refuses_a_non_stage_before_touching_strata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(F.ENV_LIB, str(tmp_path / "nope.so"))
    assert main(["view", str(tmp_path / "missing.usda")]) == 1
    assert "no such file" in capsys.readouterr().err


def test_bad_size(view_stages: dict[str, Path]) -> None:
    with pytest.raises(SystemExit, match="WxH"):
        main(["view", str(view_stages["echo"]), "--size", "big"])
    with pytest.raises(SystemExit, match="between 16 and 8192"):
        main(["view", str(view_stages["echo"]), "--size", "4x4"])


def test_fetch_strata_reports_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*a: object, **k: object) -> Path:
        raise F.FetchError("offline")

    monkeypatch.setattr(F, "fetch", boom)
    assert main(["fetch-strata"]) == 1
    assert "offline" in capsys.readouterr().err


def test_help_lists_the_view_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "view" in out and "fetch-strata" in out


def test_export_does_not_import_the_viewer_runtime() -> None:
    import subprocess
    import sys

    code = (
        "import sys; from acsf_usd.cli import build_parser; build_parser(); "
        "mods = ('acsf_usd.view.strata', 'acsf_usd.view.app', 'ctypes'); "
        "bad = [m for m in mods if m in sys.modules]; "
        "print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
