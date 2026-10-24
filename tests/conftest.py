from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
INSTANCES = FIXTURES / "instances"


@pytest.fixture
def instance_copy(tmp_path: Path) -> Callable[[str], Path]:
    """Return a factory copying a fixture instance into a fresh temporary directory."""

    def _copy(name: str) -> Path:
        target = tmp_path / "home" / name
        shutil.copytree(INSTANCES / name, target)
        return target

    return _copy


GOLDEN = FIXTURES / "golden"


@pytest.fixture(scope="session")
def view_stages(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Stages exported once per session for the viewer tests (``acsf-usd view`` reads stages,
    never instance directories)."""
    from acsf_usd.cli import main

    out = tmp_path_factory.mktemp("view-stages")
    stages = {
        "echo": (["export", str(GOLDEN / "Echo"), "-o", str(out / "echo.usda")], "echo.usda"),
        "golden": (
            ["export", str(GOLDEN), "-o", str(out / "golden" / "run.usda")],
            "golden/run.usda",
        ),
        "pair": (
            [
                "export",
                str(INSTANCES / "Lumen"),
                str(INSTANCES / "Nova"),
                "-o",
                str(out / "pair" / "run.usda"),
            ],
            "pair/run.usda",
        ),
    }
    for args, _ in stages.values():
        assert main(args) == 0
    return {k: out / rel for k, (_, rel) in stages.items()}
