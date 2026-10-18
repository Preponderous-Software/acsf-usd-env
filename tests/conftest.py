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
