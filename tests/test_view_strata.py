"""``acsf-usd view`` against the real Strata library, headless.

Skipped unless a Strata library can be resolved (``acsf-usd fetch-strata`` or
``ACSF_USD_STRATA_LIB``) and a GL 4.5 context can be created; CI's ``view`` job runs it on
llvmpipe. The golden images in ``tests/fixtures/view`` are compared with a tolerance, as Strata's
own goldens are, because software rasterisers differ slightly between Mesa versions.

``ACSF_USD_UPDATE_GOLDENS=1`` rewrites the goldens; ``ACSF_USD_VIEW_ARTIFACTS=<dir>`` keeps every
rendered image there (CI uploads them).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from acsf_usd.cli import main
from acsf_usd.view.fetch import FetchError, resolve_library
from acsf_usd.view.model import load_run
from acsf_usd.view.scene import OrbitCamera
from acsf_usd.view.strata import Engine, Library, StrataUnavailable

PIL = pytest.importorskip("PIL.Image")
GOLDENS = Path(__file__).parent / "fixtures" / "view"
CASES = [
    ("echo", None, 1),
    ("echo", None, 116),
    ("echo", None, 200),
    ("golden", "Wren", 60),
]


@pytest.fixture(scope="module")
def lib() -> Library:
    try:
        library = Library(resolve_library())
        Engine(library, 64, 64, headless=True).close()
    except (FetchError, StrataUnavailable) as exc:
        pytest.skip(f"Strata unavailable: {exc}")
    return library


def test_bound_structs_and_abi(lib: Library) -> None:
    assert lib.abi[0] == 1 and lib.abi[1] >= 5
    # the official binding checked every struct the library has against its ctypes mirror
    assert lib.binding.structs_checked > 0


def test_pinned_release_has_the_panel_backing(lib: Library) -> None:
    # strata.lock pins Strata 0.6.0 (ABI 1.12): filled rectangles back the text panel
    if os.environ.get("ACSF_USD_STRATA_LIB"):
        pytest.skip("a local Strata build may predate ABI 1.11")
    assert lib.abi[1] >= 11 and lib.has_rects


def test_camera_matrices_match_strata_look_at(lib: Library) -> None:
    cam = OrbitCamera(target=(1.0, -2.0, -1.4), distance=16.5, yaw=25.0, pitch=44.0)
    out = lib.binding.look_at(cam.eye, cam.target, (0, 1, 0), 34.0, 16 / 9, 0.1, 400.0)
    assert list(out.view) == pytest.approx(cam.view_matrix(), abs=1e-5)
    assert list(out.projection) == pytest.approx(cam.projection_matrix(16 / 9), rel=1e-5, abs=1e-6)


def _compare(actual: Path, golden: Path) -> tuple[float, float]:
    a = PIL.open(actual).convert("RGB")
    g = PIL.open(golden).convert("RGB")
    assert a.size == g.size
    pa, pg = a.tobytes(), g.tobytes()
    n = len(pa) // 3
    differ = sum(
        1 for i in range(0, len(pa), 3) if max(abs(pa[i + k] - pg[i + k]) for k in range(3)) > 48
    )
    lit = sum(1 for i in range(0, len(pa), 3) if max(pa[i : i + 3]) > 40)
    return differ / n, lit / n


@pytest.mark.parametrize(("stage", "instance", "cycle"), CASES)
def test_golden_images(
    lib: Library,
    view_stages: dict[str, Path],
    tmp_path: Path,
    stage: str,
    instance: str | None,
    cycle: int,
) -> None:
    name = f"{stage}-{instance.lower() + '-' if instance else ''}{cycle}.png"
    out = tmp_path / name
    args = ["view", str(view_stages[stage]), "--png", str(out), "--cycle", str(cycle)]
    if instance:
        args += ["--instance", instance]
    assert main(args) == 0
    if os.environ.get("ACSF_USD_VIEW_ARTIFACTS"):
        dest = Path(os.environ["ACSF_USD_VIEW_ARTIFACTS"])
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(out, dest / name)
    golden = GOLDENS / name
    if os.environ.get("ACSF_USD_UPDATE_GOLDENS") == "1":
        shutil.copy(out, golden)
    if lib.has_rects:  # the text panel's backing is drawn: lighter than the clear colour
        img = PIL.open(out).convert("RGB")
        w, h = img.size
        backing, clear = img.getpixel((w - 4 - 8, h - 12)), img.getpixel((w - 2, h - 2))
        assert sum(backing) > sum(clear) + 20, (backing, clear)
    differ, lit = _compare(out, golden)
    assert lit > 0.01, "the image is (nearly) blank"
    assert differ < 0.01, f"{differ:.2%} of pixels differ from {golden.name}"


def test_reader_counts_are_what_is_drawn(view_stages: dict[str, Path]) -> None:
    # cheap guard that the stage the goldens draw is the golden run (no Strata needed)
    assert len(load_run(view_stages["echo"]).instances[0].thoughts) == 200
