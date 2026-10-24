"""``acsf-usd view`` and ``acsf-usd fetch-strata``: argument parsing and the exit codes.

Exit 2 when Strata is unavailable (missing, fails to load, too old, or no OpenGL 4.5), with one
line saying why. The export commands never reach this module.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from acsf_usd.view import PANELS


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    view = commands.add_parser(
        "view",
        help="open exported stage(s) in a 3D view drawn with Strata",
        description=(
            "Draw a stage written by `acsf-usd export` with Strata: one node per thought on a "
            "spiral in cycle order (the only position), a perception pulse from the Stimulus "
            "sphere, a ring per reflection, lit interlocutor cubes, mood gauges and the "
            "journaled text. A synthetic schematic layout, not a body or a place. Reads the "
            "stage only. Needs `acsf-usd fetch-strata` once (or ACSF_USD_STRATA_LIB)."
        ),
    )
    view.add_argument("stage", type=Path, help="stage file (.usda or .usdc; a root layer)")
    view.add_argument(
        "--png",
        type=Path,
        metavar="OUT",
        help="render headless to OUT and exit (several --cycle values write OUT-<cycle>.png)",
    )
    view.add_argument(
        "--cycle",
        type=int,
        action="append",
        metavar="N",
        help="cycle to show (default: the last); repeat with --png for several images",
    )
    view.add_argument("--size", default="1280x720", metavar="WxH", help="default 1280x720")
    view.add_argument("--instance", metavar="NAME", help="instance whose text is shown first")
    view.add_argument(
        "--panel", choices=PANELS, default="thought", help="text shown in the bottom panel"
    )
    view.add_argument(
        "--watch",
        action="store_true",
        help="reopen the stage whenever it changes (pair with `acsf-usd export --follow`)",
    )
    view.add_argument("--frames", type=int, help=argparse.SUPPRESS)  # window smoke tests

    fetch = commands.add_parser(
        "fetch-strata",
        help="download the pinned Strata library for `acsf-usd view`",
        description=(
            "Download the Strata release asset pinned in strata.lock into the per-user cache, "
            "check its SHA-256 (deleting it on a mismatch) and print its path."
        ),
    )
    fetch.add_argument("--force", action="store_true", help="download again even if cached")


def parse_size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in text.lower().split("x"))
    except ValueError:
        raise SystemExit(f"acsf-usd: error: --size must be WxH, not {text!r}") from None
    if not (16 <= w <= 8192 and 16 <= h <= 8192):
        raise SystemExit("acsf-usd: error: --size must be between 16 and 8192 on each side")
    return w, h


def run_fetch(args: argparse.Namespace) -> int:
    from acsf_usd.view.fetch import FetchError, fetch, load_lock

    try:
        lock = load_lock()
        path = fetch(lock, force=args.force)
    except FetchError as exc:
        print(f"acsf-usd: error: {exc}", file=sys.stderr)
        return 1
    print(f"Strata {lock.version}: {path} (SHA-256 verified)")
    return 0


def run_view(args: argparse.Namespace) -> int:
    from acsf_usd.view.fetch import FetchError, resolve_library
    from acsf_usd.view.model import StageError, load_run
    from acsf_usd.view.strata import Library, StrataError, StrataUnavailable

    size = parse_size(args.size)
    try:
        run = load_run(args.stage)
    except StageError as exc:
        print(f"acsf-usd: error: {exc}", file=sys.stderr)
        return 1
    try:
        lib = Library(resolve_library())
    except (FetchError, StrataUnavailable) as exc:
        print(f"acsf-usd view: Strata unavailable: {exc}", file=sys.stderr)
        return 2

    from acsf_usd.view.app import StageWatcher, render_png, run_window

    try:
        if args.png is not None:
            if args.watch:
                print("acsf-usd: error: --watch needs the window, not --png", file=sys.stderr)
                return 2
            for path in render_png(
                lib,
                run,
                args.png,
                cycles=args.cycle or [None],
                size=size,
                instance=args.instance,
                panel=args.panel,
            ):
                print(f"wrote {path}")
            return 0
        cycles = args.cycle or [None]
        run_window(
            lib,
            run,
            size=size,
            cycle=cycles[-1],
            instance=args.instance,
            watch=StageWatcher(args.stage) if args.watch else None,
            max_frames=args.frames,
        )
    except StrataUnavailable as exc:
        print(f"acsf-usd view: Strata unavailable: {exc}", file=sys.stderr)
        return 2
    except StageError as exc:
        print(f"acsf-usd: error: {exc}", file=sys.stderr)
        return 1
    except StrataError as exc:
        print(f"acsf-usd view: {exc}", file=sys.stderr)
        return 1
    return 0
