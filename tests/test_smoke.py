"""Milestone 1 smoke tests: the USD bindings import and the CLI reports its version."""

from __future__ import annotations

import subprocess
import sys

import pytest
from pxr import Usd

import acsf_usd
from acsf_usd.cli import main


def test_usd_core_version_is_pinned() -> None:
    version = Usd.GetVersion()
    print(f"Usd.GetVersion() = {version}")
    assert tuple(version) == (0, 26, 8)


def test_cli_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"acsf-usd {acsf_usd.__version__}"


def test_module_entry_point_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "acsf_usd", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == f"acsf-usd {acsf_usd.__version__}"
