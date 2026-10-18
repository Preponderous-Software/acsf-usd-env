"""Command-line entry point: ``acsf-usd``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from acsf_usd import __version__
from acsf_usd.reader import InputError, read_instance, resolve_instance_dir
from acsf_usd.stage.build import ExportOptions, build_instance_stage, write_stage

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
            "Export an ACSF instance directory, experiment run directory, golden-run "
            "directory, or instance name under CONSCIOUSNESS_HOME to a USD stage. The input "
            "is only read, never modified."
        ),
    )
    export.add_argument("input", help="instance directory, run directory, or instance name")
    export.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output file (default: <name>.usda, or <name>.usdc with --binary)",
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
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
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


def run_export(args: argparse.Namespace) -> int:
    directory = resolve_instance_dir(args.input)
    run = read_instance(directory)
    output = output_path(args.output, run.name, args.binary)
    refuse_output_inside(output, [directory])
    options = ExportOptions(
        time_codes_per_second=args.time_codes_per_second, source_commit=args.source_commit
    )
    stage = build_instance_stage(run, options)
    write_stage(stage, output, binary=args.binary)
    cycles = run.journal.cycles
    span = f"cycles {cycles[0]}–{cycles[-1]}" if cycles else "no cycles"
    print(
        f"wrote {output} ({run.name}: {len(run.journal.events)} events, {span}, "
        f"{run.journal.indexing} indexing, {len(run.warnings)} warnings)"
    )
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
