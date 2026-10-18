"""Command-line entry point: ``acsf-usd``."""

from __future__ import annotations

import argparse
import logging
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from acsf_usd import __version__
from acsf_usd.model import InstanceRun
from acsf_usd.reader import InputError, read_instance, resolve_inputs
from acsf_usd.stage.build import ExportOptions, build_instance_stage, write_stage
from acsf_usd.stage.compose import export_composed

log = logging.getLogger("acsf_usd")

TEXT_EXT = ".usda"
BINARY_EXT = ".usdc"


class UsageError(Exception):
    """A combination of arguments that cannot be honoured."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acsf-usd",
        description="Export ACSF runs to OpenUSD stages (read-only against ACSF).",
    )
    parser.add_argument("--version", action="version", version=f"acsf-usd {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    export = commands.add_parser(
        "export",
        help="export an ACSF instance to a USD stage",
        description=(
            "Export ACSF instances to a USD stage. Each input is an instance directory, an "
            "experiment run or golden-run directory, an instance name under "
            "CONSCIOUSNESS_HOME, or a directory of instance directories (a whole "
            "CONSCIOUSNESS_HOME, or an experiment run with replicate-<i>/ subdirectories). One "
            "instance gives one layer; several give one layer per instance plus a root layer "
            "that sublayers them on one shared timeline. Inputs are only read, never modified."
        ),
    )
    export.add_argument(
        "inputs",
        nargs="+",
        metavar="input",
        help="instance directory, run directory, instance name, or directory of instances",
    )
    export.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "output file (default: <name>.usda for one instance, run.usda for several; "
            ".usdc with --binary). Instance layers of a multi-instance export are written "
            "next to it"
        ),
    )
    export.add_argument(
        "--binary", action="store_true", help="write binary .usdc instead of text .usda"
    )
    export.add_argument(
        "--time-codes-per-second",
        type=_positive_float,
        default=1.0,
        metavar="N",
        help="playback rate in cycles per second (default: 1)",
    )
    export.add_argument(
        "--source-commit",
        metavar="SHA",
        help="ACSF commit the run was produced with; recorded as acsf:sourceCommit",
    )
    return parser


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError("must be a finite number greater than 0")
    return value


def output_path(output: Path | None, name: str, binary: bool) -> Path:
    """Default or check the output path; the flag, not the extension, picks the format."""
    wanted = BINARY_EXT if binary else TEXT_EXT
    if output is None:
        return Path(f"{name}{wanted}")
    ext = output.suffix.lower()
    if ext in (TEXT_EXT, BINARY_EXT) and ext != wanted:
        flag = "with --binary" if binary else "without --binary"
        raise UsageError(f"{output}: a {ext} file cannot be written {flag}; use {wanted}")
    return output


def refuse_output_inside(output: Path, inputs: Sequence[Path]) -> None:
    resolved = output.resolve()
    for directory in inputs:
        if resolved.is_relative_to(directory.resolve()):
            raise UsageError(
                f"{output}: refusing to write inside the input directory {directory} "
                "(acsf-usd never writes to an ACSF instance directory)"
            )


def describe_run(run: InstanceRun) -> str:
    cycles = run.journal.cycles
    span = f"cycles {cycles[0]}–{cycles[-1]}" if cycles else "no cycles"
    return (
        f"{run.name}: {len(run.journal.events)} events, {span}, "
        f"{run.journal.indexing} indexing, {len(run.warnings)} warnings"
    )


def run_export(args: argparse.Namespace) -> int:
    directories, collection = resolve_inputs(args.inputs)
    # Collection directories themselves are inputs too: nothing may be written inside them.
    protected = directories + [Path(arg) for arg in args.inputs if Path(arg).is_dir()]
    options = ExportOptions(
        time_codes_per_second=args.time_codes_per_second, source_commit=args.source_commit
    )
    if len(directories) == 1 and not collection:
        run = read_instance(directories[0])
        output = output_path(args.output, run.name, args.binary)
        refuse_output_inside(output, protected)
        write_stage(build_instance_stage(run, options), output, binary=args.binary)
        print(f"wrote {output} ({describe_run(run)})")
        return 0

    output = output_path(args.output, "run", args.binary)
    refuse_output_inside(output, protected)
    runs = [read_instance(directory) for directory in directories]
    try:
        result = export_composed(runs, output, options, binary=args.binary)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    for layer, run in zip(result.layers, result.runs, strict=True):
        print(f"wrote {layer} ({describe_run(run)})")
    print(f"wrote {output} (root layer sublayering {len(result.layers)} instances)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    logging.basicConfig(level=logging.WARNING, format="acsf-usd: warning: %(message)s")
    try:
        if args.command == "export":
            return run_export(args)
    except UsageError as exc:
        parser.exit(2, f"acsf-usd: error: {exc}\n")
    except (InputError, OSError) as exc:
        print(f"acsf-usd: error: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command {args.command}")
    return 2
