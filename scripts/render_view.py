"""Render the README media of ``acsf-usd view``: an animated GIF of a golden run's spiral filling
in cycle by cycle, and stills, all drawn headless with Strata through the viewer's own code.

Every frame is exactly what ``acsf-usd view STAGE --png OUT --cycle N`` draws (plus a slow camera
orbit for the GIF). The golden runs are exported with the project's own CLI first.

Usage (Strata from ``acsf-usd fetch-strata`` or ``ACSF_USD_STRATA_LIB``; on a machine without a
GPU, ``LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe``; imageio-ffmpeg is not a project
dependency)::

    uv run --python 3.12 --no-project --with ".[view]" --with imageio-ffmpeg \\
        python scripts/render_view.py --out docs/images

Writes ``view-echo.gif``, ``view-echo-116.png``, ``view-golden.png`` and ``view-meta.json``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "fixtures" / "golden"


def export(inputs: list[str], out: Path) -> None:
    cmd = [sys.executable, "-m", "acsf_usd", "export", *inputs, "-o", str(out)]
    subprocess.run(cmd, check=True, cwd=REPO, stdout=subprocess.DEVNULL)


def main() -> int:
    from acsf_usd.view.app import Renderer, camera_for
    from acsf_usd.view.fetch import resolve_library
    from acsf_usd.view.model import load_run
    from acsf_usd.view.scene import ViewState
    from acsf_usd.view.strata import Engine, Library

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, help="scratch dir (default: <out>/view-work)")
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--step", type=int, default=2, help="cycles per GIF frame")
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--gif-width", type=int, default=720)
    ap.add_argument("--orbit", type=float, default=0.0, help="degrees of camera orbit over the GIF")
    args = ap.parse_args()

    import imageio_ffmpeg

    out: Path = args.out.resolve()
    work: Path = (args.work or out / "view-work").resolve()
    shutil.rmtree(work, ignore_errors=True)
    (work / "frames").mkdir(parents=True)
    out.mkdir(parents=True, exist_ok=True)
    w, h = (int(v) for v in args.size.split("x"))
    timings: dict[str, float] = {}

    t = time.time()
    export([str(GOLDEN / "Echo")], work / "echo.usda")
    export([str(GOLDEN)], work / "golden" / "run.usda")
    timings["export_s"] = round(time.time() - t, 2)

    lib = Library(resolve_library())
    echo = load_run(work / "echo.usda")
    golden = load_run(work / "golden" / "run.usda")
    first, last = int(echo.start), int(echo.end)
    cycles = list(range(first, last + 1, args.step))
    if cycles[-1] != last:
        cycles.append(last)
    hold = args.fps * 3

    t = time.time()
    with Engine(lib, w, h, headless=True) as engine:
        r = Renderer(engine, echo)
        n = len(cycles) + hold
        for i in range(n):
            cycle = cycles[min(i, len(cycles) - 1)]
            yaw = args.orbit * (i / (n - 1) - 0.5)  # optional slow orbit (a drawing choice)
            r.draw(ViewState(cycle=cycle, since_change=0.25), camera_for(echo, yaw=yaw))
            engine.save_png(work / "frames" / f"{i + 1:04d}.png")
        timings["gif_frames"] = n
        timings["gif_render_s"] = round(time.time() - t, 1)
        r.draw(ViewState(cycle=116), camera_for(echo))
        engine.save_png(out / "view-echo-116.png")
        r.set_run(golden)
        r.draw(ViewState(cycle=120, instance=3), camera_for(golden))
        engine.save_png(out / "view-golden.png")
        gpu = engine.gpu_description()

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    t = time.time()
    subprocess.run(
        [
            ff, "-y", "-loglevel", "error", "-framerate", str(args.fps), "-start_number", "1",
            "-i", str(work / "frames" / "%04d.png"),
            "-vf",
            f"scale={args.gif_width}:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=128:stats_mode=full[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
            "-metadata", "comment=synthetic schematic layout - not a body or a place",
            str(out / "view-echo.gif"),
        ],
        check=True,
    )  # fmt: skip
    timings["encode_s"] = round(time.time() - t, 1)
    meta = {
        "what": "acsf-usd view (Strata) on the Echo golden run, cycles 1-200, and all four runs",
        "caption": "synthetic schematic layout - not a body or a place",
        "strata": {"version": lib.version, "abi": list(lib.abi), "gpu": gpu},
        "size": [w, h],
        "gif": {
            "fps": args.fps,
            "width": args.gif_width,
            "cycles_per_frame": args.step,
            "orbit_degrees": args.orbit,
        },
        "timings": timings,
        "sizes_bytes": {
            p: (out / p).stat().st_size
            for p in ("view-echo.gif", "view-echo-116.png", "view-golden.png")
        },
    }
    (out / "view-meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(meta["sizes_bytes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
